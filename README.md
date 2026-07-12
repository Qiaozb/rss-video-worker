# 游戏行业日报 Video Worker

读取 MySQL 中的游戏新闻和口播稿，调用本机 TTS 服务生成音频，再通过 Remotion 和 FFmpeg 输出 MP4。当前也提供程序化 RSS 采集入口，用于逐步替换 Dify。

默认采用 macOS 本机运行，不需要 Docker。

## 运行依赖

- Python 3.11
- Node.js 20 或更高版本
- FFmpeg 和 FFprobe
- Google Chrome、Chromium 或 Microsoft Edge
- MySQL
- 本机 TTS 服务，默认地址 `http://127.0.0.1:9880`

## 首次安装

```bash
cd /Users/qzzzzb_h/projects/codex/dify-rss/video-worker
chmod +x scripts/*.sh
./scripts/setup-local.sh
```

安装脚本会创建 `.venv`、安装 Python 和 Node 依赖，并根据 `.env.example` 创建 `.env`。

如果已有旧环境，新增依赖后可以单独更新 Python 依赖：

```bash
cd /Users/qzzzzb_h/projects/codex/dify-rss/video-worker
.venv/bin/pip install -r requirements.txt
```

打开 `.env`，填写正确的数据库密码：

```env
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD="你的数据库密码"
MYSQL_DATABASE=dify_test

APP_SECRET_KEY="请生成并固定一个长随机字符串，云端部署必须配置"

TTS_BASE_URL=http://127.0.0.1:9880
TTS_VOICE=Serena

LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY="你的模型 API Key"
LLM_MODEL=deepseek-chat
LLM_TIMEOUT_SECONDS=180
LLM_TEMPERATURE=0.2

REMOTION_CONCURRENCY=2
REMOTION_HARDWARE_ACCELERATION=disable

# 渲染超时与僵尸任务回收（可选，以下为默认值）
RENDER_TIMEOUT_SECONDS=1800
VIDEO_STALE_SECONDS=900
PIPELINE_STALE_SECONDS=900
# 维护清理：DB 日志保留天数（0 表示不清理）
AUDIT_LOG_RETENTION_DAYS=90
LLM_CALL_LOG_RETENTION_DAYS=60
```

密码包含 `#` 时必须保留双引号。

## 数据库迁移

项目已经加入 Alembic baseline migration。详细说明见：

```text
/Users/qzzzzb_h/projects/codex/dify-rss/docs/database-migrations.md
```

如果你已经手工创建过业务表，第一次只执行版本标记：

```bash
cd /Users/qzzzzb_h/projects/codex/dify-rss/video-worker
.venv/bin/alembic stamp head
```

如果是全新空库，执行：

```bash
cd /Users/qzzzzb_h/projects/codex/dify-rss/video-worker
.venv/bin/alembic upgrade head
```

升级到最新版本时必须执行迁移，否则会缺少 `cancelled` 视频状态、用户 `session_version` 字段，以及 Pipeline 恢复去重所需的 `rss_llm_raw.pipeline_run_id` 字段：

```bash
cd /Users/qzzzzb_h/projects/codex/dify-rss/video-worker
.venv/bin/alembic upgrade head
```

迁移 `0005` 会把 `rss_video_job.status` 扩展为含 `cancelled`，并给 `auth_users` 增加 `session_version`。即使不跑迁移，服务启动时 `ensure_*` 建表逻辑也会幂等地补齐这两项，但历史「任务由用户取消」的 `failed` 记录只有迁移里的 `UPDATE` 才会归一为 `cancelled`，建议正式升级走迁移。

迁移 `0006` 会给 `rss_llm_raw` 增加 `pipeline_run_id` 唯一索引，用于服务重启后复用已生成的 LLM 原始结果和标准化报告，避免同一个 Pipeline 因恢复重跑而重复调用模型、重复生成 report。

`APP_SECRET_KEY` 用于加密模型 API Key，并参与会话 token 签名。云端部署前必须固定配置；如果修改这个值，历史保存的 API Key 将无法解密，且所有已登录会话会立即失效。

模型 API Key 会优先使用 `cryptography.Fernet` 加密保存。开发环境未安装 `cryptography` 时会使用兼容编码兜底，但正式部署必须安装 `requirements.txt` 中的依赖。

