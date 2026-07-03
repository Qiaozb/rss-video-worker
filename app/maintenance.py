from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests

from app.config import settings
from app.db import delete_old_audit_logs, delete_old_llm_call_logs, get_connection


@dataclass(frozen=True)
class CleanupRule:
    category: str
    root: Path
    patterns: tuple[str, ...]
    retention_days: int


def bytes_to_mb(value: int) -> float:
    return round(value / 1024 / 1024, 2)


def utc_timestamp_from_path(path: Path) -> Optional[str]:
    try:
        return datetime.utcfromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        return None


def is_within(path: Path, root: Path) -> bool:
    resolved = path.expanduser().resolve()
    resolved_root = root.expanduser().resolve()
    return resolved == resolved_root or resolved_root in resolved.parents


def safe_iter_files(root: Path, patterns: Iterable[str] = ("*",)) -> List[Path]:
    root = root.expanduser().resolve()
    if not root.exists():
        return []
    files: List[Path] = []
    for pattern in patterns:
        files.extend(path for path in root.rglob(pattern) if path.is_file())
    return sorted(set(files))


def directory_stats(label: str, path: Path, patterns: Iterable[str] = ("*",)) -> Dict[str, Any]:
    files = safe_iter_files(path, patterns)
    total_bytes = 0
    oldest: Optional[float] = None
    newest: Optional[float] = None
    for file_path in files:
        try:
            stat = file_path.stat()
        except OSError:
            continue
        total_bytes += stat.st_size
        oldest = stat.st_mtime if oldest is None else min(oldest, stat.st_mtime)
        newest = stat.st_mtime if newest is None else max(newest, stat.st_mtime)

    return {
        "label": label,
        "path": str(path),
        "exists": path.exists(),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "total_mb": bytes_to_mb(total_bytes),
        "oldest_mtime": datetime.utcfromtimestamp(oldest).strftime("%Y-%m-%d %H:%M:%S") if oldest else None,
        "newest_mtime": datetime.utcfromtimestamp(newest).strftime("%Y-%m-%d %H:%M:%S") if newest else None,
    }


def database_health() -> Dict[str, Any]:
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1 AS ok")
                cursor.fetchone()
        return {"status": "ok", "database": settings.mysql_database}
    except Exception as exc:
        return {"status": "failed", "database": settings.mysql_database, "error": str(exc)[:1000]}


def tts_health() -> Dict[str, Any]:
    url = f"{settings.tts_base_url.rstrip('/')}/health"
    try:
        response = requests.get(url, timeout=5)
        return {
            "status": "ok" if response.ok else "failed",
            "url": url,
            "http_status": response.status_code,
            "body": response.text[:1000],
        }
    except Exception as exc:
        return {"status": "failed", "url": url, "error": str(exc)[:1000]}


def filesystem_health() -> Dict[str, Any]:
    usage = shutil.disk_usage(settings.project_root)
    return {
        "path": str(settings.project_root),
        "total_mb": bytes_to_mb(usage.total),
        "used_mb": bytes_to_mb(usage.used),
        "free_mb": bytes_to_mb(usage.free),
        "free_percent": round(usage.free / usage.total * 100, 2) if usage.total else 0,
    }


def maintenance_summary() -> Dict[str, Any]:
    generated_audio_dir = settings.remotion_public_dir / "generated"
    log_dir = settings.project_root / "logs"
    cache_dir = settings.project_root / ".cache"
    remotion_cache_dir = settings.remotion_root / ".cache"

    directories = [
        directory_stats("输出目录", settings.output_dir),
        directory_stats("成品视频", settings.output_dir, ("*.mp4",)),
        directory_stats("TTS 音频缓存", generated_audio_dir, ("*.wav",)),
        directory_stats("日志", log_dir, ("*.log",)),
        directory_stats("Worker 缓存", cache_dir),
        directory_stats("Remotion 缓存", remotion_cache_dir),
    ]
    return {
        "status": "ok",
        "checked_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "services": {
            "database": database_health(),
            "tts": tts_health(),
        },
        "filesystem": filesystem_health(),
        "directories": directories,
    }


