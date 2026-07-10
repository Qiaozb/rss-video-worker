# 视频渲染引擎迁移方案

## 概述

将现有的双渲染引擎替换为新的双引擎架构：

| 角色 | 当前引擎 | 新引擎 | 变更 |
|---|---|---|---|
| **主引擎**（高质量+动画） | Remotion（React + headless Chrome） | **Playwright + FFmpeg** | 去掉 React/webpack，直接用 Playwright 驱动 Chromium 逐帧截图 |
| **备选引擎**（快速+稳定） | FFmpeg Template（SVG → qlmanage → PNG） | **Pillow + FFmpeg** | 去掉 macOS 绑定（qlmanage），用 Pillow 纯 Python 绘制 |

---

## 迁移顺序

建议分两个阶段执行，每阶段独立可验证、可回滚：

```
阶段 1：Pillow + FFmpeg 替换 FFmpeg Template（备选引擎）
  ↓ 验证通过
阶段 2：Playwright + FFmpeg 替换 Remotion（主引擎）
  ↓ 验证通过
阶段 3：清理（移除 Remotion、React、node_modules）
```

---

## 阶段 1：Pillow + FFmpeg 替换 FFmpeg Template

### 1.1 目标

将 `app/ffmpeg_template.py` 中的 SVG + qlmanage 截图链路替换为 Pillow 直接绘制 PNG，解决 macOS 平台绑定问题，实现跨平台部署。

### 1.2 改动范围

| 文件 | 操作 | 说明 |
|---|---|---|
| `app/pillow_renderer.py` | **新建** | Pillow 渲染器，替代 `ffmpeg_template.py` |
| `app/ffmpeg_template.py` | **保留** | 暂不删除，作为回退 |
| `app/main.py` | **修改** | 导入和调度逻辑增加 `pillow` 引擎 |
| `app/config.py` | **修改** | 新增 Pillow 字体路径配置 |
| `app/db.py` | **修改** | `render_engine` ENUM 新增 `'pillow'` 值 |
| `migrations/versions/` | **新建** | 迁移脚本：ENUM 添加 `'pillow'` |

### 1.3 Pillow 渲染器设计

#### 核心函数签名

```python
def render_pillow(
    report_id: int,
    props: Dict[str, Any],
    work_dir: Path,
    on_progress: Optional[Callable[[float], None]] = None,
) -> Path:
    """
    用 Pillow 逐场景绘制静态 PNG + FFmpeg 合成视频。
    接口与 render_ffmpeg_template / render_video 一致。
    """
```

#### 内部结构

```
render_pillow()
  ├── _build_scenes(props)           # 复用 ffmpeg_template._build_scenes()
  ├── 遍历 scenes:
  │     ├── _draw_intro(img, props, font_map)     # 绘制 intro 帧
  │     ├── _draw_news(img, props, item, index)   # 绘制 news 帧
  │     └── _draw_outro(img, props, font_map)     # 绘制 outro 帧
  ├── _render_segment(image_path, audio_path, duration, output_path, width, height, fps)
  │     # 复用 ffmpeg_template._render_segment()，完整 7 参数
  └── _concat_segments(segment_paths, output_path)
        # 复用 ffmpeg_template._concat_segments()
```

#### 绘制逻辑

Pillow 绘制一张 1920×1080 帧的典型流程：

```python
from PIL import Image, ImageDraw, ImageFont

def _draw_news(img: Image, props, item, index, fonts):
    draw = ImageDraw.Draw(img)

    # 1. 背景：填充 #f7f7ef + 径向渐变（Pillow 无原生渐变，用半透明圆近似）
    draw.rectangle([(0, 0), (1920, 1080)], fill="#f7f7ef")
    _draw_radial_overlay(draw)  # 用 alpha blend 模拟渐变

    # 2. 顶部进度条
    _draw_progress_bar(draw, active_index=index, total=len(props["items"]))

    # 3. 标题区
    draw.text((92, 100), props["title"], font=fonts["kicker"], fill="#6b7788")
    draw.text((92, 190), item["title"], font=fonts["hero"], fill="#cf6849")

    # 4. 卡片
    _draw_card(draw, x=92, y=380, w=1010, h=445, title="核心摘要", text=item["summary"], fonts=fonts)
    _draw_card(draw, x=1132, y=380, w=696, h=200, title="入选理由", text=item["reserveReason"], fonts=fonts)
    _draw_card(draw, x=1132, y=610, w=696, h=215, title="口播要点", text=item["voiceover"], fonts=fonts)

    # 5. 页脚
    draw.text((92, 1000), item.get("link", ""), font=fonts["footer"], fill="#526071")
    draw.text((1730, 1000), f"{index+1}/{len(props['items'])}", font=fonts["footer"], fill="#526071")

    return img
```

