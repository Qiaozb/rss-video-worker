from __future__ import annotations

import html
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings


def is_within(path: Path, root: Path) -> bool:
    resolved = path.expanduser().resolve()
    resolved_root = root.expanduser().resolve()
    return resolved == resolved_root or resolved_root in resolved.parents


def safe_output_path(path_value: Optional[str]) -> Optional[Path]:
    if not path_value:
        return None
    path = Path(path_value).expanduser().resolve()
    if not is_within(path, settings.output_dir):
        raise ValueError("video asset path is outside output directory")
    return path


def report_output_dir(report_id: int) -> Path:
    return settings.output_dir / f"report_{report_id}"


def report_audio_dir(report_id: int) -> Path:
    return report_output_dir(report_id) / "audio"


def report_audio_src(report_id: int, filename: str) -> str:
    safe_filename = Path(filename).name
    if safe_filename != filename:
        raise ValueError("audio filename must not contain a directory")
    return f"report_{report_id}/audio/{safe_filename}"


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
            return str(candidate)
    raise RuntimeError("No Chromium/Chrome executable found for cover generation")


def _escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


_LINE_START_FORBIDDEN_CHARS = set("，。；：！？、,.!?;:)]）】》」』”’…")


def _wrap_text(value: Any, max_chars: int, max_lines: int) -> List[str]:
    text = " ".join(str(value or "").split())
    if not text:
        return [""]
    lines: List[str] = []
    current = ""
    for index, char in enumerate(text):
        current += char
        if len(current) >= max_chars:
            next_char = text[index + 1] if index + 1 < len(text) else ""
            if next_char in _LINE_START_FORBIDDEN_CHARS:
                continue
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
    weight: int = 700,
    line_height: Optional[int] = None,
) -> str:
    dy = line_height or int(size * 1.35)
    tspans = []
    for index, line in enumerate(_wrap_text(value, max_chars=max_chars, max_lines=max_lines)):
        tspans.append(f'<tspan x="{x}" y="{y + index * dy}">{_escape(line)}</tspan>')
    return (
        f'<text font-family="PingFang SC, Hiragino Sans GB, Noto Sans CJK SC, Noto Sans SC, '
        f'Source Han Sans SC, Microsoft YaHei, Arial, sans-serif" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}">{"".join(tspans)}</text>'
    )


def _cover_svg(report: Dict[str, Any], news: List[Dict[str, Any]], width: int, height: int) -> str:
    title = report.get("title") or "今日新闻精选"
    category = report.get("rss_category") or report.get("report_type") or "daily"
    daily_trend = report.get("daily_trend") or "精选近期重点消息，整理成适合视频发布的日报内容。"
    today = datetime.now().strftime("%Y.%m.%d")
    top_news = [item.get("title") for item in news[:3] if item.get("title")]
    if not top_news:
        top_news = ["今日重点消息", "趋势观察", "视频日报"]

    if width / max(height, 1) < 1.5:
        return _cover_svg_4x3(title, category, daily_trend, today, top_news, width, height)

    return _cover_svg_16x9(title, category, daily_trend, today, top_news, width, height)