管理后台登录由 `ADMIN_PASSWORD` 控制。本地开发可以留空，留空时控制台不启用登录；云端部署必须设置强密码：

```env
ADMIN_USERNAME=admin
ADMIN_PASSWORD="请填写强密码"
ADMIN_SESSION_HOURS=12
ADMIN_COOKIE_SECURE=true
```

`ADMIN_COOKIE_SECURE=true` 只适用于 HTTPS 访问；本地 `http://127.0.0.1:8000` 调试时保持 `false`。

`REMOTION_CONCURRENCY=2` 使用 2 路并发渲染帧，这是当前本机验证过更稳定的默认值。`REMOTION_HARDWARE_ACCELERATION=disable` 默认使用软件编码，避免 macOS/Chromium/Remotion 组合下的偶发浏览器崩溃；需要提速时可以临时改为 `if-possible` 并逐步测试并发 3。

## 启动

先确认 MySQL 和 TTS 服务已经启动：

```bash
curl http://127.0.0.1:9880/health
```

通过 macOS `launchd` 启动 video-worker：

```bash
cd /Users/qzzzzb_h/projects/codex/dify-rss/video-worker
./scripts/start-local.sh
```

首次启动会把服务配置安装到 `~/Library/LaunchAgents/`。服务会在登录后自动启动，并在异常退出时自动恢复。

查看状态：

```bash
./scripts/status-local.sh
```

查看日志：

```bash
tail -f logs/video-worker.log logs/video-worker-error.log
```

停止服务：

```bash
./scripts/stop-local.sh
```

## Web 控制台

服务启动后打开：

```text
http://127.0.0.1:8000/console/
```

控制台是静态 ES Modules 前端，无需独立构建：

```text
web/index.html          页面结构
web/styles.css          控制台样式
web/app.js              应用启动、数据加载、事件绑定、SSE 实时流
web/src/state.js        全局前端状态
web/src/api.js          API 客户端和 401 登录回调
web/src/utils.js        DOM、格式化和转义工具
web/src/renderers.js    表格、卡片、报告详情等渲染函数
```

控制台支持 hash 路由，例如 `/console/#reports`，刷新页面后会保留当前模块。

当前控制台提供：

- 概览：查看 RSS 源、定时计划和最近任务数量。
- RSS 源：新增 RSS 源、刷新列表、手动采集单个源。
- 模型配置：保存 OpenAI-compatible 模型地址、模型名和 API Key，支持测试连接。
- 提示词：维护系统提示词和用户提示词模板，用户提示词模板必须包含 `{markdown}` 占位符；设为默认后，下一次 RSS 分析会自动使用。
- 定时计划：查看计划、保存计划、立即执行、禁用计划。
- 报告管理：查看报告列表、新闻明细、摘要、口播稿、视频状态，支持编辑新闻、调整排序、调整重要性、渲染、发布确认和下载视频。
- 成品资产管理：筛选视频任务，查看成品文件大小、路径、封面状态，支持下载、生成封面、删除指定报告的视频/音频产物和取消渲染任务。
- 任务中心：查看 pipeline 任务进度、Worker、心跳和任务事件，按报告触发渲染或发布确认。
- 模型日志：查看最近 LLM 调用状态、耗时、token 用量和 JSON 修复次数。
- 维护：查看数据库/TTS 健康状态、磁盘空间、输出/音频/日志/缓存目录占用，并支持预览或执行旧文件清理。
- 权限：管理本地用户、角色、启用状态、密码重置和审计日志。

如果 `.env` 配置了 `ADMIN_PASSWORD` 或 `AUTH_REQUIRED=true`，控制台会先显示登录面板，登录后才会加载业务数据。未配置时保持开放模式，方便本地调试。

权限系统提供三个角色：

- `admin`：管理全部功能，包括用户、模型配置、维护清理和审计日志。
- `editor`：可管理 RSS、提示词、定时计划、报告、任务、TTS 和视频产物；不能管理用户、模型密钥和维护清理。
- `viewer`：只读查看和下载。

首次启用 `ADMIN_PASSWORD` 后，服务启动时会把 `.env` 中的 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 同步为数据库里的 `admin` 用户；后续修改 `.env` 密码并重启服务，也会同步更新该管理员密码。密码使用 PBKDF2 哈希保存；API Key 仍使用原有加密方式保存。