#### 关键实现细节

**卡片绘制（圆角矩形 + 阴影，使用 RGBA 合成）：**

```python
def _draw_card(base: Image.Image, x, y, w, h, title, text, fonts):
    """在 base (RGB) 上绘制带阴影的卡片。使用 RGBA overlay + alpha_composite 实现透明。"""
    # 1. 创建 RGBA 透明图层
    overlay = Image.new("RGBA", (w + 20, h + 30), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)

    # 2. 阴影：偏移的半透明圆角矩形
    _rounded_rect(odraw, 8, 12, w, h, radius=22, fill=(38, 50, 56, 40))

    # 3. 卡片主体：白色不透明
    _rounded_rect(odraw, 0, 0, w, h, radius=22, fill=(255, 255, 255, 240))

    # 4. 将 overlay 合成到 base 上（base 需先转为 RGBA）
    base_rgba = base.convert("RGBA")
    base_rgba.paste(
        Image.alpha_composite(
            Image.new("RGBA", overlay.size, (0, 0, 0, 0)),
            overlay,
        ),
        (x, y),
        overlay,  # 用 overlay 自身作为 mask
    )

    # 5. 在 base 上直接绘制文字（文字不需要透明度）
    draw = ImageDraw.Draw(base_rgba)
    draw.text((x + 34, y + 34), title, font=fonts["card_title"], fill="#a5423e")
    _draw_wrapped_text(draw, x + 34, y + 96, w - 68, text,
                       font=fonts["card_text"], fill="#283543", max_lines=8)

    # 6. 转回 RGB
    base.paste(base_rgba.convert("RGB"))
```

> **注意：** Pillow 的 `ImageDraw.Draw` 在 RGB 模式下不支持 alpha 通道填充，alpha 值会被静默忽略。所有需要半透明效果的元素（阴影、渐变叠加）必须通过 RGBA overlay + `Image.paste(mask=)` 或 `Image.alpha_composite()` 实现。建议整个渲染流程使用 RGBA 模式的 base image，最后一步 `convert("RGB")` 再交给 FFmpeg。

**文字自动换行：**

```python
def _draw_wrapped_text(draw, x, y, max_width, text, font, fill, max_lines=8, line_height=None):
    lh = line_height or int(font.size * 1.4)
    lines = _wrap_text_cjk(text, font, max_width, max_lines)
    for i, line in enumerate(lines):
        draw.text((x, y + i * lh), line, font=font, fill=fill)
```

**CJK 文字换行（按字符宽度累加）：**

```python
def _wrap_text_cjk(text, font, max_width, max_lines):
    """与 Pillow 的 textlength() 配合，按字符累加宽度做换行。"""
    lines = []
    current = ""
    for ch in text:
        test = current + ch
        if font.getlength(test) > max_width:
            lines.append(current)
            current = ch
            if len(lines) >= max_lines:
                lines[-1] = lines[-1].rstrip("，。；、 ") + "..."
                return lines
        else:
            current = test
    if current:
        lines.append(current)
    return lines
```

#### 字体加载

```python
# app/config.py 新增
@dataclass(frozen=True)
class Settings:
    # ...existing...
    pillow_font_dir: Path = field(default_factory=lambda: Path("/usr/share/fonts"))

    def load_fonts(self) -> dict:
        font_dir = self.pillow_font_dir
        # 按优先级查找中文字体
        candidates = [
            font_dir / "noto-cjk" / "NotoSansCJK-Regular.ttc",
            font_dir / "opentype" / "noto" / "NotoSansCJK-Regular.ttc",
            Path("/System/Library/Fonts/PingFang.ttc"),  # macOS fallback
        ]
        for p in candidates:
            if p.exists():
                return {
                    "kicker": ImageFont.truetype(str(p), 30),
                    "hero": ImageFont.truetype(str(p), 64),
                    "title": ImageFont.truetype(str(p), 45),
                    "card_title": ImageFont.truetype(str(p), 32),
                    "card_text": ImageFont.truetype(str(p), 30),
                    "footer": ImageFont.truetype(str(p), 22),
                }
        raise RuntimeError("No CJK font found for Pillow renderer")
```

