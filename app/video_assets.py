from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

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
    return settings.remotion_public_dir / "generated" / f"report_{report_id}"


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


def generate_cover_image(report_id: int, video_path_value: Optional[str]) -> Path:
    video_path = safe_output_path(video_path_value)
    if video_path is None or not video_path.exists():
        raise FileNotFoundError("video file not found")

    cover_path = report_output_dir(report_id) / "cover.jpg"
    cover_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            "00:00:01",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(cover_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return cover_path


def remove_report_assets(report_id: int, delete_audio: bool = True) -> Dict[str, Any]:
    removed = []
    errors = []

    for path in [report_output_dir(report_id), report_audio_dir(report_id) if delete_audio else None]:
        if path is None:
            continue
        try:
            resolved = path.expanduser().resolve()
            allowed_roots = [settings.output_dir, settings.remotion_public_dir / "generated"]
            if not any(is_within(resolved, root) for root in allowed_roots):
                errors.append({"path": str(path), "error": "path outside managed directories"})
                continue
            if resolved.exists():
                shutil.rmtree(resolved)
                removed.append(str(resolved))
        except OSError as exc:
            errors.append({"path": str(path), "error": str(exc)})

    return {
        "report_id": report_id,
        "delete_audio": delete_audio,
        "removed": removed,
        "errors": errors,
    }