根路径 `http://127.0.0.1:8000/` 会自动跳转到控制台。

权限相关接口：

```bash
# 查看当前登录用户
curl -s http://127.0.0.1:8000/auth/me

# 管理用户，需 admin
curl -s http://127.0.0.1:8000/auth/users
curl -X POST http://127.0.0.1:8000/auth/users \
  -H "Content-Type: application/json" \
  -d '{"username":"editor","password":"change-me-123","role":"editor","enabled":true}'

# 查看审计日志，需 admin
curl -s "http://127.0.0.1:8000/auth/audit-logs?limit=100"
```

## 生成视频

生成最新报告：

```bash
curl -X POST http://127.0.0.1:8000/render/latest
```

生成指定报告：

```bash
curl -X POST http://127.0.0.1:8000/render/report/1
```

接口会立即返回 `job_id`。查询进度：

```bash
curl -s http://127.0.0.1:8000/jobs/1
```

订阅视频任务实时进度：

```bash
curl -N http://127.0.0.1:8000/jobs/1/stream
```

查看最近视频任务：

```bash
curl -s http://127.0.0.1:8000/video-jobs
```

取消排队中或正在渲染的任务：

```bash
curl -X POST http://127.0.0.1:8000/jobs/1/cancel
```

video-worker 一次只执行一个渲染任务，其他任务按提交顺序排队。取消正在渲染的任务时，会同时回收对应的 Node、Chrome 和 FFmpeg 进程组。

服务重启后，状态为 `pending` 或 `rendering` 的视频任务会重新进入队列，并从头生成 TTS 和视频。

macOS 没有 `watch` 时可以使用：

```bash
while true; do clear; curl -s http://127.0.0.1:8000/jobs/1; sleep 2; done
```

视频输出位置：

```text
output/report_<report_id>/final.mp4
```

TTS 音频缓存位置：

```text
output/report_<report_id>/audio/*.wav
```

## RSS 采集

新增或更新 RSS 源：

```bash
curl -X POST http://127.0.0.1:8000/rss/sources \
  -H "Content-Type: application/json" \
  -d '{
    "name": "IT之家",
    "url": "https://www.ithome.com/rss/",
    "category": "科技综合",
    "priority": 100,
    "enabled": true,
    "request_timeout_seconds": 20
  }'
```

查看 RSS 源：

```bash
curl -s http://127.0.0.1:8000/rss/sources
```

测试 RSS 源连接和解析：

```bash
curl -X POST http://127.0.0.1:8000/rss/sources/1/test
```

预览 RSS 源前 8 条内容，不写入数据库：

```bash
curl -s "http://127.0.0.1:8000/rss/sources/1/preview?limit=8"
```

删除 RSS 源采用禁用式删除，历史新闻不会删除：

```bash
curl -X DELETE http://127.0.0.1:8000/rss/sources/1
```

采集全部已启用 RSS 源，并写入 `rss_items`：

```bash
curl -X POST http://127.0.0.1:8000/rss/collect
```

采集单个 RSS 源：

```bash
curl -X POST http://127.0.0.1:8000/rss/collect/1
```

采集结果会写入：

```text
rss_sources.last_success_at / last_error_at / consecutive_failures
rss_items.title / link / normalized_link / pubdate / description / content_hash / raw_json
```

`rss_items.content_hash` 会基于规范化链接生成；链接为空时使用标题和发布时间生成。重复采集不会重复插入，只会更新 `last_seen_at`。

## 程序化分析

把最近 24 小时的 `rss_items` 交给大模型筛选，写入 `rss_llm_raw`，并同步生成 `rss_llm_report`、`rss_llm_key_news` 和 `rss_llm_tts_queue`：

```bash
curl -X POST http://127.0.0.1:8000/pipeline/analyze \
  -H "Content-Type: application/json" \
  -d '{"hours":24,"limit":120}'
```

一键先采集全部启用 RSS 源，再分析最近 24 小时：

```bash
curl -X POST http://127.0.0.1:8000/pipeline/collect-and-analyze \
  -H "Content-Type: application/json" \
  -d '{"hours":24,"limit":120}'
```