def cleanup_rules(
    output_retention_days: int,
    audio_retention_days: int,
    cache_retention_days: int,
) -> List[CleanupRule]:
    return [
        CleanupRule(
            category="output",
            root=settings.output_dir,
            patterns=("*.mp4", "props.json", "*.png", "*.jpg", "*.jpeg"),
            retention_days=output_retention_days,
        ),
        CleanupRule(
            category="audio",
            root=settings.remotion_public_dir / "generated",
            patterns=("*.wav",),
            retention_days=audio_retention_days,
        ),
        CleanupRule(
            category="cache",
            root=settings.project_root / ".cache",
            patterns=("*",),
            retention_days=cache_retention_days,
        ),
        CleanupRule(
            category="remotion_cache",
            root=settings.remotion_root / ".cache",
            patterns=("*",),
            retention_days=cache_retention_days,
        ),
    ]


def cleanup_candidates(rules: Iterable[CleanupRule]) -> List[Dict[str, Any]]:
    now = datetime.utcnow()
    candidates: List[Dict[str, Any]] = []
    for rule in rules:
        cutoff = now - timedelta(days=max(rule.retention_days, 0))
        for file_path in safe_iter_files(rule.root, rule.patterns):
            if not is_within(file_path, rule.root):
                continue
            try:
                stat = file_path.stat()
            except OSError:
                continue
            mtime = datetime.utcfromtimestamp(stat.st_mtime)
            if mtime > cutoff:
                continue
            candidates.append(
                {
                    "category": rule.category,
                    "path": str(file_path),
                    "size_bytes": stat.st_size,
                    "size_mb": bytes_to_mb(stat.st_size),
                    "mtime": mtime.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
    return candidates


def prune_empty_dirs(root: Path) -> int:
    root = root.expanduser().resolve()
    if not root.exists():
        return 0

    removed = 0
    for directory in sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True):
        if not is_within(directory, root):
            continue
        try:
            directory.rmdir()
            removed += 1
        except OSError:
            continue
    return removed


def cleanup_artifacts(
    output_retention_days: int = 30,
    audio_retention_days: int = 14,
    cache_retention_days: int = 7,
    dry_run: bool = True,
) -> Dict[str, Any]:
    rules = cleanup_rules(output_retention_days, audio_retention_days, cache_retention_days)
    candidates = cleanup_candidates(rules)
    deleted: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []

    if not dry_run:
        for candidate in candidates:
            file_path = Path(candidate["path"])
            matching_root = next(
                (rule.root for rule in rules if candidate["category"] == rule.category),
                None,
            )
            if matching_root is None or not is_within(file_path, matching_root):
                errors.append({"path": str(file_path), "error": "path is outside cleanup root"})
                continue
            try:
                file_path.unlink()
                deleted.append(candidate)
            except OSError as exc:
                errors.append({"path": str(file_path), "error": str(exc)})

        for root in {
            settings.output_dir,
            settings.remotion_public_dir / "generated",
            settings.project_root / ".cache",
            settings.remotion_root / ".cache",
        }:
            prune_empty_dirs(root)

    total_bytes = sum(int(item["size_bytes"]) for item in candidates)
    deleted_bytes = sum(int(item["size_bytes"]) for item in deleted)
    return {
        "dry_run": dry_run,
        "candidate_count": len(candidates),
        "candidate_bytes": total_bytes,
        "candidate_mb": bytes_to_mb(total_bytes),
        "deleted_count": len(deleted),
        "deleted_bytes": deleted_bytes,
        "deleted_mb": bytes_to_mb(deleted_bytes),
        "errors": errors,
        "items": candidates if dry_run else deleted,
    }


def cleanup_database_logs(audit_retention_days: int, llm_retention_days: int) -> Dict[str, int]:
    """清理过期的 DB 日志表（审计日志 / LLM 调用日志），返回各表删除行数。"""
    return {
        "audit_deleted": delete_old_audit_logs(audit_retention_days),
        "llm_deleted": delete_old_llm_call_logs(llm_retention_days),
    }