### 1.4 main.py 调度改动

```python
# 导入
from app.pillow_renderer import render_pillow

# RenderEngine 类型扩展
RenderEngine = Literal["remotion", "ffmpeg", "pillow"]

# 归一化函数扩展
def _normalize_render_engine(value: Optional[str]) -> RenderEngine:
    normalized = (value or "remotion").strip().lower()
    if normalized in {"ffmpeg", "template", "ffmpeg_template", "fast"}:
        return "ffmpeg"
    if normalized in {"pillow", "p"}:
        return "pillow"
    return "remotion"

# 分发逻辑扩展（render_report_job 内）
engine_label_map = {"pillow": "Pillow", "ffmpeg": "FFmpeg 模板", "remotion": "Remotion"}
engine_label = engine_label_map.get(render_engine, render_engine)

if render_engine == "pillow":
    video_path = render_pillow(report_id, props, report_dir, on_progress=update_render_progress)
elif render_engine == "ffmpeg":
    video_path = render_ffmpeg_template(report_id, props, report_dir, on_progress=update_render_progress)
else:
    video_path = render_video(report_id, props, report_dir, on_progress=update_render_progress)
```

**重要：Pydantic 请求模型同步更新**

以下三个 API 请求模型中 `render_engine` 字段当前硬编码为 `Literal["remotion", "ffmpeg"]`，FastAPI 会在请求到达 `_normalize_render_engine()` 之前以 422 校验错误拒绝新值。每个阶段都必须同步更新这些 Literal 类型：

```python
# app/main.py 中的 Pydantic 模型（约 line 241, 257, 278）

class AnalyzeRSSRequest(BaseModel):
    # ...
    render_engine: Literal["remotion", "ffmpeg", "pillow"] = "remotion"  # 阶段1 加 "pillow"

class PipelineRunRequest(BaseModel):
    # ...
    render_engine: Literal["remotion", "ffmpeg", "pillow"] = "remotion"  # 阶段1 加 "pillow"

class ScheduleUpsertRequest(BaseModel):
    # ...
    render_engine: Literal["remotion", "ffmpeg", "pillow"] = "remotion"  # 阶段1 加 "pillow"
```

> 阶段 2 时再将三个模型扩展为 `Literal["remotion", "ffmpeg", "pillow", "playwright"]`。

### 1.5 数据库迁移

新建 Alembic 迁移脚本，扩展 `render_engine` ENUM：

```python
# migrations/versions/202607XX_0013_add_pillow_engine.py
revision = "202607XX_0013"
down_revision = "20260704_0012"
branch_labels = None
depends_on = None

def upgrade():
    op.execute("ALTER TABLE rss_video_job MODIFY COLUMN render_engine ENUM('remotion','ffmpeg','pillow') NOT NULL DEFAULT 'remotion'")
    op.execute("ALTER TABLE schedule_configs MODIFY COLUMN render_engine ENUM('remotion','ffmpeg','pillow') NOT NULL DEFAULT 'remotion'")

def downgrade():
    op.execute("ALTER TABLE rss_video_job MODIFY COLUMN render_engine ENUM('remotion','ffmpeg') NOT NULL DEFAULT 'remotion'")
    op.execute("ALTER TABLE schedule_configs MODIFY COLUMN render_engine ENUM('remotion','ffmpeg') NOT NULL DEFAULT 'remotion'")
```

注意：`db.py` 中有 4 处硬编码了 `ENUM('remotion', 'ffmpeg')`，需要同步更新：

1. `ensure_video_job_table()`（约 line 342）— `CREATE TABLE` DDL 中的 `render_engine` 列定义
2. `_add_columns_if_missing` 自愈 DDL（约 line 368）— `ALTER TABLE ... ADD COLUMN render_engine ENUM(...)`
3. `ensure_schedule_tables()`（约 line 407）— `schedule_configs` 表的 `CREATE TABLE` DDL
4. `_add_columns_if_missing` 自愈 DDL（约 line 431）— `schedule_configs` 表