接口会立即返回 `run_id`。查询任务进度：

模型输出会先做固定 JSON 结构校验。若输出不是严格 JSON、字段缺失或 `importance` 不在“高/中/低”中，系统会调用同一个模型进行 JSON 修复重试；修复后仍不合格时，本次 Pipeline 会失败，不会写入 `rss_llm_raw` 和后续标准化表。

标准化入库使用显式事务：`rss_llm_report`、`rss_llm_key_news`、`rss_news_dedupe`、`rss_llm_tts_queue` 会一起提交或一起回滚。`rss_llm_raw` 会保留原始模型输出，方便排查问题。

```bash
curl -s http://127.0.0.1:8000/runs/<run_id>
```

查看任务事件：

```bash
curl -s http://127.0.0.1:8000/runs/<run_id>/events
```

查看最近 LLM 调用明细：

```bash
curl -s http://127.0.0.1:8000/llm-call-logs
```

查看某次 Pipeline 的 LLM 调用明细：

```bash
curl -s "http://127.0.0.1:8000/llm-call-logs?pipeline_run_id=<run_id>"
```

查看 LLM 聚合统计：

```bash
curl -s "http://127.0.0.1:8000/llm-call-stats?days=7"
```

控制台也提供“模型统计与调用日志”页，展示总调用、成功率、平均耗时、Token 总量，以及按天、模型、用途聚合的统计表。在任务中心点击某条任务的“日志”，会自动跳转并按该 Pipeline ID 筛选明细。

订阅任务实时事件流：

```bash
curl -N http://127.0.0.1:8000/runs/<run_id>/stream
```

Web 控制台的任务中心会在选择某个任务后自动使用 SSE 实时刷新；如果浏览器或网络断开，会回退到 5 秒轮询。

取消排队中或正在运行的 Pipeline 任务：

```bash
curl -X POST http://127.0.0.1:8000/runs/<run_id>/cancel
```

重试已经结束的 Pipeline 任务：

```bash
curl -X POST http://127.0.0.1:8000/runs/<run_id>/retry
```

Pipeline 取消是协作式取消：排队中、未开始、阶段切换处会很快取消；如果正在等待 RSS 或 LLM 请求返回，会在当前请求结束后落为 `cancelled`。重试会按原任务类型新建一条任务，默认分析最近 24 小时、最多 120 条候选新闻。

服务重启后，状态为 `pending` 或 `running` 的 Pipeline 会恢复为 `pending`，并按原来的 `hours`、`limit`、重试次数等参数从头执行。

分析完成后，`/runs/<run_id>` 里会出现 `report_id`。然后可以继续生成视频：

```bash
curl -X POST http://127.0.0.1:8000/render/report/<report_id>
```

视频确认无误后，标记本次报告新闻为已发布：

```bash
curl -X POST http://127.0.0.1:8000/reports/<report_id>/publish
```

发布确认会更新 `rss_news_dedupe.published_at`。之后即使 RSS 再次采集到同一条新闻，也不会进入下一期 `rss_llm_tts_queue`，从而避免重复播报。

查看报告列表：

```bash
curl -s http://127.0.0.1:8000/reports
```

查看报告详情和新闻明细：

```bash
curl -s http://127.0.0.1:8000/reports/<report_id>
```

修改报告中的单条新闻：

```bash
curl -X PUT http://127.0.0.1:8000/reports/<report_id>/news/<news_id> \
  -H "Content-Type: application/json" \
  -d '{
    "item_index": 1,
    "title": "新闻标题",
    "pubdate": "2026-06-27",
    "summary": "新闻摘要",
    "related_field": "游戏发布",
    "importance": "中",
    "reserve_reason": "保留理由",
    "link": "https://example.com/news",
    "voiceover_script": "视频口播稿"
  }'
```

`importance` 改为 `低` 后，该新闻仍保留在报告中，但不会进入视频播报。修改后需要重新渲染视频，视频才会使用新的口播稿和排序。

单条 TTS 管理：

```bash
# 生成或复用单条 TTS 音频
curl -X POST http://127.0.0.1:8000/tts-items/<queue_id>/generate

# 强制重新生成单条 TTS 音频
curl -X POST http://127.0.0.1:8000/tts-items/<queue_id>/retry

# 试听单条 TTS 音频
open http://127.0.0.1:8000/tts-items/<queue_id>/audio
```

