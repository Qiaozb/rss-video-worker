# Video Worker 重构分析

> 分析日期：2026-06-30
> 修订：2026-07-02（据代码核实修正事实与方案漏洞）

## 项目现状总览

| 文件 | 行数 | 函数数 | 问题 |
|------|------|--------|------|
| `app/db.py` | **3499** | 98 | 全量数据访问集中在一文件 |
| `app/main.py` | **2454** | 107 | God Object：路由 + 业务 + 状态 + 线程 |
| `web/app.js` | **1769** | — | 主控制器仍过大，已有部分 ES module 拆分 |
| `app/llm.py` | 457 | — | 相对合理 |
| `app/rss.py` | 328 | — | 相对合理 |
| 测试文件 | **0** | — | 完全没有测试 |

> 注：`app/` 下**已有 8 个半抽出的模块**（见下文 §2），文档原版将其忽略，误把"业务逻辑全堆在 main.py"当作起点。本版已修正。

**部署形态核实：** `Dockerfile` 为单进程 `uvicorn app.main:app`（无 gunicorn workers）。当前内存态并发模型（线程 + 全局 set）在单进程下可工作；但 `get_connection()`（`app/db.py:87`）每次新建连接、**无连接池、不复用**，SSE 轮询循环频繁查库下存在连接耗尽风险。多进程部署时全局 set 也会失效，需迁移到 DB 租约/消息队列。

---

## 六大重构方向

### 1. 拆分 `main.py` — 引入 APIRouter 分层

**当前问题：** `main.py` 承载了路由定义、请求模型、业务逻辑、全局状态管理、线程调度、SSE 流等所有职责，2454 行，无法维护。

**重构方案：**

```
app/
├── main.py                 # 只做 FastAPI 实例创建、中间件注册、路由挂载、lifespan
├── routers/
│   ├── auth.py             # /auth/* 路由（复用 app/auth.py 现有逻辑）
│   ├── rss.py              # /rss/* 路由
│   ├── pipeline.py         # /pipeline/*, /runs/* 路由
│   ├── reports.py          # /reports/* 路由
│   ├── render.py           # /render/*, /jobs/*, /videos/* 路由
│   ├── schedules.py        # /schedules/* 路由
│   ├── model_configs.py    # /model-configs/* 路由
│   ├── prompts.py          # /prompt-versions/* 路由
│   ├── maintenance.py      # /maintenance/* 路由（复用 app/maintenance.py）
│   └── video_assets.py     # /video-assets/* 路由（复用 app/video_assets.py）
├── models/                 # Pydantic 请求/响应模型
│   ├── auth.py
│   ├── pipeline.py
│   └── ...
└── ...（已有的 auth/tts/pipeline/maintenance/remotion/scheduler/video_assets 模块保留，详见 §2）
```

**SSE 端点归属（显式安排）：** 两个带状态的 SSE 生成器需随路由迁移——
- `pipeline_run_event_stream`（`app/main.py:1873`）→ `routers/pipeline.py`
- `video_job_event_stream`（`app/main.py:2397`）→ `routers/render.py`

迁移时注意生成器闭包捕获的状态（`_is_cancelled` 等应改为调用 manager 方法），以及循环内查库逻辑应走 repo 层。

**生命周期迁移：** 顺带把 `@app.on_event("startup"/"shutdown")`（`app/main.py:1266、1315`，已废弃写法）迁移到 FastAPI `lifespan` context manager，统一连接初始化、建表、线程启动与 `_shutdown_event` 触发。

**依赖注入约束：** Router 拆分后不得反向 import `main.py` 中的全局对象。`main.py` 负责创建 `RenderManager`、`PipelineManager`、`SchedulerManager` 等运行态对象，并通过 `app.state` 或 `app/dependencies.py` 提供给 routers。否则路由拆分会引入循环 import，等于把 God Object 从文件层面拆开、在依赖层面重新耦合。

**收益：** 每个路由文件 100-200 行，职责清晰，可独立阅读和修改。

---

### 2. 引入 Service 层 — 在已有模块上归位，而非新建平行文件