每新增一个引擎值（`pillow`、`playwright`），这 4 处都需要加上对应值，否则全新安装或自愈时会用旧 ENUM 创建列。

### 1.6 验证清单

- [ ] `render_engine=pillow` 触发 Pillow 渲染，输出 MP4 可播放
- [ ] 中文标题、摘要、口播要点正确渲染，无乱码
- [ ] 卡片圆角、阴影视觉效果可接受
- [ ] 进度条高亮当前新闻
- [ ] 音频与画面时长匹配，无截断或空白
- [ ] `render_engine=remotion` 和 `render_engine=ffmpeg` 仍正常工作（回归测试）
- [ ] 在 Linux 环境（Docker）验证 Pillow 渲染可用

---

## 阶段 2：Playwright + FFmpeg 替换 Remotion

### 2.1 目标

用 Playwright 直接驱动 Chromium 渲染 HTML 页面并逐帧截图，替代 Remotion 的 React 组件 + webpack bundle + headless Chrome 链路。保留动画能力，去掉 React/Node.js 运行时依赖。

### 2.2 改动范围

| 文件 | 操作 | 说明 |
|---|---|---|
| `app/playwright_renderer.py` | **新建** | Playwright 渲染器 |
| `app/main.py` | **修改** | `render_engine` 新增值 `"playwright"`，调度逻辑 |
| `app/config.py` | **修改** | Playwright 相关配置 |
| `app/db.py` | **修改** | `render_engine` ENUM 新增 `'playwright'` |
| `remotion/public/` | **修改** | HTML 模板从 React 组件迁移为独立 HTML 文件 |
| `remotion/render.mjs` | **废弃** | 不再使用 |
| `remotion/src/*.tsx` | **废弃** | React 组件迁移为 HTML 模板 |
| `migrations/versions/` | **新建** | 迁移脚本 |

### 2.3 Playwright 渲染器设计

#### 核心函数签名

```python
def render_playwright(
    report_id: int,
    props: Dict[str, Any],
    work_dir: Path,
    on_progress: Optional[Callable[[float], None]] = None,
) -> Path:
    """
    用 Playwright 逐帧截图 HTML 页面 + FFmpeg 编码视频。
    接口与 render_video / render_ffmpeg_template 一致。
    """
```

#### 渲染流程

```
render_playwright()
  ├── 1. 启动 Playwright browser（Chromium, headless）
  ├── 2. 创建 page，设置 viewport = (1920, 1080)
  ├── 3. 生成 HTML 模板文件（复用 ffmpeg_template 的 CSS，加上动画关键帧）
  ├── 4. 逐帧循环：
  │     ├── 4a. 确定当前帧属于哪个场景（intro/news/outro）
  │     ├── 4b. 计算动画参数（opacity, translateY）
  │     ├── 4c. 通过 page.evaluate() 注入帧状态
  │     ├── 4d. page.screenshot() 截取 PNG
  │     └── 4e. 报告进度
  ├── 5. 关闭 browser
  ├── 6. FFmpeg：图片序列 + 音频 → MP4
  └── 7. 返回 output_path
```

#### 动画实现方式

与 Remotion 的区别：Remotion 用 React 状态驱动逐帧渲染，Playwright 方案用 **CSS 变量 + JS 注入** 控制动画状态。

**HTML 模板中预留动画接口：**

```html
<style>
  .scene {
    opacity: var(--scene-opacity, 0);
    transform: translateY(calc(var(--scene-offset, 20) * 1px));
    transition: none; /* 逐帧截图，不需要 CSS transition */
  }
</style>

<script>
  // Playwright 通过 evaluate 调用此函数设置每帧状态
  window.setFrameState = function(state) {
    const root = document.querySelector('.scene');
    root.style.setProperty('--scene-opacity', state.opacity);
    root.style.setProperty('--scene-offset', state.offsetY);
    // 更新字幕、进度条等动态内容
    if (state.subtitle) {
      document.querySelector('.subtitle-bar').textContent = state.subtitle;
    }
  };
</script>
```