报告详情页也提供“试听 / 生成 / 重试”按钮。编辑新闻标题、摘要、重要性或口播稿后，如果原 TTS 已经生成完成，该条会自动回到 `pending`，避免继续复用旧音频。

下载已生成的视频：

```bash
open http://127.0.0.1:8000/videos/<report_id>
```

查看成品资产列表，可按状态、报告 ID、更新时间筛选：

```bash
curl -s "http://127.0.0.1:8000/video-jobs?status=done&limit=50"
curl -s "http://127.0.0.1:8000/video-jobs?report_id=1"
curl -s "http://127.0.0.1:8000/video-jobs?date_from=2026-06-01&date_to=2026-06-30"
```

为成品视频生成封面：

```bash
curl -X POST http://127.0.0.1:8000/video-assets/<report_id>/cover
open http://127.0.0.1:8000/video-assets/<report_id>/cover
```

删除指定报告的成品视频、封面和音频缓存。数据库中的报告和新闻不会删除，后续可以重新渲染：

```bash
curl -X DELETE "http://127.0.0.1:8000/video-assets/<report_id>?delete_audio=true"
```

## 本地维护

查看健康摘要、磁盘空间和目录占用：

```bash
curl -s http://127.0.0.1:8000/maintenance/summary
```

预览可清理的旧输出、旧音频和旧缓存：

```bash
curl -X POST http://127.0.0.1:8000/maintenance/cleanup \
  -H "Content-Type: application/json" \
  -d '{
    "dry_run": true,
    "output_retention_days": 30,
    "audio_retention_days": 14,
    "cache_retention_days": 7
  }'
```

确认候选文件无误后执行清理：

```bash
curl -X POST http://127.0.0.1:8000/maintenance/cleanup \
  -H "Content-Type: application/json" \
  -d '{
    "dry_run": false,
    "output_retention_days": 30,
    "audio_retention_days": 14,
    "cache_retention_days": 7
  }'
```

Web 控制台的“维护”页也提供同样能力。建议先勾选“只预览”，确认候选文件后再执行真实清理。

执行真实清理（`dry_run: false`）时，除了删除磁盘文件，还会按保留天数清理 DB 日志表：审计日志按 `AUDIT_LOG_RETENTION_DAYS`（默认 90 天）、LLM 调用日志按 `LLM_CALL_LOG_RETENTION_DAYS`（默认 60 天）。设为 `0` 表示不清理对应表。返回结果中的 `database_logs` 字段会给出各表删除行数。

## 定时任务

服务启动时会自动创建两个默认计划：

```text
RSS 每两小时采集：0 */2 * * *
每日 17 点生成游戏行业日报：0 17 * * *
```

查看计划：

```bash
curl -s http://127.0.0.1:8000/schedules
```

修改或新增计划：

```bash
curl -X POST http://127.0.0.1:8000/schedules \
  -H "Content-Type: application/json" \
  -d '{
    "name": "每日 17 点生成游戏行业日报",
    "task_type": "daily_report",
    "cron_expression": "0 17 * * *",
    "timezone": "Asia/Shanghai",
    "enabled": true,
    "prevent_overlap": true
  }'
```

立即执行某个计划：

```bash
curl -X POST http://127.0.0.1:8000/schedules/1/run
```

查看最近的 pipeline 任务：

```bash
curl -s http://127.0.0.1:8000/runs
```

禁用某个计划：

```bash
curl -X POST http://127.0.0.1:8000/schedules/1/disable
```

当前支持 5 段 cron 表达式，例如 `0 */2 * * *`、`0 10,17 * * *`、`0 17 * * *`。调度器每 30 秒检查一次到期计划。`daily_report` 会执行：采集 RSS、分析最近 24 小时新闻、写入标准化表、创建视频渲染任务。

计划支持配置最大运行秒数、重试次数、重试间隔秒。调度触发和立即执行计划时，如果 Pipeline 抛出异常，会按重试次数自动重跑；用户取消不会触发重试。当前最大运行秒数已保存，但不会强制中断正在阻塞中的 RSS/LLM 请求。