def _cover_svg_16x9(
    title: str,
    category: str,
    daily_trend: str,
    today: str,
    top_news: List[str],
    width: int,
    height: int,
) -> str:

    bullets = []
    for index, item_title in enumerate(top_news[:3], start=1):
        y = 515 + (index - 1) * 110
        bullets.append(f'<circle cx="1060" cy="{y - 10}" r="14" fill="#d26a4a"/>')
        bullets.append(_svg_text(item_title, x=1102, y=y, size=31, max_chars=18, max_lines=2, fill="#172033", weight=800, line_height=39))

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 1920 1080">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#fbf6ec"/>
      <stop offset="58%" stop-color="#f8fafc"/>
      <stop offset="100%" stop-color="#eef7f3"/>
    </linearGradient>
    <radialGradient id="warm" cx="0%" cy="0%" r="70%">
      <stop offset="0%" stop-color="#d26a4a" stop-opacity="0.24"/>
      <stop offset="100%" stop-color="#d26a4a" stop-opacity="0"/>
    </radialGradient>
    <filter id="shadow" x="-20%" y="-20%" width="140%" height="150%">
      <feDropShadow dx="0" dy="22" stdDeviation="18" flood-color="#263238" flood-opacity="0.14"/>
    </filter>
  </defs>
  <rect width="{width}" height="{height}" fill="url(#bg)"/>
  <rect width="{width}" height="{height}" fill="url(#warm)"/>
  <circle cx="1120" cy="120" r="150" fill="#3b7080" opacity="0.11"/>
  <circle cx="1160" cy="590" r="230" fill="#d26a4a" opacity="0.08"/>
  <rect x="74" y="222" width="1772" height="636" rx="44" fill="#ffffff" opacity="0.72" stroke="#e2e8f0" filter="url(#shadow)"/>
  <rect x="106" y="256" width="140" height="48" rx="24" fill="#172033"/>
  {_svg_text("DAILY", x=136, y=289, size=22, max_chars=10, max_lines=1, fill="#ffffff", weight=800)}
  {_svg_text(today, x=272, y=290, size=26, max_chars=20, max_lines=1, fill="#6b7788", weight=700)}
  {_svg_text(str(category).upper(), x=106, y=388, size=32, max_chars=24, max_lines=1, fill="#d26a4a", weight=900)}
  {_svg_text(title, x=106, y=492, size=76, max_chars=10, max_lines=2, fill="#111827", weight=900, line_height=88)}
  <rect x="106" y="674" width="690" height="2" fill="#d7d7d0"/>
  {_svg_text(daily_trend, x=106, y=734, size=30, max_chars=26, max_lines=2, fill="#364152", weight=700, line_height=42)}
  <rect x="960" y="292" width="820" height="515" rx="28" fill="#f8fafc" stroke="#e2e8f0"/>
  {_svg_text("今日要点", x=1024, y=382, size=42, max_chars=8, max_lines=1, fill="#a5423e", weight=900)}
  {"".join(bullets)}
</svg>
"""


def _cover_svg_4x3(
    title: str,
    category: str,
    daily_trend: str,
    today: str,
    top_news: List[str],
    width: int,
    height: int,
) -> str:
    bullets = []
    for index, item_title in enumerate(top_news[:3], start=1):
        y = 782 + (index - 1) * 78
        bullets.append(f'<circle cx="128" cy="{y - 10}" r="13" fill="#d26a4a"/>')
        bullets.append(_svg_text(item_title, x=166, y=y, size=29, max_chars=38, max_lines=1, fill="#172033", weight=800))

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 1440 1080">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#fbf6ec"/>
      <stop offset="58%" stop-color="#f8fafc"/>
      <stop offset="100%" stop-color="#eef7f3"/>
    </linearGradient>
    <radialGradient id="warm" cx="0%" cy="0%" r="70%">
      <stop offset="0%" stop-color="#d26a4a" stop-opacity="0.22"/>
      <stop offset="100%" stop-color="#d26a4a" stop-opacity="0"/>
    </radialGradient>
    <filter id="shadow" x="-20%" y="-20%" width="140%" height="150%">
      <feDropShadow dx="0" dy="22" stdDeviation="18" flood-color="#263238" flood-opacity="0.14"/>
    </filter>
  </defs>
  <rect width="1440" height="1080" fill="url(#bg)"/>
  <rect width="1440" height="1080" fill="url(#warm)"/>
  <circle cx="1120" cy="130" r="160" fill="#3b7080" opacity="0.11"/>
  <circle cx="930" cy="795" r="260" fill="#d26a4a" opacity="0.08"/>
  <rect x="56" y="105" width="1328" height="870" rx="44" fill="#ffffff" opacity="0.74" stroke="#e2e8f0" filter="url(#shadow)"/>
  <rect x="108" y="141" width="140" height="48" rx="24" fill="#172033"/>
  {_svg_text("DAILY", x=138, y=174, size=22, max_chars=10, max_lines=1, fill="#ffffff", weight=800)}
  {_svg_text(today, x=274, y=175, size=26, max_chars=20, max_lines=1, fill="#6b7788", weight=700)}
  {_svg_text(str(category).upper(), x=108, y=267, size=32, max_chars=24, max_lines=1, fill="#d26a4a", weight=900)}
  {_svg_text(title, x=108, y=373, size=76, max_chars=10, max_lines=2, fill="#111827", weight=900, line_height=88)}
  <rect x="108" y="535" width="640" height="2" fill="#d7d7d0"/>
  {_svg_text(daily_trend, x=108, y=595, size=30, max_chars=31, max_lines=2, fill="#364152", weight=700, line_height=42)}
  {_svg_text("今日要点", x=108, y=709, size=42, max_chars=8, max_lines=1, fill="#a5423e", weight=900)}
  <rect x="108" y="739" width="1224" height="230" rx="30" fill="#f8fafc" stroke="#e2e8f0"/>
  {"".join(bullets)}
</svg>
"""