**当前问题：** `render_report_job`、`_execute_pipeline_task`、`_scheduler_loop`、`generate_tts_item_audio` 等核心业务逻辑直接写在 `main.py` 里，与 HTTP 层深度耦合，无法在 HTTP 之外复用，更无法测试。

**关键澄清：** 项目**已经抽出了 8 个模块**，文档原版忽略它们导致方案会新建一批 `*_service.py` 与现有模块并存，造成混乱。`main.py` 已在 import 它们（`app/main.py:117-124`）。正确的做法是**把 main.py 里残留的业务函数归位到这些已有模块**：

| 已有模块 | 行数 | 现状 | 残留在 main.py 的逻辑（需归位） |
|---|---|---|---|
| `app/tts.py` | 188 | 有 `TTSClient`、`normalize_tts_text` | `generate_tts_item_audio`（`main.py:362`）、`generate_tts_item`（`main.py:2112`） |
| `app/pipeline.py` | 138 | 有 `analyze_recent_rss_items`、`report_title_fallback` | pipeline 分析/报告标题等纯领域逻辑；任务执行、取消、恢复应进 `PipelineManager` |
| `app/scheduler.py` | 111 | 有 `ScheduleConfig`、`next_run_after` | `_scheduler_loop`（`main.py:1236`） |
| `app/remotion.py` | 153 | 有 `render_video`、`cancel_render` | 保持为 Remotion 进程适配器；`render_report_job`、`_render_worker_loop` 应进 `RenderManager` |
| `app/maintenance.py` | 270 | 有 `cleanup_*`、`maintenance_summary` | maintenance 路由的业务编排 |
| `app/auth.py` | 212 | 有 `hash_password` 等 | auth 路由业务 |
| `app/video_assets.py` | 124 | 有 `file_info`、`generate_cover_image` | video-assets 路由业务 |
| `app/progress.py` | 31 | 有 `ProgressThrottler` | — |

**重构方案：**

```
app/
├── tts.py              # ← 归入 generate_tts_item_audio（不新建 tts_service.py）
├── pipeline.py         # ← 保留分析、标题、提示词输入构造等纯领域逻辑
├── scheduler.py        # ← 归入 _scheduler_loop（运行态逻辑，配置类已在此）
├── remotion.py         # ← 保留 render_video、cancel_render 等底层 Remotion 调用
├── render_engines/
│   ├── base.py              # RenderEngine 协议：render(props, output_path) / cancel(job_id)
│   ├── remotion_engine.py   # 默认主引擎，封装现有 Remotion + FFmpeg 渲染
│   └── playwright_engine.py # 后期备用引擎：Playwright 截图序列 + FFmpeg 合成
├── maintenance.py      # ← 归入 maintenance 业务编排
├── services/
│   ├── render_manager.py   # 渲染队列/状态封装；归入 render_report_job、_render_worker_loop；按配置选择 RenderEngine
│   ├── pipeline_manager.py # pipeline 执行、恢复、并发、取消状态封装
│   └── report_service.py   # 报告查询与发布编排（无对应已有模块，需新建）
└── ...
```

> 命名约定：已有领域模块（tts/pipeline/...）继续承载该领域的"做什么"；`services/` 下的 manager 类只承载"跨请求的运行态与并发状态"（队列、锁、取消集合）。避免 `tts.py` 与 `tts_service.py` 并存。

**关键原则：** Router 只做参数解析和 HTTP 响应封装；领域模块（tts/pipeline/...）处理业务；DB 层处理持久化。三层单向依赖。

**事务边界（service 层的明确产出）：** 现状 `get_connection()` 默认 `autocommit=True`，`main.py` 内 `autocommit=False`/`.commit()` 命中 0 次；但项目并非完全没有事务，`db.py` 的 `normalize_raw_report()` 已经使用显式事务。问题应准确表述为：`main.py` 中多步写操作（创建 pipeline_run + 写 events、视频 job 状态流转、任务恢复/重试）缺少统一事务边界，中途失败会留脏状态。service 层重构应顺势引入事务/Unit-of-Work 边界，把这列为 service 层的验收项，而非含糊带过。