**Playwright 逐帧驱动：**

```python
import math
from playwright.sync_api import sync_playwright

def _ease_out(t: float) -> float:
    """ease-out 缓动曲线，t ∈ [0, 1] → [0, 1]"""
    return 1 - (1 - t) ** 3

def _subtitle_at(scene: dict, local_frame: int, fps: int) -> str:
    """根据当前帧时间从 TTS 音频时间轴中提取对应的字幕文本。
    scene["subtitles"] 是 [{text, start_sec, end_sec}, ...] 列表，
    按 local_frame / fps 计算当前时间，返回匹配的子标题。
    """
    current_sec = local_frame / fps
    for sub in scene.get("subtitles", []):
        if sub["start_sec"] <= current_sec < sub["end_sec"]:
            return sub["text"]
    return ""

def render_playwright(report_id, props, work_dir, on_progress=None):
    fps = int(props.get("fps", 24))
    width = int(props.get("width", 1920))
    height = int(props.get("height", 1080))
    frames_dir = work_dir / "frames"
    frames_dir.mkdir(exist_ok=True)

    scenes = _build_playwright_scenes(props)  # 复用 _build_scenes 结构，扩展为含 HTML + 字幕时间轴
    total_frames = sum(scene["frame_count"] for scene in scenes)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": height})

        frame_index = 0
        for scene in scenes:
            # 加载该场景的 HTML
            page.set_content(scene["html"])

            for local_frame in range(scene["frame_count"]):
                # 计算动画参数
                progress = local_frame / max(scene["frame_count"] - 1, 1)
                opacity = _ease_out(min(progress * 2, 1.0))  # 前半段淡入
                offset_y = 20 * (1 - _ease_out(min(progress * 2, 1.0)))

                # 注入帧状态
                page.evaluate(
                    "state => window.setFrameState(state)",
                    {"opacity": opacity, "offsetY": offset_y,
                     "subtitle": _subtitle_at(scene, local_frame, fps)}
                )

                # 截图
                page.screenshot(
                    path=str(frames_dir / f"frame_{frame_index:06d}.png"),
                    type="png"
                )
                frame_index += 1

                if on_progress:
                    on_progress(frame_index / total_frames)

        browser.close()

    # FFmpeg 合成
    output_path = work_dir / "final.mp4"
    _ffmpeg_from_frames(frames_dir, props, output_path, fps)
    return output_path
```

#### 性能优化：场景复用

不需要每帧都重新加载 HTML。同一场景内只注入 `setFrameState()`，切换场景时才 `page.set_content()`：

```python
current_scene_html = None
for scene in scenes:
    if scene["html"] != current_scene_html:
        page.set_content(scene["html"])
        current_scene_html = scene["html"]
    # ...逐帧截图
```

#### HTML 模板策略

两种可选方案：

**方案 A：复用 ffmpeg_template.py 的 HTML/CSS 生成函数**

直接将 `_intro_slide()`、`_news_slide()`、`_outro_slide()` 的输出作为 Playwright 的页面内容，在上面叠加动画 CSS 和 `setFrameState()` 脚本。

优势：改动最小，布局逻辑已验证。
劣势：`ffmpeg_template.py` 的 HTML 是静态设计，需要增加动态层。

**方案 B：新建独立 HTML 模板目录**

```
remotion/public/templates/
  ├── intro.html       # intro 场景 HTML 模板（Jinja2 或 f-string）
  ├── news.html        # news 场景 HTML 模板
  ├── outro.html       # outro 场景 HTML 模板
  └── shared.css       # 共享样式（从 ffmpeg_template 抽取）
```

优势：模板独立维护，动画支持更完善。
劣势：CSS 需要在两个引擎间保持同步。

**建议：** 先用方案 A 快速验证，稳定后按需提取为方案 B。

#### FFmpeg 合成

```python
def _ffmpeg_from_frames(frames_dir, props, output_path, fps):
    """将帧序列 + 音频合成为 MP4"""
    # 先拼接所有场景的音频
    audio_concat = _concat_audio_tracks(props)

    # FFmpeg：帧序列 + 音频 → MP4
    subprocess.run([
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", str(frames_dir / "frame_%06d.png"),
        "-i", str(audio_concat),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        "-movflags", "+faststart",
        str(output_path),
    ], check=True)
```