def file_info(path_value: Optional[str]) -> Dict[str, Any]:
    if not path_value:
        return {
            "exists": False,
            "path": None,
            "size_bytes": 0,
            "size_mb": 0,
            "mtime": None,
        }
    try:
        path = safe_output_path(path_value)
    except ValueError:
        return {
            "exists": False,
            "path": path_value,
            "size_bytes": 0,
            "size_mb": 0,
            "mtime": None,
            "error": "path outside output directory",
        }
    if path is None or not path.exists() or not path.is_file():
        return {
            "exists": False,
            "path": str(path) if path else path_value,
            "size_bytes": 0,
            "size_mb": 0,
            "mtime": None,
        }
    stat = path.stat()
    return {
        "exists": True,
        "path": str(path),
        "size_bytes": stat.st_size,
        "size_mb": round(stat.st_size / 1024 / 1024, 2),
        "mtime": stat.st_mtime,
    }


def _capture_cover_svg(svg_path: Path, png_path: Path, width: int, height: int) -> None:
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
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0 or not png_path.exists():
        details = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"failed to capture cover image: {details}")


def _convert_png_to_jpeg(png_path: Path, cover_path: Path) -> None:
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(png_path),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(cover_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0 or not cover_path.exists():
        details = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"failed to convert cover image: {details}")


def _generate_cover_variant(
    report_id: int,
    report: Dict[str, Any],
    news: List[Dict[str, Any]],
    *,
    width: int,
    height: int,
    stem: str,
) -> Path:
    output_dir = report_output_dir(report_id)
    cover_path = output_dir / f"{stem}.jpg"
    svg_path = output_dir / f"{stem}.svg"
    png_path = output_dir / f"{stem}.png"
    output_dir.mkdir(parents=True, exist_ok=True)
    svg_path.write_text(_cover_svg(report, news, width, height), encoding="utf-8")
    png_path.unlink(missing_ok=True)
    cover_path.unlink(missing_ok=True)
    _capture_cover_svg(svg_path, png_path, width, height)
    _convert_png_to_jpeg(png_path, cover_path)
    return cover_path


def generate_cover_images(
    report_id: int,
    video_path_value: Optional[str],
    report: Optional[Dict[str, Any]] = None,
    news: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Path]:
    if video_path_value:
        video_path = safe_output_path(video_path_value)
        if video_path is not None and not video_path.exists():
            raise FileNotFoundError("video file not found")

    return {
        "16x9": _generate_cover_variant(
            report_id,
            report or {},
            news or [],
            width=1920,
            height=1080,
            stem="cover",
        ),
        "4x3": _generate_cover_variant(
            report_id,
            report or {},
            news or [],
            width=1440,
            height=1080,
            stem="cover_4x3",
        ),
    }


def generate_cover_image(
    report_id: int,
    video_path_value: Optional[str],
    report: Optional[Dict[str, Any]] = None,
    news: Optional[List[Dict[str, Any]]] = None,
) -> Path:
    return generate_cover_images(report_id, video_path_value, report=report, news=news)["16x9"]


def remove_report_assets(report_id: int, delete_audio: bool = True) -> Dict[str, Any]:
    removed = []
    errors = []

    output_dir = report_output_dir(report_id)
    audio_dir = report_audio_dir(report_id)
    if delete_audio:
        targets = [output_dir]
    elif output_dir.exists():
        targets = [path for path in output_dir.iterdir() if path != audio_dir]
    else:
        targets = []

    for path in targets:
        try:
            resolved = path.expanduser().resolve()
            if not is_within(resolved, settings.output_dir):
                errors.append({"path": str(path), "error": "path outside managed directories"})
                continue
            if resolved.exists():
                if resolved.is_dir():
                    shutil.rmtree(resolved)
                else:
                    resolved.unlink()
                removed.append(str(resolved))
        except OSError as exc:
            errors.append({"path": str(path), "error": str(exc)})

    return {
        "report_id": report_id,
        "delete_audio": delete_audio,
        "removed": removed,
        "errors": errors,
    }