---

### 3. 拆分 `db.py` — 按领域分 Repository

**当前问题：** `db.py` 3499 行、98 个函数，涵盖 auth、RSS、pipeline、video job、schedule、model config、prompt、audit log、LLM call log 等所有领域。任何修改都需要在巨文件中定位。

**重构方案：**

```
app/
├── db/
│   ├── __init__.py             # 连接管理、通用工具（get_connection 等）
│   ├── auth_repo.py            # auth_users、audit_logs 相关
│   ├── rss_repo.py             # rss_sources、rss_items 相关
│   ├── pipeline_repo.py        # pipeline_runs、pipeline_events 相关
│   ├── report_repo.py          # rss_llm_report、rss_llm_key_news、rss_news_dedupe 相关
│   ├── video_job_repo.py       # rss_video_job 相关
│   ├── schedule_repo.py        # schedule_configs 相关
│   ├── model_config_repo.py    # model_configs 相关
│   ├── prompt_repo.py          # prompt_versions 相关
│   ├── llm_log_repo.py         # llm_call_logs 相关
│   └── migrations.py           # ensure_* 建表函数集中管理
```

**迁移约束：** 当前已经存在 `app/db.py`，不能直接同时新增 `app/db/` 包并继续使用 `from app import db` / `from app.db import ...`。两种可执行路径：

1. **低风险路径（推荐）：** 先创建 `app/repositories/`，把领域 repo 逐步迁入；保留 `app/db.py` 作为兼容门面，逐步减少导出符号。
2. **一次性路径：** 在一个提交中把 `app/db.py` 移为 `app/db/__init__.py`，同步更新所有 import，并确保测试覆盖。

不建议在未处理 import 冲突的情况下直接创建 `app/db/` 目录。

**事务约束：** Repo 拆分后，多 repo 的原子操作必须共享同一个连接或 Unit of Work。repo 函数应支持传入 `conn`，或由 `UnitOfWork` 统一创建 connection/commit/rollback；否则每个 repo 自己开连接，service 层仍然无法形成事务。

**收益：** 每个 repo 200-400 行，按领域内聚。`main.py` 从 `app.db` 导入的 115 个符号也会随之清晰化。

---

### 4. 全局状态封装 — 从散落的 Lock/Queue/Set 到管理器类

**当前问题：** `main.py` 里散落着大量全局可变状态：

```python
_render_queue: Queue
_render_state_lock: Lock
_scheduled_reports: set
_cancelled_reports: set
_shutdown_event: Event
_pipeline_lock: Lock
_pipeline_cancel_lock: Lock
_cancelled_pipeline_runs: set
_maintenance_lock: Lock
```

这些状态通过散落的 `_is_cancelled`、`_raise_if_cancelled`、`_request_pipeline_cancel` 等私有函数操作，极易引入并发 bug。

**重构方案：**

```python
# app/services/render_manager.py
class RenderManager:
    def __init__(self):
        self._queue: Queue
        self._lock: Lock
        self._scheduled: set
        self._cancelled: set

    def schedule(self, report_id: int) -> tuple[int, bool]: ...
    def cancel(self, report_id: int) -> None: ...
    def is_cancelled(self, report_id: int) -> bool: ...
    def worker_loop(self) -> None: ...

# app/services/pipeline_manager.py
class PipelineManager:
    def __init__(self):
        self._lock: Lock
        self._cancel_lock: Lock
        self._cancelled: set

    def start(self, ...) -> int: ...
    def cancel(self, run_id: int) -> None: ...
    def recover(self, rows: list) -> None: ...
```

**收益：** 状态与操作封装在一个类里，可以独立测试，生命周期可控。

**⚠️ 必须修复的语义漏洞 — 取消请求未持久化：** `_cancelled_reports` / `_cancelled_pipeline_runs` 是纯内存 set（`app/main.py:154、160`）。DB 已能记录最终的 `cancelled` 状态，但"取消已请求、任务尚未退出"这个中间意图仍然只在内存里。启动恢复逻辑 `recover_unfinished_video_jobs()` 按 DB 里的 running 状态重新入队（`app/main.py:1289`）：

