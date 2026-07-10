from __future__ import annotations

import html
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.config import settings


ProgressCallback = Callable[[float], None]


def _escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _browser_candidates() -> List[str]:
    return [
        os.getenv("REMOTION_BROWSER_EXECUTABLE", ""),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ]


def _find_browser() -> str:
    for candidate in _browser_candidates():
        if candidate and Path(candidate).exists():
            return candidate
    raise RuntimeError("No Chromium/Chrome executable found for FFmpeg template screenshots")


def _resolve_public_audio(src: str) -> Optional[Path]:
    if not src:
        return None
    path = Path(src)
    if path.is_absolute():
        return path if path.exists() else None
    public_path = settings.remotion_public_dir / src
    return public_path if public_path.exists() else None


def _scene_duration(value: Any, minimum: float) -> float:
    try:
        duration = float(value or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return max(minimum, duration + 1.0)


def _progress_html(active_index: Optional[int], total: int) -> str:
    if total <= 0:
        return ""
    parts = []
    for index in range(total):
        class_name = "progress-segment active" if index == active_index else "progress-segment"
        parts.append(f'<span class="{class_name}"></span>')
    return f'<div class="top-progress">{"".join(parts)}</div>'


def _base_html(width: int, height: int, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width={width}, height={height}, initial-scale=1" />
  <style>
    * {{ box-sizing: border-box; }}
    html, body {{
      width: {width}px;
      height: {height}px;
      margin: 0;
      overflow: hidden;
      background: #f7f7ef;
      color: #1f2933;
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    }}
    .frame {{
      position: relative;
      width: {width}px;
      height: {height}px;
      padding: 70px 92px 82px;
      background:
        radial-gradient(circle at 10% 0%, rgba(210, 99, 67, .12), transparent 30%),
        radial-gradient(circle at 92% 88%, rgba(59, 112, 128, .10), transparent 34%),
        #f7f7ef;
    }}
    .top-progress {{
      position: absolute;
      left: 64px;
      right: 64px;
      top: 34px;
      display: flex;
      gap: 8px;
    }}
    .progress-segment {{
      height: 9px;
      flex: 1;
      border-radius: 999px;
      background: rgba(31, 41, 51, .12);
    }}
    .progress-segment.active {{ background: #d26a4a; }}
    .kicker {{
      font-size: 30px;
      color: #6b7788;
      font-weight: 700;
      margin-bottom: 16px;
    }}
    h1 {{
      margin: 0;
      color: #cf6849;
      font-size: 74px;
      line-height: 1.08;
      letter-spacing: 0;
      font-weight: 800;
    }}
    h2 {{
      margin: 0 0 22px;
      color: #a5423e;
      font-size: 45px;
      line-height: 1.18;
      font-weight: 800;
    }}
    .hero {{
      height: 100%;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      text-align: center;
      gap: 34px;
    }}
    .hero .copy {{
      max-width: 1180px;
      font-size: 40px;
      line-height: 1.55;
      color: #344050;
      font-weight: 600;
    }}
    .news-layout {{
      height: 100%;
      display: grid;
      grid-template-rows: auto 1fr;
      gap: 38px;
    }}
    .news-header {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 42px;
      align-items: start;
    }}
    .badges {{
      display: flex;
      gap: 12px;
      justify-content: flex-end;
      flex-wrap: wrap;
      max-width: 520px;
    }}
    .badge {{
      padding: 11px 18px;
      border-radius: 999px;
      border: 1px solid rgba(54, 65, 82, .14);
      background: rgba(255, 255, 255, .72);
      color: #526071;
      font-size: 25px;
      font-weight: 700;
    }}
    .cards {{
      display: grid;
      grid-template-columns: 1.15fr .85fr;
      gap: 28px;
      min-height: 0;
    }}
    .card {{
      border-radius: 22px;
      border: 1px solid rgba(53, 64, 76, .14);
      background: rgba(255, 255, 255, .78);
      padding: 32px 36px;
      box-shadow: 0 18px 40px rgba(38, 50, 56, .08);
      overflow: hidden;
    }}
    .card.summary {{ grid-row: span 2; }}
    .card-title {{
      margin-bottom: 16px;
      color: #a5423e;
      font-size: 32px;
      font-weight: 800;
    }}
    .card-text {{
      color: #283543;
      font-size: 34px;
      line-height: 1.45;
      font-weight: 650;
      display: -webkit-box;
      -webkit-box-orient: vertical;
      -webkit-line-clamp: 8;
      overflow: hidden;
    }}
    .card.compact .card-text {{
      font-size: 30px;
      line-height: 1.42;
      -webkit-line-clamp: 5;
    }}
    .footer-note {{
      position: absolute;
      left: 92px;
      right: 92px;
      bottom: 36px;
      color: rgba(82, 96, 113, .92);
      font-size: 24px;
      display: flex;
      justify-content: space-between;
      gap: 24px;
    }}
  </style>
</head>
<body>
  {body}
</body>
</html>
"""


def _intro_slide(props: Dict[str, Any], width: int, height: int) -> str:
    body = f"""
<main class="frame">
  <section class="hero">
    <div class="kicker">{_escape(props.get("date"))}</div>
    <h1>{_escape(props.get("title") or "今日日报")}</h1>
    <div class="copy">{_escape(props.get("introVoiceover"))}</div>
  </section>
</main>
"""
    return _base_html(width, height, body)


def _outro_slide(props: Dict[str, Any], width: int, height: int) -> str:
    body = f"""
<main class="frame">
  <section class="hero">
    <div class="kicker">END</div>
    <h1>感谢收看</h1>
    <div class="copy">{_escape(props.get("outroVoiceover"))}</div>
  </section>
</main>
"""
    return _base_html(width, height, body)


def _news_slide(props: Dict[str, Any], item: Dict[str, Any], index: int, width: int, height: int) -> str:
    items = props.get("items") or []
    body = f"""
<main class="frame">
  {_progress_html(index, len(items))}
  <section class="news-layout">
    <header class="news-header">
      <div>
        <div class="kicker">{_escape(props.get("title"))}</div>
        <h1>{_escape(item.get("title"))}</h1>
      </div>
      <div class="badges">
        <span class="badge">重要性 {_escape(item.get("importance") or "中")}</span>
        <span class="badge">{_escape(item.get("relatedField") or "行业动态")}</span>
        <span class="badge">{_escape(item.get("pubdate") or props.get("date"))}</span>
      </div>
    </header>
    <div class="cards">
      <article class="card summary">
        <div class="card-title">核心摘要</div>
        <div class="card-text">{_escape(item.get("summary"))}</div>
      </article>
      <article class="card compact">
        <div class="card-title">入选理由</div>
        <div class="card-text">{_escape(item.get("reserveReason"))}</div>
      </article>
      <article class="card compact">
        <div class="card-title">口播要点</div>
        <div class="card-text">{_escape(item.get("voiceover"))}</div>
      </article>
    </div>
  </section>
  <footer class="footer-note">
    <span>{_escape(item.get("link"))}</span>
    <span>{index + 1} / {len(items)}</span>
  </footer>
</main>
"""
    return _base_html(width, height, body)


def _write_slide_svg(scene: Dict[str, Any], svg_path: Path, width: int, height: int) -> None:
    if scene["kind"] == "intro":
        content = _intro_svg(scene["props"], width, height)
    elif scene["kind"] == "outro":
        content = _outro_svg(scene["props"], width, height)
    else:
        content = _news_svg(scene["props"], scene["item"], scene["index"], width, height)
    svg_path.write_text(content, encoding="utf-8")


def _wrap_text(value: Any, max_chars: int, max_lines: int) -> List[str]:
    text = " ".join(str(value or "").split())
    if not text:
        return [""]
    lines: List[str] = []
    current = ""
    for char in text:
        current += char
        if len(current) >= max_chars:
            lines.append(current.strip())
            current = ""
            if len(lines) >= max_lines:
                break
    if current and len(lines) < max_lines:
        lines.append(current.strip())
    if len(lines) == max_lines and len("".join(lines)) < len(text):
        lines[-1] = lines[-1].rstrip("，。；、 ") + "..."
    return lines


def _svg_text(
    value: Any,
    *,
    x: int,
    y: int,
    size: int,
    max_chars: int,
    max_lines: int,
    fill: str = "#111827",
    weight: int = 600,
    line_height: Optional[int] = None,
) -> str:
    dy = line_height or int(size * 1.35)
    lines = _wrap_text(value, max_chars=max_chars, max_lines=max_lines)
    tspans = []
    for index, line in enumerate(lines):
        tspan_y = y + index * dy
        tspans.append(f'<tspan x="{x}" y="{tspan_y}">{_escape(line)}</tspan>')
    return (
        f'<text font-family="PingFang SC, Hiragino Sans GB, Noto Sans CJK SC, Noto Sans SC, Source Han Sans SC, Microsoft YaHei, Arial, sans-serif" '
        f'font-size="{size}" font-weight="{weight}" fill="{fill}">{"".join(tspans)}</text>'
    )


def _svg_frame(width: int, height: int, body: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <defs>
    <radialGradient id="warm" cx="8%" cy="0%" r="48%">
      <stop offset="0%" stop-color="#d26a4a" stop-opacity="0.16"/>
      <stop offset="100%" stop-color="#d26a4a" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="cool" cx="92%" cy="92%" r="46%">
      <stop offset="0%" stop-color="#3b7080" stop-opacity="0.12"/>
      <stop offset="100%" stop-color="#3b7080" stop-opacity="0"/>
    </radialGradient>
    <filter id="shadow" x="-20%" y="-20%" width="140%" height="150%">
      <feDropShadow dx="0" dy="18" stdDeviation="14" flood-color="#263238" flood-opacity="0.12"/>
    </filter>
  </defs>
  <rect width="{width}" height="{height}" fill="#f7f7ef"/>
  <rect width="{width}" height="{height}" fill="url(#warm)"/>
  <rect width="{width}" height="{height}" fill="url(#cool)"/>
  {body}
</svg>
"""


def _svg_progress(active_index: Optional[int], total: int) -> str:
    if total <= 0:
        return ""
    gap = 8
    left = 64
    top = 34
    width = 1920 - left * 2
    segment_width = (width - gap * (total - 1)) / total
    rects = []
    for index in range(total):
        x = left + index * (segment_width + gap)
        color = "#d26a4a" if index == active_index else "#d9d7cf"
        rects.append(f'<rect x="{x:.2f}" y="{top}" width="{segment_width:.2f}" height="9" rx="5" fill="{color}"/>')
    return "".join(rects)


def _intro_svg(props: Dict[str, Any], width: int, height: int) -> str:
    body = f"""
  {_svg_text(props.get("date"), x=760, y=260, size=30, max_chars=40, max_lines=1, fill="#6b7788", weight=700)}
  {_svg_text(props.get("title") or "今日日报", x=420, y=385, size=74, max_chars=18, max_lines=2, fill="#cf6849", weight=800, line_height=92)}
  {_svg_text(props.get("introVoiceover"), x=360, y=620, size=40, max_chars=36, max_lines=4, fill="#344050", weight=650, line_height=62)}
"""
    return _svg_frame(width, height, body)


def _outro_svg(props: Dict[str, Any], width: int, height: int) -> str:
    body = f"""
  {_svg_text("END", x=900, y=285, size=30, max_chars=20, max_lines=1, fill="#6b7788", weight=700)}
  {_svg_text("感谢收看", x=760, y=430, size=74, max_chars=12, max_lines=1, fill="#cf6849", weight=800)}
  {_svg_text(props.get("outroVoiceover"), x=410, y=620, size=40, max_chars=34, max_lines=4, fill="#344050", weight=650, line_height=62)}
"""
    return _svg_frame(width, height, body)


def _card(x: int, y: int, width: int, height: int, title: str, text: Any, *, compact: bool = False) -> str:
    text_size = 30 if compact else 34
    max_chars = 26 if compact else 30
    max_lines = 5 if compact else 8
    return f"""
  <rect x="{x}" y="{y}" width="{width}" height="{height}" rx="22" fill="#ffffff" opacity="0.86" stroke="#d7d7d0" filter="url(#shadow)"/>
  {_svg_text(title, x=x + 34, y=y + 60, size=32, max_chars=12, max_lines=1, fill="#a5423e", weight=800)}
  {_svg_text(text, x=x + 34, y=y + 122, size=text_size, max_chars=max_chars, max_lines=max_lines, fill="#283543", weight=650, line_height=int(text_size * 1.42))}
"""


def _news_svg(props: Dict[str, Any], item: Dict[str, Any], index: int, width: int, height: int) -> str:
    items = props.get("items") or []
    body = f"""
  {_svg_progress(index, len(items))}
  {_svg_text(props.get("title"), x=92, y=125, size=30, max_chars=30, max_lines=1, fill="#6b7788", weight=700)}
  {_svg_text(item.get("title"), x=92, y=230, size=64, max_chars=26, max_lines=2, fill="#cf6849", weight=800, line_height=78)}
  <rect x="1280" y="130" width="190" height="50" rx="25" fill="#ffffff" opacity="0.82" stroke="#d7d7d0"/>
  <rect x="1486" y="130" width="310" height="50" rx="25" fill="#ffffff" opacity="0.82" stroke="#d7d7d0"/>
  {_svg_text(f"重要性 {item.get('importance') or '中'}", x=1305, y=164, size=24, max_chars=12, max_lines=1, fill="#526071", weight=700)}
  {_svg_text(item.get("relatedField") or "行业动态", x=1516, y=164, size=24, max_chars=15, max_lines=1, fill="#526071", weight=700)}
  {_card(92, 380, 1010, 445, "核心摘要", item.get("summary"), compact=False)}
  {_card(1132, 380, 696, 200, "入选理由", item.get("reserveReason"), compact=True)}
  {_card(1132, 610, 696, 215, "口播要点", item.get("voiceover"), compact=True)}
  {_svg_text(item.get("link"), x=92, y=1000, size=22, max_chars=90, max_lines=1, fill="#526071", weight=500)}
  {_svg_text(f"{index + 1} / {len(items)}", x=1730, y=1000, size=24, max_chars=12, max_lines=1, fill="#526071", weight=700)}
"""
    return _svg_frame(width, height, body)


def _capture_slide_with_chrome(svg_path: Path, png_path: Path, width: int, height: int) -> None:
    browser = _find_browser()
    result = subprocess.run(
        [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            f"--window-size={width},{height}",
            f"--screenshot={png_path}",
            svg_path.resolve().as_uri(),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0 or not png_path.exists():
        details = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Failed to capture FFmpeg template slide via Chrome: {details}")


def _capture_slide_with_quicklook(svg_path: Path, png_path: Path, width: int, height: int) -> None:
    qlmanage = shutil.which("qlmanage")
    if not qlmanage:
        raise RuntimeError("qlmanage command not found; FFmpeg template screenshots require macOS Quick Look")
    generated_path = png_path.parent / f"{svg_path.name}.png"
    generated_path.unlink(missing_ok=True)
    result = subprocess.run(
        [qlmanage, "-t", "-s", str(max(width, height)), "-o", str(png_path.parent), str(svg_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0 or not generated_path.exists():
        details = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Failed to capture FFmpeg template slide via qlmanage: {details}")
    if png_path.exists():
        png_path.unlink()
    generated_path.rename(png_path)


def _capture_slide(_: str, svg_path: Path, png_path: Path, width: int, height: int) -> None:
    png_path.unlink(missing_ok=True)
    try:
        _capture_slide_with_chrome(svg_path, png_path, width, height)
    except Exception as chrome_error:
        try:
            _capture_slide_with_quicklook(svg_path, png_path, width, height)
        except Exception as quicklook_error:
            raise RuntimeError(
                "Failed to capture FFmpeg template slide. "
                f"Chrome error: {chrome_error}; Quick Look error: {quicklook_error}"
            ) from quicklook_error


def _ffmpeg_codec_args(codec: str) -> List[str]:
    if codec == "h264_videotoolbox":
        return ["-c:v", "h264_videotoolbox", "-b:v", "10M"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]


def _render_segment(
    image_path: Path,
    audio_path: Optional[Path],
    duration: float,
    output_path: Path,
    width: int,
    height: int,
    fps: int,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg command not found")

    def command_for(codec: str) -> List[str]:
        command = [
            ffmpeg,
            "-y",
            "-loop",
            "1",
            "-framerate",
            str(fps),
            "-i",
            str(image_path),
        ]
        if audio_path is not None:
            command += ["-i", str(audio_path)]
        else:
            command += [
                "-f",
                "lavfi",
                "-t",
                f"{duration:.3f}",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=44100",
            ]
        command += [
            "-t",
            f"{duration:.3f}",
            "-vf",
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
            "-r",
            str(fps),
            *_ffmpeg_codec_args(codec),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        return command

    codecs = ["h264_videotoolbox", "libx264"]
    last_error = ""
    for codec in codecs:
        result = subprocess.run(command_for(codec), capture_output=True, text=True, timeout=max(120, int(duration * 4)))
        if result.returncode == 0 and output_path.exists():
            return
        last_error = (result.stderr or result.stdout or "").strip()
    raise RuntimeError(f"Failed to render FFmpeg segment: {last_error[-2000:]}")


def _concat_segments(segment_paths: List[Path], output_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg command not found")
    list_path = output_path.parent / "segments.txt"
    list_lines = []
    for path in segment_paths:
        escaped_path = str(path).replace("'", "'\\''")
        list_lines.append(f"file '{escaped_path}'")
    list_path.write_text("\n".join(list_lines), encoding="utf-8")
    command = [
        ffmpeg,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    if result.returncode == 0 and output_path.exists():
        return

    fallback = [
        ffmpeg,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    fallback_result = subprocess.run(fallback, capture_output=True, text=True, timeout=1800)
    if fallback_result.returncode != 0 or not output_path.exists():
        details = (fallback_result.stderr or fallback_result.stdout or result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Failed to concat FFmpeg template segments: {details[-2000:]}")


def _build_scenes(props: Dict[str, Any]) -> List[Dict[str, Any]]:
    scenes: List[Dict[str, Any]] = [
        {
            "kind": "intro",
            "props": props,
            "audio_path": _resolve_public_audio(str(props.get("introAudioSrc") or "")),
            "duration": _scene_duration(props.get("introDuration"), 3.0),
        }
    ]
    for index, item in enumerate(props.get("items") or []):
        scenes.append(
            {
                "kind": "news",
                "props": props,
                "item": item,
                "index": index,
                "audio_path": _resolve_public_audio(str(item.get("audioSrc") or "")),
                "duration": _scene_duration(item.get("duration"), 8.0),
            }
        )
    scenes.append(
        {
            "kind": "outro",
            "props": props,
            "audio_path": _resolve_public_audio(str(props.get("outroAudioSrc") or "")),
            "duration": _scene_duration(props.get("outroDuration"), 3.0),
        }
    )
    return scenes


def render_ffmpeg_template(
    report_id: int,
    props: Dict[str, Any],
    work_dir: Path,
    on_progress: Optional[ProgressCallback] = None,
) -> Path:
    width = int(props.get("width") or settings.video_width)
    height = int(props.get("height") or settings.video_height)
    fps = int(props.get("fps") or settings.video_fps)
    output_path = work_dir / "final.mp4"
    template_dir = work_dir / "ffmpeg-template"
    slides_dir = template_dir / "slides"
    svg_dir = template_dir / "svg"
    segments_dir = template_dir / "segments"

    if template_dir.exists():
        shutil.rmtree(template_dir)
    svg_dir.mkdir(parents=True, exist_ok=True)
    slides_dir.mkdir(parents=True, exist_ok=True)
    segments_dir.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)

    scenes = _build_scenes(props)
    if not scenes:
        raise RuntimeError(f"No scenes for FFmpeg template render: report_id={report_id}")

    segment_paths: List[Path] = []
    total_steps = max(len(scenes) * 2 + 1, 1)
    completed_steps = 0

    def tick() -> None:
        if on_progress is not None:
            on_progress(min(completed_steps / total_steps, 0.99))

    for index, scene in enumerate(scenes):
        svg_path = svg_dir / f"slide_{index:03d}.svg"
        png_path = slides_dir / f"slide_{index:03d}.png"
        segment_path = segments_dir / f"segment_{index:03d}.mp4"
        _write_slide_svg(scene, svg_path, width, height)
        _capture_slide("", svg_path, png_path, width, height)
        completed_steps += 1
        tick()
        _render_segment(
            image_path=png_path,
            audio_path=scene.get("audio_path"),
            duration=float(scene["duration"]),
            output_path=segment_path,
            width=width,
            height=height,
            fps=fps,
        )
        segment_paths.append(segment_path)
        completed_steps += 1
        tick()

    _concat_segments(segment_paths, output_path)
    completed_steps += 1
    tick()
    if on_progress is not None:
        on_progress(1.0)
    return output_path