## 浏览器选择

Remotion 会依次寻找：

1. `REMOTION_BROWSER_EXECUTABLE` 指定的程序
2. Google Chrome
3. Chromium
4. Microsoft Edge
5. Linux Chromium

通常无需手动配置。需要指定时在 `.env` 中加入：

```env
REMOTION_BROWSER_EXECUTABLE=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome
```

## 从 Docker 切换

本机服务启动前，需要释放 Docker 占用的 `8000` 端口：

```bash
cd /Users/qzzzzb_h/projects/codex/dify-rss/video-worker
docker compose down
./scripts/start-local.sh
```

Docker 配置暂时保留，仅用于回退，不再是默认运行方式。

## 云端部署

云端部署模板位于：

```text
deploy/systemd/
deploy/nginx/
deploy/logrotate/
deploy/scripts/
```

部署步骤见：

```text
../docs/cloud-deployment-runbook.md
```

## 变更记录

### 2026-06-29 稳定性与安全加固

本轮修复了若干逻辑与架构缺陷，涉及一个新迁移（`20260629_0005`），升级前请执行 `alembic upgrade head`。

**任务可靠性**

- 渲染超时：单次 Remotion 渲染增加墙上时钟超时（`RENDER_TIMEOUT_SECONDS`，默认 1800 秒）。超时后强制终止子进程组（Node + Chrome + FFmpeg），避免卡死的渲染进程永久阻塞单消费者渲染队列。
- 僵尸任务回收：调度器每 30 秒检查一次心跳。`rss_video_job` / `pipeline_runs` 中 `rendering` / `running` 且心跳早于阈值（`VIDEO_STALE_SECONDS` / `PIPELINE_STALE_SECONDS`，默认 900 秒）的任务会被回收——渲染任务杀进程组后标记失败，pipeline 任务标记失败。这让已有的 `worker_id` / `heartbeat_at` 字段真正闭环。
  - 说明：渲染路径靠「超时 + killpg」可真正闭环；pipeline 侧阻塞调用均有 I/O 超时自愈，回收仅作兜底。彻底释放 Python 进程内锁需将任务领取锁迁移到数据库租约，留待多进程/云端部署时再做。
- 恢复幂等：服务重启恢复未完成的 pipeline 任务时，若该任务已产出 `report_id`（崩溃发生在「分析已提交、run 未完成」窗口），不再重跑分析，直接复用已产出报告并重新调度渲染，避免产生重复 report 与孤儿 video job。

**状态语义**

- 新增 `cancelled` 视频状态，区分用户取消与真正失败。`set_video_job_cancelled` 改写为 `cancelled`，`/video-jobs` 筛选、取消接口返回值、终态判定集合同步更新。历史「任务由用户取消」的 `failed` 记录由迁移归一为 `cancelled`。
- TTS 字幕一致性：超长口播稿按 `MAX_TTS_CHARS`（260 字）截断后再合成音频，视频 props 中的字幕现在使用截断后的同一文本，避免字幕显示全文而音频中途停止。

**安全**

- 会话吊销：会话 token 加入 `session_version` 并参与签名。修改密码或禁用用户时递增该版本，旧 token 立即失效（此前只能等自然过期）。注意：.env 配置的 admin 账号 `session_version` 恒为 0，改 .env 密码不影响已签发 token，需重启并固定 `APP_SECRET_KEY` 才能彻底吊销。
  - 升级影响：token 格式从 4 段变为 5 段，升级后所有已登录会话失效，需重新登录（一次性）。
- CSRF 防护：登录中间件对非 GET 请求校验 `Origin` / `Referer` 与本机 host，配合已有的 `SameSite=Lax` cookie。
- RSS 解析加固：改用 `defusedxml` 解析外部 RSS，并限制单条 feed 体量不超过 5MB，防御实体膨胀（billion laughs 等）XML DoS。

**运维**

- DB 日志清理：`/maintenance/cleanup` 在真实清理（非预览）时，按保留天数清理审计日志与 LLM 调用日志（`AUDIT_LOG_RETENTION_DAYS` / `LLM_CALL_LOG_RETENTION_DAYS`，默认 90 / 60 天），返回 `database_logs` 删除行数。设为 `0` 表示不清理对应表。