> 进程重启后，一个"已被用户取消但 DB 里仍 running"的 job 会被 recovery 重新入队执行——取消意图丢失。

把这套状态搬进 `RenderManager` 类**只是换了容器，没有修这个 bug**。封装的真正产出必须是：取消请求落库（例如新增 `cancel_requested_at` / `cancel_requested_by`，或独立取消表），类内 set 仅作内存缓存/加速。否则封装是假修复。

---

### 5. 补充测试 — 从零开始建立测试基础设施

**当前问题：** 完全没有测试文件。对于有权限系统、并发渲染队列、pipeline 重试/恢复逻辑的项目来说，这是高风险的。

**前置步骤（P-1）：重构前先写最小 characterization 测试。** 现状零测试，第一次拆 main.py 本身就是无保护网的盲操作。重构启动前应先用 FastAPI `TestClient` 打现有端点写一批黑盒冒烟/回归测试，锁定当前行为，再动手拆分。

**P-1 最小测试集：**

1. 登录 / 获取当前用户 / 退出登录
2. RSS 源新增、修改、删除、列表查询
3. 模型配置新增、测试、修改、删除
4. 提示词新增、修改、删除、列表查询
5. 手动触发 pipeline，验证 run 创建、事件写入、状态流转
6. 渲染入队、取消、失败状态读取（Remotion/TTS 使用 mock）
7. 报告列表与报告详情读取

**重构优先级：**

1. **Service 层单元测试**（需要先把 service 层抽出来）：pipeline 执行逻辑、取消逻辑、重试逻辑
2. **DB 层集成测试**：优先使用共享 test MySQL 实例；需要隔离性时再上 testcontainers-MySQL。**禁用 SQLite**——`db.py` 全是 pymysql + MySQL 方言 DDL（`AUTO_INCREMENT`、MySQL 类型），`get_connection()` 直连 pymysql，SQLite 跑不起来，除非重写所有 SQL。
3. **Router 层 API 测试**：使用 FastAPI `TestClient` 验证请求/响应
4. **并发场景测试**：渲染取消、pipeline 重入防护、僵尸任务回收；重点覆盖 §4 的取消持久化漏洞（取消后重启进程，验证 job 不被重新执行）

```
tests/
├── conftest.py              # fixtures: testcontainers-MySQL, test client, mock TTS
├── unit/
│   ├── test_pipeline_service.py
│   ├── test_render_manager.py
│   └── test_auth.py
├── integration/
│   ├── test_rss_repo.py
│   └── test_pipeline_repo.py
└── api/
    ├── test_auth_routes.py
    └── test_pipeline_routes.py
```

---

### 6. 数据库访问层升级 — 连接池先行，ORM 远期

**当前问题：** `db.py` 中大量 raw SQL 字符串拼接：

- 返回值全是 `Dict[str, Any]`，没有类型安全
- 建表逻辑（`ensure_*`）和 CRUD 混在一起
- `get_connection()`（`app/db.py:87`）每次 `pymysql.connect()` 新建连接，**无连接池、无复用**；SSE 轮询循环频繁查库下存在连接耗尽风险
- 事务管理不完整：`autocommit=True` 默认开启，`normalize_raw_report()` 已有显式事务，但 `main.py` 中多步写操作仍缺少统一事务边界（详见 §2 事务边界）

**两个方向（拆成两步，不必一次上 ORM）：**

| 步骤 | 内容 | 优点 | 缺点 |
|------|------|------|------|
| **P2.5 连接池**（独立小步） | 引入 DBUtils / SQLAlchemy Pool，仅替换 `get_connection` 的连接来源，SQL 不动 | 投入小、立即解决连接耗尽、为后续 ORM 铺路 | 仍是字符串 SQL |
| **P3 ORM/Query Builder** | SQLAlchemy Core，dataclass 返回值替代 `Dict[str, Any]` | 类型安全、可测试、迁移工具成熟 | 学习成本、改造量大 |