### 2.4 main.py 调度改动

```python
RenderEngine = Literal["remotion", "ffmpeg", "pillow", "playwright"]

def _normalize_render_engine(value: Optional[str]) -> RenderEngine:
    normalized = (value or "remotion").strip().lower()
    if normalized in {"ffmpeg", "template", "ffmpeg_template", "fast"}:
        return "ffmpeg"
    if normalized in {"pillow", "p"}:
        return "pillow"
    if normalized in {"playwright", "pw"}:
        return "playwright"
    return "remotion"

# 分发逻辑
if render_engine == "playwright":
    video_path = render_playwright(report_id, props, report_dir, on_progress=update_render_progress)
elif render_engine == "pillow":
    video_path = render_pillow(report_id, props, report_dir, on_progress=update_render_progress)
elif render_engine == "ffmpeg":
    video_path = render_ffmpeg_template(report_id, props, report_dir, on_progress=update_render_progress)
else:
    video_path = render_video(report_id, props, report_dir, on_progress=update_render_progress)
```

**Pydantic 请求模型：** 同阶段 1，将 `AnalyzeRSSRequest`、`PipelineRunRequest`、`ScheduleUpsertRequest` 中的 `render_engine` Literal 扩展为 `Literal["remotion", "ffmpeg", "pillow", "playwright"]`。

**`_build_playwright_scenes()` 说明：** 此函数基于 `ffmpeg_template.py` 中的 `_build_scenes()` 结构扩展，额外增加 `html`（带 CSS 动画变量的完整 HTML 字符串）和 `subtitles`（`[{text, start_sec, end_sec}, ...]` 字幕时间轴）两个字段。阶段 2 初期可复用 `_build_scenes()` 的输出，在此基础上追加 HTML 生成和字幕解析逻辑。

### 2.5 config.py 新增

```python
@dataclass(frozen=True)
class Settings:
    # ...existing...
    playwright_headless: bool = True
    playwright_browser: str = "chromium"  # chromium / firefox / webkit
```

### 2.6 数据库迁移

```python
# migrations/versions/202607XX_0014_add_playwright_engine.py
revision = "202607XX_0014"
down_revision = "202607XX_0013"   # 指向阶段 1 的迁移
branch_labels = None
depends_on = None

def upgrade():
    op.execute("ALTER TABLE rss_video_job MODIFY COLUMN render_engine ENUM('remotion','ffmpeg','pillow','playwright') NOT NULL DEFAULT 'remotion'")
    op.execute("ALTER TABLE schedule_configs MODIFY COLUMN render_engine ENUM('remotion','ffmpeg','pillow','playwright') NOT NULL DEFAULT 'remotion'")

def downgrade():
    op.execute("ALTER TABLE rss_video_job MODIFY COLUMN render_engine ENUM('remotion','ffmpeg','pillow') NOT NULL DEFAULT 'remotion'")
    op.execute("ALTER TABLE schedule_configs MODIFY COLUMN render_engine ENUM('remotion','ffmpeg','pillow') NOT NULL DEFAULT 'remotion'")
```

`db.py` 中 4 处硬编码的 ENUM 值也需同步加上 `'playwright'`（详见阶段 1 §1.5 说明）。

### 2.7 验证清单

- [ ] `render_engine=playwright` 渲染输出 MP4 可播放
- [ ] 场景淡入动画（opacity 0→1）正常
- [ ] 场景上滑动画（translateY 20→0）正常
- [ ] 字幕与音频同步
- [ ] 进度条正确高亮当前新闻
- [ ] 渲染耗时 ≤ Remotion（目标：同等内容提速 50%+）
- [ ] Chromium 进程在渲染完成后正确关闭，无僵尸进程
- [ ] 渲染超时（render_timeout_seconds）正确杀进程
- [ ] 渲染失败时正确设置 job 状态为 failed

---

## 阶段 3：清理

当两个新引擎稳定运行后，移除旧引擎相关代码和依赖。

### 3.1 文件删除