**渐进策略：** 先 P2.5 引连接池（小投入、立即收益，**不应和完整 ORM 一起被压到 P3**）；再保持 pymysql 但引入 dataclass 返回值；最后远期迁移到 SQLAlchemy Core。

---

## 建议的重构优先级

| 优先级 | 方向 | 理由 |
|--------|------|------|
| **P-1** | 重构前先写 characterization / 冒烟测试 | 现状零测试，拆 main.py 是盲操作；先锁行为再动手 |
| **P0** | 拆分 main.py（APIRouter + lifespan + 依赖注入） | 投入最小、收益最大、可渐进执行 |
| **P1** | 抽 Service 层（归位到已有模块 + 引入事务边界） | 解除业务逻辑与 HTTP 的耦合，补齐 main.py 多步写操作的事务边界 |
| **P1** | 封装全局状态（含取消状态持久化） | 降低并发 bug 风险；必须落库否则假修复 |
| **P2** | DB 层集成测试（共享 test MySQL 或 testcontainers-MySQL） | 拆 db.py 的前置安全网；禁止 SQLite |
| **P2.5** | 引入连接池 | 投入小、立即解决 SSE 连接耗尽，不应被压到 P3 |
| **P2+** | 拆分 db.py（按领域 Repository） | 文件大，但"功能正确"未经验证（零测试 + 无事务），必须卡在 DB 集成测试之后 |
| **P3** | 双渲染引擎抽象 | 保留 Remotion 主路径，同时为 Playwright + FFmpeg 兜底渲染预留接口，降低单一渲染链路失败风险 |
| **P3** | ORM/Query Builder | 投入大、收益长期 |

---

## 语言更换分析

### 当前语言栈

| 层 | 语言 | 行数 | 绑定的生态 |
|---|---|---|---|
| 后端 API + 业务逻辑 | Python (FastAPI) | ~6500 行 | pymysql、requests、defusedxml |
| 视频渲染 | Node.js (Remotion + React) | 148 行 | @remotion/bundler、@remotion/renderer |
| 前端控制台 | Vanilla JS + esbuild | ~2000 行 | 无框架依赖 |

### 后端 Python → 其他语言

**技术上完全可行，但投入产出比需要评估：**

| 目标语言 | 优势 | 劣势 |
|----------|------|------|
| **Go** | 天然并发、部署为单二进制、性能高 | 生态没有直接对标 FastAPI 的框架，ORM 弱，开发速度慢 |
| **TypeScript (Node.js)** | 与 Remotion 统一语言栈，全栈 TS | 单线程，CPU 密集型任务（TTS 调度、并发渲染管理）需要额外设计 |
| **Rust** | 极致性能和内存安全 | 开发周期长 3-5 倍，团队学习成本极高，不适合业务频繁迭代 |

**当前阶段不建议换后端语言。** 原因：

1. **业务还在快速迭代** — 控制台功能还在持续增加（权限、TTS 管理、模型日志等都是最近几天加的），此时换语言等于重写
2. **Python 在这里没有性能瓶颈** — 瓶颈在 Remotion 渲染（Node.js）和 TTS 合成（外部服务），Python 只是做调度和胶水
3. **6500 行代码量不算大** — 结构问题比语言问题更紧迫，先重构结构，换语言的选项反而会变得更清晰

### 视频渲染 Node.js → 其他

**不建议直接替换 Remotion，但建议后期重构为双渲染引擎。** Remotion 绑定 React + Node.js 生态，当前模板、动画和画面结构都依赖它，直接替换等于重写整个视频生成管线。更稳妥的做法是保留 Remotion 作为主引擎，同时新增 Playwright + FFmpeg 作为备用引擎。

#### 后期目标：双渲染引擎

```
RenderManager
  -> RenderEngine(remotion)
      -> Remotion renderMedia
      -> FFmpeg / Remotion compositor 输出 MP4

RenderManager
  -> RenderEngine(playwright_ffmpeg)
      -> Playwright 打开同一套 HTML/React 画面
      -> 按时间轴截图生成 frame_000001.png ...
      -> FFmpeg 图片序列 + TTS 音频合成 MP4
```

#### 引擎职责边界

| 层 | 职责 |
|---|---|
| `RenderManager` | 领取任务、生成 props、生成 TTS、写进度、处理取消和失败重试 |
| `RenderEngine` | 只负责把 props + 音频资源渲染成一个视频文件 |
| `RemotionEngine` | 当前默认路径，复用现有 Remotion 模板和 bundle 缓存 |
| `PlaywrightFfmpegEngine` | 备用路径，用浏览器截图序列规避 Remotion renderMedia/Chromium 端口链路不稳定问题 |

#### 配置建议

新增配置项：

```env
VIDEO_RENDER_ENGINE=remotion
VIDEO_RENDER_FALLBACK_ENGINE=playwright_ffmpeg
VIDEO_RENDER_ENABLE_FALLBACK=1
```

运行策略：

1. 默认走 `remotion`。
2. Remotion 出现可重试错误时，先走一次 safe 模式：低并发、禁用硬件加速、重新 bundle。
3. safe 模式仍失败时，如果 `VIDEO_RENDER_ENABLE_FALLBACK=1`，自动切到 `playwright_ffmpeg`。
4. 两个引擎都失败时，任务才最终标记 `failed`。

#### Playwright + FFmpeg 备用引擎的实现步骤

1. 新增独立预览页面或复用 Remotion 组件导出的静态页面，确保同一份 props 可驱动画面。
2. 按视频时间轴计算每一帧的场景状态，Playwright 每帧截图到临时目录。
3. 使用 FFmpeg 将图片序列编码为无声视频。
4. 使用 FFmpeg 拼接/混合 TTS 音频，生成最终 MP4。
5. 接入同一套进度回调：截图阶段 80%-95%，编码阶段 95%-100%。
6. 对比 Remotion 输出与备用输出：字幕、画面、音频时长、分辨率和最终文件路径保持一致。

#### 验收标准

- 同一个 `props.json` 可被 `remotion` 和 `playwright_ffmpeg` 两个引擎消费。
- 前端视频管理页面可看到使用的渲染引擎和失败原因。
- Remotion 故障时，备用引擎能生成可播放 MP4。
- 备用引擎输出的视频分辨率、音频时长、字幕内容与主引擎保持一致。
- 取消任务时能同时取消 Remotion、Playwright、Chrome 和 FFmpeg 子进程。

### 前端 Vanilla JS → React / Vue

**这个可以考虑，且投入相对可控：**

- 当前 `web/app.js` 1769 行，手工管理 DOM 和状态，已经接近 vanilla JS 的维护极限
- 项目已经有 esbuild + JSX 构建管线（`glass-island.jsx`），迁移到 React 的基础设施已就绪
- 但如果近期不打算大改前端交互，维持现状也完全可以

### 换语言的合适时机

```
现在 → 重构结构（P0/P1） → 补测试 → 业务稳定
                                        ↓
                              评估是否需要换语言
```

当以下条件满足时，换后端语言才值得考虑：

1. **部署规模扩大** — 需要多进程/多机部署，Python GIL 成为真实瓶颈
2. **团队语言栈统一需求** — 比如团队全面转向 Go 或 TypeScript
3. **性能需求变化** — 比如并发 pipeline 从 1 个增长到几十个

**核心建议：** 先把架构分层做好。分层清晰之后，即便将来要换语言，也是逐层替换（比如先把 API 层用 Go 重写，Service 层保持 Python 通过 gRPC 通信），而不是一次性重写。

---

## 额外观察

- `web/app.js`（1769 行）也值得模块化拆分，但优先级低于后端。当前 esbuild 已配置好，可以渐进地将 `app.js` 拆分为 ES modules 并利用已有的构建管线。
- `app/main.py` 从 `app.db` 导入 115 个符号，是 `db.py` 拆分紧迫性的直接证据。
- 渲染队列和 pipeline 执行使用原生 `threading.Thread`，未来如需多进程部署，需要迁移到数据库租约或消息队列。
- `@app.on_event("startup"/"shutdown")` 为已废弃写法，§1 拆分时应顺带迁移到 `lifespan` context manager。