| 文件/目录 | 说明 |
|---|---|
| `app/remotion.py` | Remotion 渲染器 |
| `app/ffmpeg_template.py` | 旧 FFmpeg Template 渲染器 |
| `remotion/render.mjs` | Remotion 渲染入口 |
| `remotion/src/DailyBriefing.tsx` | React 视频组件 |
| `remotion/src/index.tsx` | React 入口 |
| `.cache/remotion-bundle/` | webpack bundle 缓存 |

### 3.2 package.json 精简

```json
{
  "name": "dify-rss-video-worker",
  "private": true,
  "type": "module",
  "dependencies": {},
  "devDependencies": {}
}
```

移除：`@remotion/bundler`、`@remotion/renderer`、`remotion`、`react`、`react-dom`、`@types/react`、`@types/react-dom`、`typescript`。

然后执行 `rm -rf node_modules/ && npm prune`（或 `npm install --omit=dev`）。

预期 node_modules 从 280MB 降到 ~0。

### 3.3 main.py 清理

- 移除 `from app.remotion import ...` 导入
- 移除 `from app.ffmpeg_template import ...` 导入
- 移除 `cancel_render()`、`cancel_all_renders()` 对 Remotion 进程的调用
- `_render_worker_loop` 中不再需要 `_cleanup_process_group` 逻辑（Playwright 和 Pillow 都是 Python 进程内操作）
- `RenderEngine` 简化为 `Literal["pillow", "playwright"]`
- `_normalize_render_engine()` 默认值改为 `"playwright"`

### 3.4 config.py 清理

- 移除 `remotion_root`、`remotion_public_dir`（或重命名为 `template_dir`）
- 移除 `render_timeout_seconds`（Playwright/Pillow 用更短的超时即可）

### 3.5 数据库迁移

```python
def upgrade():
    op.execute("ALTER TABLE rss_video_job MODIFY COLUMN render_engine ENUM('pillow','playwright') NOT NULL DEFAULT 'playwright'")
    op.execute("ALTER TABLE schedule_configs MODIFY COLUMN render_engine ENUM('pillow','playwright') NOT NULL DEFAULT 'playwright'")
```

### 3.6 验证清单

- [ ] `npm` / `node` 不再被项目启动流程依赖
- [ ] `render_engine=playwright` 和 `render_engine=pillow` 均正常
- [ ] 旧 `render_engine=remotion` 和 `render_engine=ffmpeg` 的 API 调用被正确归一化到新引擎
- [ ] 项目总大小从 ~434MB 降至 ~150MB 以下

---

## 风险与回滚

### 阶段 1 回滚

Pillow 渲染器出问题：API 请求指定 `render_engine=ffmpeg`（或 `remotion`）即可切回旧引擎，无需代码变更。

### 阶段 2 回滚

Playwright 渲染器出问题：API 请求指定 `render_engine=remotion` 即可切回，Remotion 引擎代码在阶段 3 之前保持完整。

### 阶段 3 回滚

阶段 3 是最终清理，不可直接回滚。建议通过 git tag 标记清理前的版本，需要时可 checkout 恢复。

---

## 时间估算

| 阶段 | 预估工时 | 说明 |
|---|---|---|
| 阶段 1：Pillow + FFmpeg | 2-3 天 | 绘制逻辑 + 字体适配 + 调试 |
| 阶段 2：Playwright + FFmpeg | 3-5 天 | 逐帧截图 + 动画调试 + 性能优化 |
| 阶段 3：清理 | 0.5 天 | 删除文件 + 精简配置 |
| 总计 | 5.5-8.5 天 | |

---

## 依赖清单

### 新增 Python 依赖

```
Pillow>=10.0        # 阶段 1：图像绘制
playwright>=1.40    # 阶段 2：浏览器自动化
```

### 系统依赖

```
ffmpeg              # 已有，两个引擎都需要
fonts-noto-cjk      # Pillow 渲染需要中文字体（Linux: apt install fonts-noto-cjk）
```

Playwright 首次使用需要安装浏览器：
```bash
playwright install chromium
```

### 移除的依赖

```
react, react-dom                        # 阶段 3 移除
@remotion/bundler, @remotion/renderer   # 阶段 3 移除
remotion                                # 阶段 3 移除
liquid-glass-react                      # 已在 web 控制台迁移中移除
esbuild                                 # 已在 web 控制台迁移中移除
```
