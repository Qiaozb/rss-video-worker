from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha1
from pathlib import Path
from queue import Queue
from time import monotonic
from threading import Event, Lock, Thread
from typing import Any, AsyncIterator, Dict, Literal, Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import requests

from app.auth import (
    COOKIE_NAME,
    auth_enabled,
    authenticate_user,
    create_session_token,
    current_user,
    current_username,
    has_role,
    hash_password,
    normalize_role,
)
from app.config import settings
from app.db import (
    add_pipeline_event,
    create_pipeline_run,
    create_auth_user,
    delete_model_config,
    delete_tts_voice,
    disable_rss_source,
    disable_schedule,
    delete_prompt_version,
    due_schedule_configs,
    enable_schedule,
    ensure_auth_tables,
    ensure_default_admin_user,
    ensure_model_config_table,
    ensure_tts_voice_table,
    ensure_llm_call_log_table,
    ensure_llm_raw_table,
    ensure_pipeline_tables,
    ensure_prompt_version_table,
    ensure_rss_report_tables,
    ensure_video_job_table,
    ensure_schedule_tables,
    get_latest_report_id,
    get_default_model_config,
    get_model_config,
    get_default_tts_voice,
    get_pipeline_run,
    get_prompt_version,
    get_report,
    get_schedule_config,
    get_tts_item,
    get_auth_user_by_id,
    get_video_job,
    get_video_job_by_report_id,
    hard_delete_rss_source,
    list_report_news,
    list_reports,
    list_pipeline_run_events_after,
    list_pipeline_run_events,
    list_pipeline_runs,
    list_llm_call_logs,
    llm_call_stats,
    list_model_configs,
    list_tts_audio_assets,
    list_tts_voices,
    list_prompt_versions,
    list_schedule_configs,
    load_tts_items,
    list_video_jobs,
    mark_report_news_published,
    mark_schedule_started,
    finish_pipeline_run,
    recover_unfinished_pipeline_runs,
    recover_pipeline_run_report,
    should_auto_publish,
    recover_unfinished_video_jobs,
    clear_video_job_assets,
    set_video_job_cancelled,
    set_video_job_progress,
    set_tts_item_done,
    set_tts_item_failed,
    set_tts_item_pending,
    set_video_job_cover_path,
    set_video_job_done,
    set_video_job_failed,
    list_rss_sources,
    list_audit_logs,
    list_auth_users,
    start_pipeline_run,
    insert_audit_log,
    mark_auth_user_login,
    update_pipeline_run,
    update_report_news,
    update_auth_user,
    update_auth_user_password,
    upsert_model_config,
    upsert_tts_voice,
    upsert_prompt_version,
    upsert_schedule_config,
    upsert_rss_source,
    upsert_video_job,
    bump_auth_user_session_version,
    delete_old_audit_logs,
    delete_old_llm_call_logs,
    list_stale_pipeline_runs,
    list_stale_video_jobs,
)
from app.ffmpeg_template import render_ffmpeg_template
from app.remotion import cancel_all_renders, cancel_render, render_video
from app.progress import ProgressThrottler
from app.pipeline import analyze_recent_rss_items, report_title_fallback
from app.llm import LLMConfigurationError, test_model_config
from app.maintenance import cleanup_artifacts, cleanup_database_logs, maintenance_summary
from app.rss import collect_enabled_sources, collect_one_source, preview_source
from app.tts import TTSClient, audio_duration_seconds, normalize_tts_text
from app.video_assets import (
    file_info,
    generate_cover_images,
    is_within,
    remove_report_assets,
    report_output_dir,
    safe_output_path,
)

app = FastAPI(title="Game Daily Video Worker")


def _fallback_title_from_report(report_id: int) -> str:
    """从 DB 读取报告元数据生成回退标题，避免硬编码游戏语义。"""
    report = get_report(report_id)
    if report:
        return report_title_fallback(
            report.get("report_type") or "general",
            report.get("rss_category"),
        )
    return report_title_fallback("general", None)


def _domain_label(report_type: str, rss_category: str | None) -> str:
    """返回适合口播的领域名称（如'游戏行业'、'科技行业'）。"""
    from app.pipeline import _report_title_prefix
    return _report_title_prefix(report_type, rss_category)


WEB_DIR = settings.project_root / "web"
if WEB_DIR.exists():
    app.mount("/console", StaticFiles(directory=WEB_DIR, html=True), name="console")

TTS_CACHE_VERSION = "neutral-v1"
_render_queue: Queue[Optional[int]] = Queue()
_render_state_lock = Lock()
_scheduled_reports: set[int] = set()
_cancelled_reports: set[int] = set()
_shutdown_event = Event()
_render_worker_thread: Optional[Thread] = None
_scheduler_thread: Optional[Thread] = None
_pipeline_lock = Lock()
_pipeline_cancel_lock = Lock()
_cancelled_pipeline_runs: set[int] = set()
_maintenance_lock = Lock()
_last_database_log_cleanup_at = 0.0


PUBLIC_PATH_PREFIXES = (
    "/console",
    "/docs",
    "/redoc",
)
PUBLIC_PATHS = {
    "/",
    "/health",
    "/openapi.json",
    "/favicon.ico",
    "/auth/me",
    "/auth/login",
    "/auth/logout",
}


class RenderJobCancelled(RuntimeError):
    pass


class PipelineTaskCancelled(RuntimeError):
    pass


PIPELINE_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
VIDEO_JOB_TERMINAL_STATUSES = {"done", "failed", "cancelled"}


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=4096)


class AuthUserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=4096)
    role: Literal["admin", "editor", "viewer"] = "viewer"
    enabled: bool = True


class AuthUserUpdateRequest(BaseModel):
    role: Literal["admin", "editor", "viewer"]
    enabled: bool = True


class AuthUserPasswordRequest(BaseModel):
    password: str = Field(min_length=8, max_length=4096)


class RSSSourceUpsertRequest(BaseModel):
    id: Optional[int] = Field(default=None, ge=1)
    name: str = Field(min_length=1, max_length=255)
    url: str = Field(min_length=1, max_length=2048)
    category: Optional[str] = Field(default=None, max_length=100)
    language: str = Field(default="zh-CN", max_length=20)
    priority: int = 100
    enabled: bool = True
    request_timeout_seconds: int = Field(default=20, ge=1, le=120)


class AnalyzeRSSRequest(BaseModel):
    hours: int = Field(default=24, ge=1, le=168)
    limit: int = Field(default=120, ge=1, le=500)
    rss_category: Optional[str] = Field(default=None, max_length=100)
    model_config_id: Optional[int] = Field(default=None, ge=1)
    prompt_version_id: Optional[int] = Field(default=None, ge=1)
    report_type: str = Field(default="general", max_length=50)
    auto_render: bool = True
    auto_publish: bool = False
    render_engine: Literal["remotion", "ffmpeg"] = "remotion"


class PipelineRunRequest(BaseModel):
    """手动按指定组合运行一次分析任务（阶段 8.2）。"""

    task_type: Literal["rss_analyze", "collect_and_analyze", "daily_report"] = "daily_report"
    hours: int = Field(default=24, ge=1, le=168)
    limit: int = Field(default=120, ge=1, le=500)
    rss_category: Optional[str] = Field(default=None, max_length=100)
    model_config_id: Optional[int] = Field(default=None, ge=1)
    prompt_version_id: Optional[int] = Field(default=None, ge=1)
    report_type: str = Field(default="general", max_length=50)
    auto_render: bool = True
    auto_publish: bool = False
    render_engine: Literal["remotion", "ffmpeg"] = "remotion"


class ScheduleUpsertRequest(BaseModel):
    id: Optional[int] = Field(default=None, ge=1)
    name: str = Field(min_length=1, max_length=100)
    task_type: Literal["rss_collect", "daily_report"]
    cron_expression: str = Field(min_length=1, max_length=100)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)
    enabled: bool = True
    prevent_overlap: bool = True
    max_runtime_seconds: int = Field(default=3600, ge=60, le=86400)
    retry_count: int = Field(default=2, ge=0, le=10)
    retry_interval_seconds: int = Field(default=300, ge=10, le=86400)
    rss_category: Optional[str] = Field(default=None, max_length=100)
    model_config_id: Optional[int] = Field(default=None, ge=1)
    prompt_version_id: Optional[int] = Field(default=None, ge=1)
    tts_config_id: Optional[int] = Field(default=None, ge=1)
    report_type: str = Field(default="general", max_length=50)
    auto_render: bool = True
    auto_publish: bool = False
    render_engine: Literal["remotion", "ffmpeg"] = "remotion"


class ModelConfigUpsertRequest(BaseModel):
    id: Optional[int] = Field(default=None, ge=1)
    name: str = Field(min_length=1, max_length=100)
    provider: str = Field(default="openai-compatible", max_length=50)
    base_url: str = Field(min_length=1, max_length=1024)
    model_name: str = Field(default="", max_length=255)
    api_key: Optional[str] = Field(default=None, max_length=4096)
    timeout_seconds: int = Field(default=180, ge=10, le=600)
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_retries: int = Field(default=2, ge=0, le=10)
    enabled: bool = True
    is_default: bool = False
    model_type: Literal["llm", "tts"] = "llm"
    voice: Optional[str] = Field(default=None, max_length=100)


class TTSVoiceUpsertRequest(BaseModel):
    id: Optional[int] = Field(default=None, ge=1)
    name: str = Field(min_length=1, max_length=100)
    language_code: str = Field(default="zh-CN", min_length=1, max_length=20)
    voice: str = Field(min_length=1, max_length=100)
    model_config_id: Optional[int] = Field(default=None, ge=1)
    sample_text: str = Field(default="大家好，欢迎收看今天的新闻播报。", min_length=1, max_length=500)
    enabled: bool = True
    is_default: bool = False


class PromptVersionUpsertRequest(BaseModel):
    id: Optional[int] = Field(default=None, ge=1)
    name: str = Field(min_length=1, max_length=100)
    system_prompt: str = Field(min_length=1)
    user_prompt_template: str = Field(min_length=1)
    enabled: bool = True
    is_default: bool = False


class ReportNewsUpdateRequest(BaseModel):
    item_index: int = Field(ge=1, le=1000)
    title: str = Field(min_length=1, max_length=500)
    pubdate: Optional[str] = Field(default=None, max_length=100)
    summary: str = Field(default="", max_length=20000)
    related_field: Optional[str] = Field(default=None, max_length=255)
    importance: Literal["高", "中", "低"] = "中"
    reserve_reason: Optional[str] = Field(default=None, max_length=20000)
    link: Optional[str] = Field(default=None, max_length=2048)
    voiceover_script: Optional[str] = Field(default=None, max_length=50000)


class MaintenanceCleanupRequest(BaseModel):
    dry_run: bool = True
    output_retention_days: int = Field(default=30, ge=0, le=3650)
    audio_retention_days: int = Field(default=14, ge=0, le=3650)
    cache_retention_days: int = Field(default=7, ge=0, le=3650)


def _tts_config_id_from_report(report_meta: Dict[str, Any]) -> Optional[int]:
    """从报告元数据中提取 tts_config_id：优先直接字段，其次通过 pipeline_run_id 查询。"""
    if report_meta.get("tts_config_id"):
        return int(report_meta["tts_config_id"])
    pipeline_run_id = report_meta.get("pipeline_run_id")
    if pipeline_run_id:
        try:
            run = get_pipeline_run(int(pipeline_run_id))
            if run and run.get("tts_config_id"):
                return int(run["tts_config_id"])
        except Exception:
            pass
    return None


def _resolve_tts_client(tts_config_id: Optional[int] = None) -> "TTSClient":
    """获取 TTS 客户端：优先使用指定 TTS 配置，其次默认 TTS 配置，回退到环境变量。"""
    client: Optional[TTSClient] = None
    selected_config_id: Optional[int] = None
    config_has_voice = False
    if tts_config_id:
        try:
            config = get_model_config(tts_config_id)
            if config and config.model_type == "tts":
                client = TTSClient.from_config(config)
                selected_config_id = config.id
                config_has_voice = bool(config.voice)
        except Exception:
            pass
    if client is None:
        try:
            config = get_default_model_config(model_type="tts")
            if config:
                client = TTSClient.from_config(config)
                selected_config_id = config.id
                config_has_voice = bool(config.voice)
        except Exception:
            pass
    if client is None:
        client = TTSClient.from_settings()
    if not config_has_voice:
        try:
            voice = get_default_tts_voice(selected_config_id)
            if voice and voice.voice:
                client.voice = voice.voice
        except Exception:
            pass
    return client


def audio_cache_name(prefix: str, text: str) -> str:
    digest = sha1(f"{TTS_CACHE_VERSION}\n{text}".encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}.wav"


def _tts_audio_public_dir(report_id: int):
    audio_dir = settings.remotion_public_dir / "generated" / f"report_{report_id}"
    audio_dir.mkdir(parents=True, exist_ok=True)
    return audio_dir


def _tts_audio_filename(item) -> str:
    return audio_cache_name(f"audio_{item.item_index:03d}", item.narration)


def generate_tts_item_audio(item, force: bool = False) -> Dict[str, Any]:
    narration = item.narration
    if not narration:
        raise RuntimeError(f"TTS item has no narration: queue_id={item.id}")

    audio_dir = _tts_audio_public_dir(item.report_id)
    audio_filename = _tts_audio_filename(item)
    audio_path = audio_dir / audio_filename

    if force:
        set_tts_item_pending(item.id)

    try:
        if force or not audio_path.exists():
            report_meta = get_report(item.report_id) or {}
            duration = _resolve_tts_client(_tts_config_id_from_report(report_meta)).synthesize(
                narration,
                audio_path,
            )
        else:
            duration = audio_duration_seconds(audio_path)
        set_tts_item_done(item.id, str(audio_path))
        return {
            "queue_id": item.id,
            "report_id": item.report_id,
            "audio_path": str(audio_path),
            "audio_url": f"/tts-items/{item.id}/audio",
            "duration_seconds": duration,
            "status": "done",
            "generated": force or not item.tts_audio_path,
        }
    except Exception as exc:
        set_tts_item_failed(item.id, str(exc))
        raise


def _safe_tts_audio_path(item) -> Path:
    candidate = Path(item.tts_audio_path) if item.tts_audio_path else None
    if candidate is None:
        fallback = _tts_audio_public_dir(item.report_id) / _tts_audio_filename(item)
        candidate = fallback if fallback.exists() else None
    if candidate is None:
        raise HTTPException(status_code=404, detail="TTS audio has not been generated")

    resolved = candidate.expanduser().resolve()
    public_root = settings.remotion_public_dir.expanduser().resolve()
    if public_root not in [resolved, *resolved.parents]:
        raise HTTPException(status_code=403, detail="TTS audio path is outside public directory")
    if not resolved.exists():
        raise HTTPException(status_code=404, detail="TTS audio file not found")
    return resolved


def _tts_audio_file_info(path_value: Optional[str]) -> Dict[str, Any]:
    if not path_value:
        return {
            "exists": False,
            "path": None,
            "size_bytes": 0,
            "size_mb": 0,
            "duration_seconds": None,
        }
    try:
        path = Path(path_value).expanduser().resolve()
        public_root = settings.remotion_public_dir.expanduser().resolve()
        if not is_within(path, public_root):
            return {
                "exists": False,
                "path": path_value,
                "size_bytes": 0,
                "size_mb": 0,
                "duration_seconds": None,
                "error": "path outside public directory",
            }
    except Exception:
        return {
            "exists": False,
            "path": path_value,
            "size_bytes": 0,
            "size_mb": 0,
            "duration_seconds": None,
            "error": "invalid path",
        }
    if not path.exists() or not path.is_file():
        return {
            "exists": False,
            "path": str(path),
            "size_bytes": 0,
            "size_mb": 0,
            "duration_seconds": None,
        }
    stat = path.stat()
    return {
        "exists": True,
        "path": str(path),
        "size_bytes": stat.st_size,
        "size_mb": round(stat.st_size / 1024 / 1024, 2),
        "duration_seconds": round(audio_duration_seconds(path), 2),
    }


def _enrich_tts_audio_asset(item: Dict[str, Any]) -> Dict[str, Any]:
    audio = _tts_audio_file_info(item.get("tts_audio_path"))
    item["audio_file_exists"] = audio["exists"]
    item["audio_file_size_bytes"] = audio["size_bytes"]
    item["audio_file_size_mb"] = audio["size_mb"]
    item["audio_file_path"] = audio["path"]
    item["audio_duration_seconds"] = audio["duration_seconds"]
    item["audio_download_url"] = (
        f"/tts-items/{item['id']}/audio"
        if item.get("tts_status") == "done" and audio["exists"]
        else None
    )
    item["narration_preview"] = normalize_tts_text(
        item.get("voiceover_script")
        or item.get("tts_text")
        or item.get("summary")
        or item.get("news_title")
        or ""
    )
    return item


def _enrich_video_asset(item: Dict[str, Any]) -> Dict[str, Any]:
    video = file_info(item.get("video_path"))
    cover = file_info(item.get("cover_path"))
    report_id = int(item["report_id"])
    item["video_file_exists"] = video["exists"]
    item["video_file_size_bytes"] = video["size_bytes"]
    item["video_file_size_mb"] = video["size_mb"]
    item["video_file_path"] = video["path"]
    item["cover_file_exists"] = cover["exists"]
    item["cover_file_size_bytes"] = cover["size_bytes"]
    item["cover_file_size_mb"] = cover["size_mb"]
    item["cover_file_path"] = cover["path"]
    item["video_download_url"] = f"/videos/{report_id}" if video["exists"] else None
    item["cover_download_url"] = f"/video-assets/{report_id}/cover" if cover["exists"] else None
    cover_4x3_path = report_output_dir(report_id) / "cover_4x3.jpg"
    item["cover_4x3_file_exists"] = cover_4x3_path.exists()
    item["cover_4x3_download_url"] = (
        f"/video-assets/{report_id}/cover/4x3"
        if cover_4x3_path.exists()
        else None
    )
    return item


def _is_cancelled(report_id: int) -> bool:
    with _render_state_lock:
        return report_id in _cancelled_reports or _shutdown_event.is_set()


def _raise_if_cancelled(report_id: int) -> None:
    if _is_cancelled(report_id):
        raise RenderJobCancelled(f"Render cancelled for report_id={report_id}")


def _request_pipeline_cancel(run_id: int) -> None:
    with _pipeline_cancel_lock:
        _cancelled_pipeline_runs.add(run_id)


def _is_pipeline_cancelled(run_id: int) -> bool:
    with _pipeline_cancel_lock:
        return run_id in _cancelled_pipeline_runs or _shutdown_event.is_set()


def _clear_pipeline_cancel(run_id: int) -> None:
    with _pipeline_cancel_lock:
        _cancelled_pipeline_runs.discard(run_id)


def _raise_if_pipeline_cancelled(run_id: int) -> None:
    if _is_pipeline_cancelled(run_id):
        raise PipelineTaskCancelled(f"Pipeline cancelled for run_id={run_id}")


def _sse_message(event: str, data: Dict[str, Any], event_id: Optional[int] = None) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, ensure_ascii=False, default=str)}")
    lines.append("")
    return "\n".join(lines) + "\n"


async def _pipeline_run_event_stream(
    request: Request,
    run_id: int,
    poll_interval_seconds: float = 1.0,
) -> AsyncIterator[str]:
    last_event_id = 0
    run = get_pipeline_run(run_id)
    if not run:
        yield _sse_message("error", {"detail": f"Pipeline run not found: {run_id}"})
        return

    events = list_pipeline_run_events(run_id, limit=200)
    if events:
        last_event_id = max(int(item["id"]) for item in events)
    yield _sse_message(
        "snapshot",
        {
            "run": run,
            "events": events,
        },
        event_id=last_event_id or None,
    )

    while run.get("status") not in PIPELINE_TERMINAL_STATUSES:
        await asyncio.sleep(poll_interval_seconds)
        if await request.is_disconnected():
            break

        run = get_pipeline_run(run_id)
        if not run:
            yield _sse_message("error", {"detail": f"Pipeline run not found: {run_id}"})
            break

        new_events = list_pipeline_run_events_after(run_id, last_event_id, limit=200)
        if new_events:
            last_event_id = max(int(item["id"]) for item in new_events)

        yield _sse_message(
            "update",
            {
                "run": run,
                "events": new_events,
            },
            event_id=last_event_id or None,
        )


async def _video_job_event_stream(
    request: Request,
    job_id: int,
    poll_interval_seconds: float = 1.0,
) -> AsyncIterator[str]:
    last_signature = ""
    job = get_video_job(job_id)
    if not job:
        yield _sse_message("error", {"detail": f"Video job not found: {job_id}"})
        return

    last_signature = json.dumps(job, ensure_ascii=False, default=str, sort_keys=True)
    yield _sse_message("snapshot", {"job": job}, event_id=int(job["id"]))

    while job.get("status") not in VIDEO_JOB_TERMINAL_STATUSES:
        await asyncio.sleep(poll_interval_seconds)
        if await request.is_disconnected():
            break

        job = get_video_job(job_id)
        if not job:
            yield _sse_message("error", {"detail": f"Video job not found: {job_id}"})
            break

        signature = json.dumps(job, ensure_ascii=False, default=str, sort_keys=True)
        if signature == last_signature:
            continue
        last_signature = signature
        yield _sse_message("update", {"job": job}, event_id=int(job["id"]))


def _is_public_path(path: str) -> bool:
    return path in PUBLIC_PATHS or any(path.startswith(prefix) for prefix in PUBLIC_PATH_PREFIXES)


def _origin_allowed(request: Request) -> bool:
    """校验请求来源与本机 host 一致，用于 CSRF 防护。"""
    from urllib.parse import urlsplit

    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        # 无 Origin/Referer：放行由登录态兜底，避免误伤不带这些头的合法程序化调用。
        return True
    host = urlsplit(origin).netloc.lower()
    if not host:
        return False
    request_host = (request.headers.get("host") or "").lower()
    return host == request_host


def _required_role(method: str, path: str) -> str:
    if path.startswith("/auth/users") or path.startswith("/auth/audit-logs"):
        return "admin"
    if path.startswith("/model-configs"):
        return "admin" if method != "GET" else "viewer"
    if path.startswith("/tts-voices"):
        return "admin" if method != "GET" else "viewer"
    if path.startswith("/maintenance/cleanup"):
        return "admin"
    if method in {"GET", "HEAD", "OPTIONS"}:
        return "viewer"
    return "editor"


def _audit_action(method: str, path: str) -> str:
    if path.startswith("/auth/users"):
        return "auth_user_manage"
    if path.startswith("/model-configs"):
        return "model_config_manage"
    if path.startswith("/tts-voices"):
        return "tts_voice_manage"
    if path.startswith("/prompt-versions"):
        return "prompt_manage"
    if path.startswith("/rss/"):
        return "rss_manage"
    if path.startswith("/pipeline/") or path.startswith("/runs/"):
        return "pipeline_manage"
    if path.startswith("/schedules"):
        return "schedule_manage"
    if path.startswith("/reports"):
        return "report_manage"
    if path.startswith("/render") or path.startswith("/jobs/") or path.startswith("/video-assets"):
        return "video_manage"
    if path.startswith("/tts-items"):
        return "tts_manage"
    if path.startswith("/maintenance"):
        return "maintenance_manage"
    return "api_mutation"


def _client_ip(request: Request) -> Optional[str]:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.client.host if request.client else None


def _write_audit_log(
    request: Request,
    *,
    action: str,
    status_code: Optional[int] = None,
    message: Optional[str] = None,
) -> None:
    user = getattr(request.state, "auth_user", None) or current_user(request)
    try:
        insert_audit_log(
            username=str(user.get("username")) if user else None,
            role=str(user.get("role")) if user else None,
            action=action,
            method=request.method,
            path=request.url.path,
            status_code=status_code,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            message=message,
        )
    except Exception:
        pass


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if not auth_enabled() or _is_public_path(path):
        return await call_next(request)

    # CSRF：对状态变更方法校验 Origin/Referer，阻止跨站表单伪造（配合 SameSite=Lax）。
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not _origin_allowed(request):
        return JSONResponse({"detail": "跨站请求被拒绝"}, status_code=403)

    user = current_user(request)
    request.state.auth_user = user
    if not user:
        return JSONResponse({"detail": "未登录或登录已过期"}, status_code=401)

    required_role = _required_role(request.method, path)
    if not has_role(str(user.get("role") or "viewer"), required_role):
        _write_audit_log(
            request,
            action="permission_denied",
            status_code=403,
            message=f"required_role={required_role}",
        )
        return JSONResponse({"detail": "没有权限执行此操作"}, status_code=403)

    response = await call_next(request)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        _write_audit_log(request, action=_audit_action(request.method, path), status_code=response.status_code)
    return response


def _render_worker_loop() -> None:
    while True:
        report_id = _render_queue.get()
        if report_id is None:
            _render_queue.task_done()
            return

        try:
            try:
                if _is_cancelled(report_id):
                    set_video_job_cancelled(report_id)
                else:
                    render_report_job(report_id)
            except Exception as exc:
                # Keep the single consumer alive even if bookkeeping fails outside
                # render_report_job's own error boundary.
                try:
                    set_video_job_failed(report_id, str(exc))
                except Exception:
                    pass
        finally:
            with _render_state_lock:
                _scheduled_reports.discard(report_id)
                _cancelled_reports.discard(report_id)
            _render_queue.task_done()


RenderEngine = Literal["remotion", "ffmpeg"]


def _normalize_render_engine(value: Optional[str]) -> RenderEngine:
    normalized = (value or "remotion").strip().lower()
    if normalized in {"ffmpeg", "template", "ffmpeg_template", "fast"}:
        return "ffmpeg"
    return "remotion"


def _schedule_report(
    report_id: int,
    title: str,
    item_count: int,
    render_engine: RenderEngine = "remotion",
) -> tuple[int, bool]:
    with _render_state_lock:
        if report_id in _scheduled_reports:
            existing = get_video_job_by_report_id(report_id)
            if existing is None:
                raise RuntimeError(f"Scheduled report has no video job: report_id={report_id}")
            return int(existing["id"]), False
        _scheduled_reports.add(report_id)

    try:
        job_id = upsert_video_job(report_id, title, "pending", item_count, render_engine=render_engine)
        _render_queue.put(report_id)
        return job_id, True
    except Exception:
        with _render_state_lock:
            _scheduled_reports.discard(report_id)
        raise


@dataclass
class PipelineTaskConfig:
    """一次分析任务的完整可配置上下文，贯穿 pipeline_run → 采集 → LLM → 报告 → 渲染 → 发布。"""

    hours: int = 24
    limit: int = 120
    rss_category: Optional[str] = None
    model_config_id: Optional[int] = None
    prompt_version_id: Optional[int] = None
    tts_config_id: Optional[int] = None
    report_type: str = "general"
    auto_render: bool = True
    auto_publish: bool = False
    render_engine: RenderEngine = "remotion"


def _execute_pipeline_task(
    run_id: int,
    task_type: str,
    config: PipelineTaskConfig,
) -> Dict[str, Any]:
    _raise_if_pipeline_cancelled(run_id)
    if task_type == "rss_collect":
        update_pipeline_run(run_id, 5, "开始采集 RSS")
        cat_label = f"（分类：{config.rss_category}）" if config.rss_category else ""
        add_pipeline_event(run_id, "rss_collect", f"开始采集 RSS 源{cat_label}", progress_percent=5)
        _raise_if_pipeline_cancelled(run_id)
        collect_result = collect_enabled_sources(category=config.rss_category)
        _raise_if_pipeline_cancelled(run_id)
        update_pipeline_run(
            run_id,
            100,
            "RSS 采集完成",
            processed_count=collect_result.get("inserted_count", 0),
            total_count=collect_result.get("fetched_count", 0),
        )
        add_pipeline_event(
            run_id,
            "rss_collect",
            (
                f"采集完成：获取 {collect_result.get('fetched_count', 0)} 条，"
                f"新增 {collect_result.get('inserted_count', 0)} 条，"
                f"重复 {collect_result.get('duplicate_count', 0)} 条"
            ),
            progress_percent=100,
        )
        return {"task_type": task_type, "collect": collect_result}

    if task_type in {"rss_analyze", "collect_and_analyze", "daily_report"}:
        collect_result: Optional[Dict[str, Any]] = None
        if task_type in {"collect_and_analyze", "daily_report"}:
            update_pipeline_run(run_id, 5, "开始采集 RSS")
            cat_label = f"（分类：{config.rss_category}）" if config.rss_category else ""
            add_pipeline_event(run_id, "rss_collect", f"开始采集 RSS 源{cat_label}", progress_percent=5)
            _raise_if_pipeline_cancelled(run_id)
            collect_result = collect_enabled_sources(category=config.rss_category)
            _raise_if_pipeline_cancelled(run_id)
            add_pipeline_event(
                run_id,
                "rss_collect",
                (
                    f"采集完成：获取 {collect_result.get('fetched_count', 0)} 条，"
                    f"新增 {collect_result.get('inserted_count', 0)} 条，"
                    f"重复 {collect_result.get('duplicate_count', 0)} 条"
                ),
                progress_percent=30,
            )

        update_pipeline_run(run_id, 35, "开始调用大模型分析 RSS")
        add_pipeline_event(
            run_id,
            "llm_analyze",
            (
                f"开始分析最近 {config.hours} 小时的 RSS 新闻，最多 {config.limit} 条候选"
                + (f"（分类：{config.rss_category}）" if config.rss_category else "")
            ),
            progress_percent=35,
        )
        _raise_if_pipeline_cancelled(run_id)
        analyze_result = analyze_recent_rss_items(
            hours=config.hours,
            limit=config.limit,
            pipeline_run_id=run_id,
            rss_category=config.rss_category,
            model_config_id=config.model_config_id,
            prompt_version_id=config.prompt_version_id,
            report_type=config.report_type,
        )
        _raise_if_pipeline_cancelled(run_id)
        update_pipeline_run(
            run_id,
            75,
            "大模型分析完成",
            processed_count=analyze_result.selected_count,
            total_count=analyze_result.candidate_count,
            report_id=analyze_result.report_id,
        )
        add_pipeline_event(
            run_id,
            "llm_analyze",
            (
                f"分析完成：候选 {analyze_result.candidate_count} 条，"
                f"筛选 {analyze_result.selected_count} 条，report_id={analyze_result.report_id}"
            ),
            progress_percent=75,
        )

        render_result: Optional[Dict[str, Any]] = None
        if task_type == "daily_report" and config.auto_render and analyze_result.selected_count > 0:
            _raise_if_pipeline_cancelled(run_id)
            update_pipeline_run(run_id, 85, "创建视频渲染任务", report_id=analyze_result.report_id)
            items = load_tts_items(analyze_result.report_id)
            if items:
                _raise_if_pipeline_cancelled(run_id)
                title = items[0].report_title or report_title_fallback(
                    config.report_type, config.rss_category
                )
                job_id, scheduled = _schedule_report(
                    analyze_result.report_id,
                    title,
                    len(items),
                    render_engine=config.render_engine,
                )
                render_result = {
                    "job_id": job_id,
                    "report_id": analyze_result.report_id,
                    "scheduled": scheduled,
                }
                add_pipeline_event(
                    run_id,
                    "render",
                    f"视频渲染任务已创建：job_id={job_id}",
                    progress_percent=95,
                )
            else:
                add_pipeline_event(
                    run_id,
                    "render",
                    "没有可播报的高/中重要性新闻，跳过视频渲染",
                    level="warning",
                    progress_percent=95,
                )
        elif task_type == "daily_report" and not config.auto_render:
            add_pipeline_event(
                run_id,
                "render",
                "auto_render 关闭，跳过视频渲染",
                level="warning",
                progress_percent=95,
            )
        elif task_type == "daily_report":
            add_pipeline_event(
                run_id,
                "render",
                "没有筛选到相关新闻，跳过视频渲染",
                level="warning",
                progress_percent=95,
            )

        return {
            "task_type": task_type,
            "collect": collect_result,
            "analyze": {
                "raw_id": analyze_result.raw_id,
                "report_id": analyze_result.report_id,
                "candidate_count": analyze_result.candidate_count,
                "selected_count": analyze_result.selected_count,
            },
            "render": render_result,
        }

    raise RuntimeError(f"Unsupported schedule task_type={task_type}")


def _run_pipeline_task(
    run_id: int,
    task_type: str,
    config: PipelineTaskConfig,
    prevent_overlap: bool = True,
    retry_count: int = 0,
    retry_interval_seconds: int = 300,
) -> None:
    acquired = False
    try:
        _raise_if_pipeline_cancelled(run_id)
        if prevent_overlap:
            acquired = _pipeline_lock.acquire(blocking=False)
            if not acquired:
                add_pipeline_event(
                    run_id,
                    "overlap",
                    "已有 pipeline 正在运行，本次任务被跳过",
                    level="warning",
                )
                finish_pipeline_run(run_id, "cancelled", "任务因防重入被跳过", 0)
                return
        else:
            _pipeline_lock.acquire()
            acquired = True
        _raise_if_pipeline_cancelled(run_id)
        start_pipeline_run(run_id)
        add_pipeline_event(run_id, "start", f"开始执行任务：{task_type}", progress_percent=0)

        attempts = max(0, retry_count) + 1
        for attempt in range(1, attempts + 1):
            try:
                if attempt > 1:
                    update_pipeline_run(run_id, 0, f"开始第 {attempt}/{attempts} 次尝试")
                    add_pipeline_event(
                        run_id,
                        "retry",
                        f"开始第 {attempt}/{attempts} 次尝试",
                        level="warning",
                        progress_percent=0,
                    )

                result = _execute_pipeline_task(run_id, task_type, config)
                _raise_if_pipeline_cancelled(run_id)
                report_id = None
                analyze = result.get("analyze")
                if isinstance(analyze, dict):
                    report_id = analyze.get("report_id")
                finish_pipeline_run(run_id, "succeeded", "任务执行完成", 100, report_id=report_id)
                add_pipeline_event(run_id, "finish", "任务执行完成", progress_percent=100)
                return
            except PipelineTaskCancelled:
                raise
            except Exception as exc:
                if attempt >= attempts:
                    raise
                add_pipeline_event(
                    run_id,
                    "retry",
                    f"第 {attempt}/{attempts} 次尝试失败：{exc}；{retry_interval_seconds} 秒后重试",
                    level="warning",
                )
                update_pipeline_run(run_id, 0, f"等待重试：{retry_interval_seconds} 秒")
                for _ in range(max(0, retry_interval_seconds)):
                    _raise_if_pipeline_cancelled(run_id)
                    if _shutdown_event.wait(1):
                        _raise_if_pipeline_cancelled(run_id)
    except PipelineTaskCancelled:
        add_pipeline_event(run_id, "cancel", "任务已取消", level="warning")
        finish_pipeline_run(run_id, "cancelled", "任务已取消", 0)
    except Exception as exc:
        add_pipeline_event(run_id, "error", str(exc), level="error")
        finish_pipeline_run(run_id, "failed", "任务执行失败", 99, error_message=str(exc))
        print(f"Pipeline task failed: task_type={task_type}, error={exc}", flush=True)
    finally:
        if acquired:
            _pipeline_lock.release()
        _clear_pipeline_cancel(run_id)


def _config_from_schedule(schedule: ScheduleConfig) -> PipelineTaskConfig:
    return PipelineTaskConfig(
        rss_category=schedule.rss_category,
        model_config_id=schedule.model_config_id,
        prompt_version_id=schedule.prompt_version_id,
        tts_config_id=schedule.tts_config_id,
        report_type=schedule.report_type or "general",
        auto_render=bool(schedule.auto_render),
        auto_publish=bool(schedule.auto_publish),
        render_engine=_normalize_render_engine(schedule.render_engine),
    )


def _config_from_run_row(row: Dict[str, Any]) -> PipelineTaskConfig:
    """从 pipeline_runs 行（含 task_payload 与冗余列）还原任务配置，供重试/恢复复用。"""
    payload = _pipeline_payload(row)
    return PipelineTaskConfig(
        hours=payload["hours"],
        limit=payload["limit"],
        rss_category=payload["rss_category"],
        model_config_id=payload["model_config_id"],
        prompt_version_id=payload["prompt_version_id"],
        tts_config_id=payload["tts_config_id"],
        report_type=payload["report_type"],
        auto_render=payload["auto_render"],
        auto_publish=payload["auto_publish"],
        render_engine=payload["render_engine"],
    )


def _start_pipeline_task(
    task_type: str,
    config: PipelineTaskConfig,
    trigger_type: str = "manual",
    schedule_id: Optional[int] = None,
    prevent_overlap: bool = True,
    retry_count: int = 0,
    retry_interval_seconds: int = 300,
) -> int:
    run_id = create_pipeline_run(
        task_type=task_type,
        trigger_type=trigger_type,
        schedule_id=schedule_id,
        current_step="任务已排队",
        task_payload={
            "hours": config.hours,
            "limit": config.limit,
            "rss_category": config.rss_category,
            "model_config_id": config.model_config_id,
            "prompt_version_id": config.prompt_version_id,
            "tts_config_id": config.tts_config_id,
            "report_type": config.report_type,
            "auto_render": config.auto_render,
            "auto_publish": config.auto_publish,
            "render_engine": config.render_engine,
            "prevent_overlap": prevent_overlap,
            "retry_count": retry_count,
            "retry_interval_seconds": retry_interval_seconds,
        },
        rss_category=config.rss_category,
        model_config_id=config.model_config_id,
        prompt_version_id=config.prompt_version_id,
        tts_config_id=config.tts_config_id,
        report_type=config.report_type,
    )
    Thread(
        target=_run_pipeline_task,
        args=(run_id, task_type, config, prevent_overlap, retry_count, retry_interval_seconds),
        name=f"pipeline-{task_type}",
        daemon=True,
    ).start()
    return run_id


def _pipeline_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    raw_payload = row.get("task_payload") or {}
    if isinstance(raw_payload, str):
        try:
            parsed = json.loads(raw_payload)
        except json.JSONDecodeError:
            parsed = {}
    elif isinstance(raw_payload, dict):
        parsed = raw_payload
    else:
        parsed = {}

    return {
        "prevent_overlap": bool(parsed.get("prevent_overlap", True)),
        "hours": int(parsed.get("hours", 24) or 24),
        "limit": int(parsed.get("limit", 120) or 120),
        "retry_count": int(parsed.get("retry_count", 0) or 0),
        "retry_interval_seconds": int(parsed.get("retry_interval_seconds", 300) or 300),
        "rss_category": parsed.get("rss_category") or row.get("rss_category"),
        "model_config_id": parsed.get("model_config_id") or row.get("model_config_id"),
        "prompt_version_id": parsed.get("prompt_version_id") or row.get("prompt_version_id"),
        "tts_config_id": parsed.get("tts_config_id") or row.get("tts_config_id"),
        "report_type": parsed.get("report_type") or row.get("report_type") or "general",
        "auto_render": bool(parsed.get("auto_render", True)),
        "auto_publish": bool(parsed.get("auto_publish", False)),
        "render_engine": _normalize_render_engine(parsed.get("render_engine")),
    }


def _run_recovered_pipeline_run(row: Dict[str, Any]) -> None:
    payload = _pipeline_payload(row)
    run_id = int(row["id"])
    config = PipelineTaskConfig(
        hours=payload["hours"],
        limit=payload["limit"],
        rss_category=payload["rss_category"],
        model_config_id=payload["model_config_id"],
        prompt_version_id=payload["prompt_version_id"],
        tts_config_id=payload["tts_config_id"],
        report_type=payload["report_type"],
        auto_render=payload["auto_render"],
        auto_publish=payload["auto_publish"],
        render_engine=payload["render_engine"],
    )
    add_pipeline_event(
        run_id,
        "recover",
        "服务启动后恢复未完成 Pipeline 任务",
        level="warning",
        progress_percent=0,
    )
    # 幂等恢复：若分析阶段已提交产出 report_id（崩溃发生在“分析已 commit、
    # run 未 finish”窗口），则不重跑分析，避免产生重复 report 与孤儿 video job；
    # 直接复用该 report 完成 run，并重新调度渲染。
    existing = recover_pipeline_run_report(run_id)
    existing_report_id = row.get("report_id") or (existing or {}).get("report_id")
    if existing_report_id:
        existing_report_id = int(existing_report_id)
        add_pipeline_event(
            run_id,
            "recover",
            f"检测到已产出 report_id={existing_report_id}，跳过重复分析，直接复用",
            progress_percent=85,
        )
        finish_pipeline_run(
            run_id,
            "succeeded",
            "任务恢复完成（复用已产出报告）",
            100,
            report_id=existing_report_id,
        )
        if row["task_type"] == "daily_report" and config.auto_render:
            items = load_tts_items(existing_report_id)
            if items:
                title = items[0].report_title or report_title_fallback(
                    config.report_type, config.rss_category
                )
                _schedule_report(
                    existing_report_id,
                    title,
                    len(items),
                    render_engine=config.render_engine,
                )
        return
    _run_pipeline_task(
        run_id,
        row["task_type"],
        config,
        payload["prevent_overlap"],
        payload["retry_count"],
        payload["retry_interval_seconds"],
    )


def _recover_pipeline_runs(rows: list[Dict[str, Any]]) -> None:
    for row in rows:
        if _shutdown_event.is_set():
            return
        _run_recovered_pipeline_run(row)


def _run_pipeline_task_now(
    task_type: str,
    config: PipelineTaskConfig,
    trigger_type: str = "manual",
) -> Dict[str, Any]:
    run_id = create_pipeline_run(
        task_type=task_type,
        trigger_type=trigger_type,
        current_step="任务已创建",
        task_payload={
            "hours": config.hours,
            "limit": config.limit,
            "rss_category": config.rss_category,
            "model_config_id": config.model_config_id,
            "prompt_version_id": config.prompt_version_id,
            "report_type": config.report_type,
            "auto_render": config.auto_render,
            "auto_publish": config.auto_publish,
            "prevent_overlap": True,
            "retry_count": 0,
            "retry_interval_seconds": 300,
        },
        rss_category=config.rss_category,
        model_config_id=config.model_config_id,
        prompt_version_id=config.prompt_version_id,
        report_type=config.report_type,
    )
    acquired = _pipeline_lock.acquire(blocking=False)
    if not acquired:
        add_pipeline_event(run_id, "overlap", "已有 pipeline 正在运行，本次任务被跳过", level="warning")
        finish_pipeline_run(run_id, "cancelled", "任务因防重入被跳过", 0)
        return {"run_id": run_id, "status": "cancelled"}

    try:
        _raise_if_pipeline_cancelled(run_id)
        start_pipeline_run(run_id)
        add_pipeline_event(run_id, "start", f"开始执行任务：{task_type}", progress_percent=0)
        result = _execute_pipeline_task(run_id, task_type, config)
        _raise_if_pipeline_cancelled(run_id)
        report_id = None
        analyze = result.get("analyze")
        if isinstance(analyze, dict):
            report_id = analyze.get("report_id")
        finish_pipeline_run(run_id, "succeeded", "任务执行完成", 100, report_id=report_id)
        add_pipeline_event(run_id, "finish", "任务执行完成", progress_percent=100)
        result["run_id"] = run_id
        return result
    except PipelineTaskCancelled:
        add_pipeline_event(run_id, "cancel", "任务已取消", level="warning")
        finish_pipeline_run(run_id, "cancelled", "任务已取消", 0)
        return {"run_id": run_id, "status": "cancelled"}
    except Exception as exc:
        add_pipeline_event(run_id, "error", str(exc), level="error")
        finish_pipeline_run(run_id, "failed", "任务执行失败", 99, error_message=str(exc))
        raise
    finally:
        _pipeline_lock.release()
        _clear_pipeline_cancel(run_id)


def _reclaim_stale_tasks() -> None:
    """回收心跳超时的僵尸任务，让心跳字段真正闭环。"""
    # 渲染任务心跳超时：杀进程组让卡死的 render 线程退出（stdout EOF），队列继续消费。
    for job in list_stale_video_jobs(settings.video_stale_seconds):
        report_id = int(job["report_id"])
        print(f"Reclaiming stale video job: report_id={report_id}", flush=True)
        try:
            cancel_render(report_id)
        except Exception:
            pass
        try:
            set_video_job_failed(report_id, "渲染心跳超时，任务已回收")
        except Exception:
            pass
    # pipeline 心跳超时：标记失败。pipeline 侧阻塞均有 I/O 超时自愈，此处仅作兜底。
    for run in list_stale_pipeline_runs(settings.pipeline_stale_seconds):
        run_id = int(run["id"])
        print(f"Reclaiming stale pipeline run: run_id={run_id}", flush=True)
        try:
            add_pipeline_event(run_id, "reclaim", "任务心跳超时，被调度器回收", level="error")
            finish_pipeline_run(
                run_id,
                "failed",
                "任务心跳超时被回收",
                99,
                error_message="heartbeat timeout",
            )
        except Exception:
            pass


def _cleanup_database_logs_if_due() -> None:
    global _last_database_log_cleanup_at
    if settings.audit_log_retention_days <= 0 and settings.llm_call_log_retention_days <= 0:
        return

    now = monotonic()
    with _maintenance_lock:
        if _last_database_log_cleanup_at and now - _last_database_log_cleanup_at < 24 * 3600:
            return
        _last_database_log_cleanup_at = now

    try:
        result = cleanup_database_logs(
            settings.audit_log_retention_days,
            settings.llm_call_log_retention_days,
        )
        deleted = int(result.get("audit_deleted") or 0) + int(result.get("llm_deleted") or 0)
        if deleted:
            print(f"Database log cleanup completed: {result}", flush=True)
    except Exception as exc:
        print(f"Database log cleanup failed: {exc}", flush=True)


def _scheduler_loop() -> None:
    while not _shutdown_event.is_set():
        try:
            for schedule in due_schedule_configs():
                mark_schedule_started(schedule)
                config = PipelineTaskConfig(
                    rss_category=schedule.rss_category,
                    model_config_id=schedule.model_config_id,
                    prompt_version_id=schedule.prompt_version_id,
                    tts_config_id=schedule.tts_config_id,
                    report_type=schedule.report_type or "general",
                    auto_render=bool(schedule.auto_render),
                    auto_publish=bool(schedule.auto_publish),
                    render_engine=_normalize_render_engine(schedule.render_engine),
                )
                _start_pipeline_task(
                    schedule.task_type,
                    config,
                    trigger_type="schedule",
                    schedule_id=schedule.id,
                    prevent_overlap=bool(schedule.prevent_overlap),
                    retry_count=schedule.retry_count,
                    retry_interval_seconds=schedule.retry_interval_seconds,
                )
            _reclaim_stale_tasks()
            _cleanup_database_logs_if_due()
        except Exception as exc:
            print(f"Scheduler tick failed: {exc}", flush=True)
        _shutdown_event.wait(30)


@app.on_event("startup")
def startup() -> None:
    global _render_worker_thread, _scheduler_thread
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    ensure_auth_tables()
    if settings.admin_password:
        ensure_default_admin_user(settings.admin_username, hash_password(settings.admin_password))
    ensure_llm_raw_table()
    ensure_video_job_table()
    ensure_schedule_tables()
    ensure_pipeline_tables()
    ensure_rss_report_tables()
    ensure_model_config_table()
    ensure_tts_voice_table()
    ensure_prompt_version_table()
    ensure_llm_call_log_table()
    _shutdown_event.clear()
    recovered_video_jobs = recover_unfinished_video_jobs()
    recovered_pipeline_runs = recover_unfinished_pipeline_runs()

    _render_worker_thread = Thread(
        target=_render_worker_loop,
        name="video-render-worker",
        daemon=True,
    )
    _render_worker_thread.start()

    for row in recovered_video_jobs:
        report_id = int(row["report_id"])
        with _render_state_lock:
            _scheduled_reports.add(report_id)
            _cancelled_reports.discard(report_id)
        _render_queue.put(report_id)

    if recovered_pipeline_runs:
        Thread(
            target=_recover_pipeline_runs,
            args=(recovered_pipeline_runs,),
            name="pipeline-recovery",
            daemon=True,
        ).start()

    _scheduler_thread = Thread(
        target=_scheduler_loop,
        name="pipeline-scheduler",
        daemon=True,
    )
    _scheduler_thread.start()


@app.on_event("shutdown")
def shutdown() -> None:
    _shutdown_event.set()
    with _render_state_lock:
        reports = list(_scheduled_reports)
        _cancelled_reports.update(reports)
    for report_id in reports:
        set_video_job_cancelled(report_id)
    cancel_all_renders()
    _render_queue.put(None)
    if _render_worker_thread is not None:
        _render_worker_thread.join(timeout=10)
    if _scheduler_thread is not None:
        _scheduler_thread.join(timeout=10)


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/maintenance/summary")
def maintenance() -> Dict[str, Any]:
    return maintenance_summary()


@app.post("/maintenance/cleanup")
def maintenance_cleanup(payload: MaintenanceCleanupRequest) -> Dict[str, Any]:
    result = cleanup_artifacts(
        output_retention_days=payload.output_retention_days,
        audio_retention_days=payload.audio_retention_days,
        cache_retention_days=payload.cache_retention_days,
        dry_run=payload.dry_run,
    )
    if not payload.dry_run:
        result["database_logs"] = cleanup_database_logs(
            settings.audit_log_retention_days,
            settings.llm_call_log_retention_days,
        )
    return result


@app.get("/auth/me")
def auth_me(request: Request) -> Dict[str, Any]:
    user = current_user(request)
    username = str(user["username"]) if user else None
    return {
        "auth_enabled": auth_enabled(),
        "authenticated": username is not None,
        "username": username,
        "role": str(user["role"]) if user else None,
    }


@app.post("/auth/login")
def login(payload: LoginRequest, response: Response) -> Any:
    username = payload.username.strip()
    if not auth_enabled():
        return {
            "auth_enabled": False,
            "authenticated": True,
            "username": settings.admin_username,
            "role": "admin",
        }
    user = authenticate_user(username, payload.password)
    if not user:
        insert_audit_log(
            username=username,
            role=None,
            action="login_failed",
            method="POST",
            path="/auth/login",
            status_code=401,
            message="用户名或密码错误",
        )
        response = JSONResponse({"detail": "用户名或密码错误"}, status_code=401)
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    response.set_cookie(
        key=COOKIE_NAME,
        value=create_session_token(str(user["username"]), int(user.get("session_version") or 0)),
        httponly=True,
        secure=settings.admin_cookie_secure,
        samesite="lax",
        max_age=settings.admin_session_hours * 3600,
        path="/",
    )
    mark_auth_user_login(str(user["username"]))
    insert_audit_log(
        username=str(user["username"]),
        role=str(user["role"]),
        action="login_succeeded",
        method="POST",
        path="/auth/login",
        status_code=200,
    )
    return {
        "auth_enabled": True,
        "authenticated": True,
        "username": user["username"],
        "role": user["role"],
    }


@app.post("/auth/logout")
def logout(request: Request, response: Response) -> Dict[str, Any]:
    _write_audit_log(request, action="logout", status_code=200)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"status": "logged_out"}


@app.get("/auth/users")
def auth_users() -> Dict[str, Any]:
    items = list_auth_users()
    return {"count": len(items), "items": items}


@app.post("/auth/users")
def create_user(payload: AuthUserCreateRequest, request: Request) -> Dict[str, Any]:
    user_id = create_auth_user(
        payload.username.strip(),
        hash_password(payload.password),
        normalize_role(payload.role),
        payload.enabled,
    )
    _write_audit_log(request, action="auth_user_create", status_code=200, message=f"user_id={user_id}")
    return {"id": user_id, "status": "created"}


@app.put("/auth/users/{user_id}")
def update_user(user_id: int, payload: AuthUserUpdateRequest, request: Request) -> Dict[str, Any]:
    if not update_auth_user(user_id, normalize_role(payload.role), payload.enabled):
        raise HTTPException(status_code=404, detail="User not found")
    _write_audit_log(request, action="auth_user_update", status_code=200, message=f"user_id={user_id}")
    return {"id": user_id, "status": "updated"}


@app.post("/auth/users/{user_id}/password")
def update_user_password(user_id: int, payload: AuthUserPasswordRequest, request: Request) -> Dict[str, Any]:
    if not update_auth_user_password(user_id, hash_password(payload.password)):
        raise HTTPException(status_code=404, detail="User not found")
    _write_audit_log(request, action="auth_user_password_update", status_code=200, message=f"user_id={user_id}")
    return {"id": user_id, "status": "password_updated"}


@app.delete("/auth/users/{user_id}")
def disable_user(user_id: int, request: Request) -> Dict[str, Any]:
    user = get_auth_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if str(user["username"]) == current_username(request):
        raise HTTPException(status_code=400, detail="不能停用当前登录用户")
    if not update_auth_user(user_id, normalize_role(str(user["role"])), False):
        raise HTTPException(status_code=404, detail="User not found")
    bump_auth_user_session_version(user_id)
    _write_audit_log(request, action="auth_user_disable", status_code=200, message=f"user_id={user_id}")
    return {"id": user_id, "status": "disabled"}


@app.get("/auth/audit-logs")
def audit_logs(limit: int = 100) -> Dict[str, Any]:
    items = list_audit_logs(limit=limit)
    return {"count": len(items), "items": items}


@app.get("/")
def root():
    return RedirectResponse(url="/console/")


@app.get("/rss/sources")
def rss_sources(enabled_only: bool = False) -> Dict[str, Any]:
    sources = list_rss_sources(enabled_only=enabled_only)
    return {
        "count": len(sources),
        "sources": sources,
    }


@app.get("/model-configs")
def model_configs(model_type: Optional[str] = None) -> Dict[str, Any]:
    items = list_model_configs(model_type=model_type)
    return {
        "count": len(items),
        "model_configs": items,
    }


@app.post("/model-configs")
def save_model_config(payload: ModelConfigUpsertRequest) -> Dict[str, Any]:
    config_id = upsert_model_config(
        name=payload.name,
        provider=payload.provider,
        base_url=payload.base_url,
        model_name=payload.model_name,
        api_key=payload.api_key,
        timeout_seconds=payload.timeout_seconds,
        temperature=payload.temperature,
        max_retries=payload.max_retries,
        enabled=payload.enabled,
        is_default=payload.is_default,
        model_type=payload.model_type,
        voice=payload.voice,
        config_id=payload.id,
    )
    return {
        "id": config_id,
        "status": "saved",
        "updated": payload.id is not None,
    }


@app.delete("/model-configs/{config_id}")
def delete_model_config_endpoint(config_id: int) -> Dict[str, Any]:
    try:
        deleted = delete_model_config(config_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Model config not found: {config_id}")
    return {
        "id": config_id,
        "status": "deleted",
    }


@app.get("/tts-voices")
def tts_voices(enabled_only: bool = False, model_config_id: Optional[int] = None) -> Dict[str, Any]:
    items = list_tts_voices(enabled_only=enabled_only, model_config_id=model_config_id)
    return {
        "count": len(items),
        "tts_voices": items,
    }


@app.post("/tts-voices")
def save_tts_voice(payload: TTSVoiceUpsertRequest) -> Dict[str, Any]:
    try:
        voice_id = upsert_tts_voice(
            name=payload.name,
            language_code=payload.language_code,
            voice=payload.voice,
            model_config_id=payload.model_config_id,
            sample_text=payload.sample_text,
            enabled=payload.enabled,
            is_default=payload.is_default,
            voice_id=payload.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "id": voice_id,
        "status": "saved",
        "updated": payload.id is not None,
    }


@app.delete("/tts-voices/{voice_id}")
def delete_tts_voice_endpoint(voice_id: int) -> Dict[str, Any]:
    deleted = delete_tts_voice(voice_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"TTS voice not found: {voice_id}")
    return {
        "id": voice_id,
        "status": "deleted",
    }


def _test_tts_voice_config(voice_row: Dict[str, Any]) -> Dict[str, Any]:
    import tempfile

    tts_config_id = voice_row.get("model_config_id")
    client = _resolve_tts_client(int(tts_config_id)) if tts_config_id else _resolve_tts_client()
    client.voice = str(voice_row["voice"])
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        duration = client.synthesize(str(voice_row["sample_text"]), tmp_path)
        return {
            "ok": True,
            "model": client.model,
            "voice": client.voice,
            "language_code": voice_row.get("language_code"),
            "duration_seconds": round(duration, 2),
        }
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


@app.post("/tts-voices/{voice_id}/test")
def test_tts_voice_endpoint(voice_id: int) -> Dict[str, Any]:
    voice = next((item for item in list_tts_voices() if int(item["id"]) == int(voice_id)), None)
    if voice is None:
        raise HTTPException(status_code=404, detail=f"TTS voice not found: {voice_id}")
    try:
        return _test_tts_voice_config(voice)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "message": f"语音测试失败：{exc}",
                "error_type": "tts_voice_test_failed",
                "voice": voice.get("voice"),
                "model_config_id": voice.get("model_config_id"),
            },
        ) from exc


def _test_tts_config(config) -> Dict[str, Any]:
    """用短文本测试 TTS 配置是否可用。"""
    from app.tts import TTSClient
    import tempfile
    client = TTSClient.from_config(config)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        duration = client.synthesize("测试语音", tmp_path)
        return {
            "ok": True,
            "model_type": "tts",
            "model": config.model_name,
            "voice": config.voice,
            "duration_seconds": round(duration, 2),
        }
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


@app.post("/model-configs/{config_id}/test")
def test_saved_model_config(config_id: int) -> Dict[str, Any]:
    config = get_model_config(config_id)
    if config is None:
        raise HTTPException(status_code=404, detail=f"Model config not found: {config_id}")
    try:
        if config.model_type == "tts":
            return _test_tts_config(config)
        return test_model_config(config)
    except LLMConfigurationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "message": f"配置错误：{exc}",
                "error_type": "configuration",
            },
        ) from exc
    except requests.exceptions.Timeout as exc:
        raise HTTPException(
            status_code=408,
            detail={
                "ok": False,
                "message": f"API 请求超时（{config.timeout_seconds}s）：{config.base_url}",
                "error_type": "timeout",
                "base_url": config.base_url,
            },
        ) from exc
    except requests.exceptions.ConnectionError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "ok": False,
                "message": f"无法连接到 API 服务器：{config.base_url}",
                "error_type": "connection",
                "base_url": config.base_url,
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "message": f"API 测试失败：{exc}",
                "error_type": "unknown",
                "base_url": config.base_url,
                "model": config.model_name,
            },
        ) from exc


@app.get("/prompt-versions")
def prompt_versions() -> Dict[str, Any]:
    items = list_prompt_versions()
    return {
        "count": len(items),
        "prompt_versions": items,
    }


@app.get("/prompt-versions/{prompt_id}")
def prompt_version(prompt_id: int) -> Dict[str, Any]:
    item = get_prompt_version(prompt_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Prompt version not found: {prompt_id}")
    return item.__dict__


@app.post("/prompt-versions")
def save_prompt_version(payload: PromptVersionUpsertRequest) -> Dict[str, Any]:
    if "{markdown}" not in payload.user_prompt_template:
        raise HTTPException(status_code=400, detail="用户提示词模板必须包含 {markdown} 占位符")
    prompt_id = upsert_prompt_version(
        name=payload.name,
        system_prompt=payload.system_prompt,
        user_prompt_template=payload.user_prompt_template,
        enabled=payload.enabled,
        is_default=payload.is_default,
        prompt_id=payload.id,
    )
    return {
        "id": prompt_id,
        "status": "saved",
    }


@app.delete("/prompt-versions/{prompt_id}")
def delete_prompt_version_endpoint(prompt_id: int) -> Dict[str, Any]:
    deleted = delete_prompt_version(prompt_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Prompt version not found: {prompt_id}")
    return {
        "id": prompt_id,
        "status": "deleted",
    }


@app.post("/rss/sources")
def save_rss_source(payload: RSSSourceUpsertRequest) -> Dict[str, Any]:
    try:
        source_id = upsert_rss_source(
            name=payload.name,
            url=payload.url,
            category=payload.category,
            language=payload.language,
            priority=payload.priority,
            enabled=payload.enabled,
            request_timeout_seconds=payload.request_timeout_seconds,
            source_id=payload.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "id": source_id,
        "status": "saved",
        "updated": payload.id is not None,
    }


@app.post("/rss/sources/{source_id}/test")
def test_rss_source(source_id: int) -> Dict[str, Any]:
    try:
        result = preview_source(source_id, limit=5)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail=f"RSS source not found: {source_id}")
    return {
        "status": "ok",
        "source_id": result["source_id"],
        "source_name": result["source_name"],
        "url": result["url"],
        "fetched_count": result["fetched_count"],
        "preview_count": result["preview_count"],
    }


@app.get("/rss/sources/{source_id}/preview")
def preview_rss_source(source_id: int, limit: int = 10) -> Dict[str, Any]:
    try:
        result = preview_source(source_id, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail=f"RSS source not found: {source_id}")
    return result


@app.delete("/rss/sources/{source_id}")
def delete_rss_source(source_id: int) -> Dict[str, Any]:
    disabled = disable_rss_source(source_id)
    if not disabled:
        raise HTTPException(status_code=404, detail=f"RSS source not found: {source_id}")
    return {
        "source_id": source_id,
        "status": "disabled",
    }


@app.post("/rss/sources/{source_id}/disable")
def disable_rss_source_endpoint(source_id: int) -> Dict[str, Any]:
    disabled = disable_rss_source(source_id)
    if not disabled:
        raise HTTPException(status_code=404, detail=f"RSS source not found: {source_id}")
    return {
        "source_id": source_id,
        "status": "disabled",
    }


@app.post("/rss/sources/{source_id}/delete")
def hard_delete_rss_source_endpoint(source_id: int) -> Dict[str, Any]:
    deleted = hard_delete_rss_source(source_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"RSS source not found: {source_id}")
    return {
        "source_id": source_id,
        "status": "deleted",
    }


@app.post("/rss/collect")
def collect_rss_sources() -> Dict[str, Any]:
    return collect_enabled_sources()


@app.post("/rss/collect/{source_id}")
def collect_rss_source(source_id: int) -> Dict[str, Any]:
    result = collect_one_source(source_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"RSS source not found: {source_id}")
    return result


@app.post("/pipeline/analyze")
def analyze_rss_items(payload: AnalyzeRSSRequest) -> Dict[str, Any]:
    config = PipelineTaskConfig(
        hours=payload.hours,
        limit=payload.limit,
        rss_category=payload.rss_category,
        model_config_id=payload.model_config_id,
        prompt_version_id=payload.prompt_version_id,
        report_type=payload.report_type,
        auto_render=payload.auto_render,
        auto_publish=payload.auto_publish,
        render_engine=_normalize_render_engine(payload.render_engine),
    )
    run_id = _start_pipeline_task(
        "rss_analyze",
        config,
        trigger_type="manual",
    )
    return {
        "run_id": run_id,
        "status": "pending",
        "poll_url": f"/runs/{run_id}",
    }


@app.post("/pipeline/collect-and-analyze")
def collect_and_analyze(payload: AnalyzeRSSRequest) -> Dict[str, Any]:
    config = PipelineTaskConfig(
        hours=payload.hours,
        limit=payload.limit,
        rss_category=payload.rss_category,
        model_config_id=payload.model_config_id,
        prompt_version_id=payload.prompt_version_id,
        report_type=payload.report_type,
        auto_render=payload.auto_render,
        auto_publish=payload.auto_publish,
        render_engine=_normalize_render_engine(payload.render_engine),
    )
    run_id = _start_pipeline_task(
        "collect_and_analyze",
        config,
        trigger_type="manual",
    )
    return {
        "run_id": run_id,
        "status": "pending",
        "poll_url": f"/runs/{run_id}",
    }


@app.post("/pipeline/run")
def pipeline_run(payload: PipelineRunRequest) -> Dict[str, Any]:
    """手动按指定 RSS 分类 / 模型 / 提示词 / 报告类型组合运行一次任务。"""
    config = PipelineTaskConfig(
        hours=payload.hours,
        limit=payload.limit,
        rss_category=payload.rss_category,
        model_config_id=payload.model_config_id,
        prompt_version_id=payload.prompt_version_id,
        report_type=payload.report_type,
        auto_render=payload.auto_render,
        auto_publish=payload.auto_publish,
        render_engine=_normalize_render_engine(payload.render_engine),
    )
    run_id = _start_pipeline_task(
        payload.task_type,
        config,
        trigger_type="manual",
    )
    return {
        "run_id": run_id,
        "status": "pending",
        "task_type": payload.task_type,
        "poll_url": f"/runs/{run_id}",
    }


@app.get("/schedules")
def schedules() -> Dict[str, Any]:
    items = list_schedule_configs()
    return {
        "count": len(items),
        "schedules": items,
    }


@app.get("/runs")
def runs(limit: int = 50) -> Dict[str, Any]:
    safe_limit = max(1, min(limit, 200))
    items = list_pipeline_runs(limit=safe_limit)
    return {
        "count": len(items),
        "runs": items,
    }


@app.get("/runs/{run_id}")
def pipeline_run(run_id: int) -> Dict[str, Any]:
    row = get_pipeline_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Pipeline run not found: {run_id}")
    return row


@app.get("/runs/{run_id}/events")
def pipeline_run_events(run_id: int, limit: int = 200) -> Dict[str, Any]:
    row = get_pipeline_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Pipeline run not found: {run_id}")
    safe_limit = max(1, min(limit, 1000))
    events = list_pipeline_run_events(run_id, limit=safe_limit)
    return {
        "run_id": run_id,
        "count": len(events),
        "events": events,
    }


@app.get("/runs/{run_id}/stream")
def pipeline_run_event_stream(run_id: int, request: Request) -> StreamingResponse:
    row = get_pipeline_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Pipeline run not found: {run_id}")

    return StreamingResponse(
        _pipeline_run_event_stream(request, run_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/llm-call-logs")
def llm_call_logs(limit: int = 100, pipeline_run_id: Optional[int] = None) -> Dict[str, Any]:
    safe_limit = max(1, min(limit, 500))
    items = list_llm_call_logs(limit=safe_limit, pipeline_run_id=pipeline_run_id)
    return {
        "count": len(items),
        "items": items,
    }


@app.get("/llm-call-stats")
def llm_stats(days: int = 7) -> Dict[str, Any]:
    return llm_call_stats(days=max(1, min(days, 365)))


@app.post("/runs/{run_id}/cancel")
def cancel_pipeline_run(run_id: int) -> Dict[str, Any]:
    row = get_pipeline_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Pipeline run not found: {run_id}")

    if row["status"] not in {"pending", "running"}:
        return {
            "run_id": run_id,
            "status": row["status"],
            "cancelled": False,
            "message": "任务已结束，不能取消",
        }

    _request_pipeline_cancel(run_id)
    update_pipeline_run(run_id, float(row.get("progress_percent") or 0), "取消请求已提交")
    add_pipeline_event(run_id, "cancel", "用户提交取消请求", level="warning")
    return {
        "run_id": run_id,
        "status": "cancel_requested",
        "cancelled": True,
    }


@app.post("/runs/{run_id}/retry")
def retry_pipeline_run(run_id: int) -> Dict[str, Any]:
    row = get_pipeline_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Pipeline run not found: {run_id}")

    if row["status"] in {"pending", "running"}:
        raise HTTPException(status_code=409, detail="任务仍在运行，不能重试")

    retry_run_id = _start_pipeline_task(
        row["task_type"],
        _config_from_run_row(row),
        trigger_type="retry",
        schedule_id=row.get("schedule_id"),
        prevent_overlap=True,
    )
    add_pipeline_event(
        run_id,
        "retry",
        f"已创建重试任务：run_id={retry_run_id}",
        level="warning",
    )
    return {
        "run_id": run_id,
        "retry_run_id": retry_run_id,
        "status": "retry_started",
        "poll_url": f"/runs/{retry_run_id}",
    }


@app.post("/schedules")
def save_schedule(payload: ScheduleUpsertRequest) -> Dict[str, Any]:
    schedule_id = upsert_schedule_config(
        name=payload.name,
        task_type=payload.task_type,
        cron_expression=payload.cron_expression,
        tz_name=payload.timezone,
        enabled=payload.enabled,
        prevent_overlap=payload.prevent_overlap,
        max_runtime_seconds=payload.max_runtime_seconds,
        retry_count=payload.retry_count,
        retry_interval_seconds=payload.retry_interval_seconds,
        rss_category=payload.rss_category,
        model_config_id=payload.model_config_id,
        prompt_version_id=payload.prompt_version_id,
        tts_config_id=payload.tts_config_id,
        report_type=payload.report_type,
        auto_render=payload.auto_render,
        auto_publish=payload.auto_publish,
        render_engine=_normalize_render_engine(payload.render_engine),
        schedule_id=payload.id,
    )
    return {
        "id": schedule_id,
        "status": "saved",
    }


@app.post("/schedules/{schedule_id}/run")
def run_schedule(schedule_id: int) -> Dict[str, Any]:
    schedule = get_schedule_config(schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}")
    run_id = _start_pipeline_task(
        schedule.task_type,
        _config_from_schedule(schedule),
        trigger_type="manual",
        schedule_id=schedule.id,
        prevent_overlap=bool(schedule.prevent_overlap),
        retry_count=schedule.retry_count,
        retry_interval_seconds=schedule.retry_interval_seconds,
    )
    return {
        "id": schedule_id,
        "run_id": run_id,
        "task_type": schedule.task_type,
        "status": "started",
        "poll_url": f"/runs/{run_id}",
    }


@app.post("/schedules/{schedule_id}/disable")
def stop_schedule(schedule_id: int) -> Dict[str, Any]:
    schedule = get_schedule_config(schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}")
    disable_schedule(schedule_id)
    return {
        "id": schedule_id,
        "status": "disabled",
    }


@app.post("/schedules/{schedule_id}/enable")
def start_schedule(schedule_id: int) -> Dict[str, Any]:
    schedule = get_schedule_config(schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}")
    enabled = enable_schedule(schedule_id)
    if not enabled:
        raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}")
    updated = get_schedule_config(schedule_id)
    return {
        "id": schedule_id,
        "status": "enabled",
        "next_run_at": updated.next_run_at if updated else None,
    }


@app.get("/reports")
def reports(
    limit: int = 50,
    report_type: Optional[str] = None,
    rss_category: Optional[str] = None,
    model_config_id: Optional[int] = None,
    prompt_version_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict[str, Any]:
    safe_limit = max(1, min(limit, 200))
    items = list_reports(
        limit=safe_limit,
        report_type=report_type or None,
        rss_category=rss_category or None,
        model_config_id=model_config_id or None,
        prompt_version_id=prompt_version_id or None,
        date_from=date_from or None,
        date_to=date_to or None,
    )
    for item in items:
        item["video_download_url"] = f"/videos/{item['id']}" if item.get("video_status") == "done" else None
    return {
        "count": len(items),
        "reports": items,
    }


@app.get("/reports/{report_id}")
def report_detail(report_id: int) -> Dict[str, Any]:
    report = get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Report not found: {report_id}")
    report["video_download_url"] = (
        f"/videos/{report_id}" if report.get("video_status") == "done" else None
    )
    news = list_report_news(report_id)
    return {
        "report": report,
        "news": news,
    }


@app.put("/reports/{report_id}/news/{news_id}")
def update_report_news_item(
    report_id: int,
    news_id: int,
    payload: ReportNewsUpdateRequest,
) -> Dict[str, Any]:
    report = get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Report not found: {report_id}")

    item = update_report_news(
        report_id=report_id,
        news_id=news_id,
        item_index=payload.item_index,
        title=payload.title,
        pubdate=payload.pubdate,
        summary=payload.summary,
        related_field=payload.related_field,
        importance=payload.importance,
        reserve_reason=payload.reserve_reason,
        link=payload.link,
        voiceover_script=payload.voiceover_script,
    )
    if item is None:
        raise HTTPException(status_code=404, detail=f"Report news not found: {news_id}")
    return {
        "status": "saved",
        "report_id": report_id,
        "news": item,
    }


@app.get("/tts-audio-assets")
def tts_audio_assets(
    limit: int = 50,
    status: Optional[str] = None,
    report_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict[str, Any]:
    safe_limit = max(1, min(limit, 200))
    safe_status = status if status in {None, "pending", "processing", "done", "failed"} else None
    items = list_tts_audio_assets(
        limit=safe_limit,
        status=safe_status,
        report_id=report_id,
        date_from=date_from,
        date_to=date_to,
    )
    return {
        "count": len(items),
        "tts_audio_assets": [_enrich_tts_audio_asset(item) for item in items],
    }


@app.post("/tts-items/{queue_id}/generate")
def generate_tts_item(queue_id: int, force: bool = False) -> Dict[str, Any]:
    item = get_tts_item(queue_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"TTS item not found: {queue_id}")
    try:
        return generate_tts_item_audio(item, force=force)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/tts-items/{queue_id}/retry")
def retry_tts_item(queue_id: int) -> Dict[str, Any]:
    return generate_tts_item(queue_id, force=True)


@app.get("/tts-items/{queue_id}/audio")
def tts_item_audio(queue_id: int) -> FileResponse:
    item = get_tts_item(queue_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"TTS item not found: {queue_id}")
    audio_path = _safe_tts_audio_path(item)
    return FileResponse(
        audio_path,
        media_type="audio/wav",
        filename=f"tts_item_{queue_id}.wav",
    )


@app.delete("/tts-items/{queue_id}/audio")
def delete_tts_item_audio(queue_id: int) -> Dict[str, Any]:
    item = get_tts_item(queue_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"TTS item not found: {queue_id}")
    audio_path = _safe_tts_audio_path(item)
    try:
        audio_path.unlink()
    except FileNotFoundError:
        pass
    set_tts_item_pending(queue_id)
    return {
        "queue_id": queue_id,
        "report_id": item.report_id,
        "status": "deleted",
        "audio_path": str(audio_path),
    }


@app.post("/render/latest")
def render_latest(engine: str = "remotion") -> Dict[str, Any]:
    report_id = get_latest_report_id()
    if report_id is None:
        raise HTTPException(status_code=404, detail="No report found")
    return render_report(report_id, engine=engine)


@app.post("/render/report/{report_id}")
def render_report(report_id: int, engine: str = "remotion") -> Dict[str, Any]:
    items = load_tts_items(report_id)
    if not items:
        raise HTTPException(status_code=404, detail=f"No TTS items for report_id={report_id}")

    render_engine = _normalize_render_engine(engine)
    title = items[0].report_title or _fallback_title_from_report(report_id)
    job_id, scheduled = _schedule_report(report_id, title, len(items), render_engine=render_engine)

    return {
        "job_id": job_id,
        "report_id": report_id,
        "render_engine": render_engine,
        "status": "pending" if scheduled else "already_scheduled",
        "progress_percent": 0,
        "current_step": "任务已排队" if scheduled else "任务已在队列中",
        "poll_url": f"/jobs/{job_id}",
    }


@app.post("/reports/{report_id}/publish")
def publish_report(report_id: int) -> Dict[str, Any]:
    items = load_tts_items(report_id)
    if not items:
        raise HTTPException(status_code=404, detail=f"No publishable TTS items for report_id={report_id}")

    published_count = mark_report_news_published(report_id)
    return {
        "report_id": report_id,
        "published_count": published_count,
        "status": "published",
    }


def render_report_job(report_id: int) -> None:
    try:
        _raise_if_cancelled(report_id)
        items = load_tts_items(report_id)
        if not items:
            raise RuntimeError(f"No TTS items for report_id={report_id}")

        title = items[0].report_title or _fallback_title_from_report(report_id)
        existing_job = get_video_job_by_report_id(report_id) or {}
        render_engine = _normalize_render_engine(existing_job.get("render_engine"))
        upsert_video_job(report_id, title, "rendering", len(items), render_engine=render_engine)
        _raise_if_cancelled(report_id)

        report_dir = settings.output_dir / f"report_{report_id}"
        remotion_audio_dir = _tts_audio_public_dir(report_id)
        report_dir.mkdir(parents=True, exist_ok=True)

        report_meta = get_report(report_id) or {}
        tts_config_id = _tts_config_id_from_report(report_meta)
        tts_client = _resolve_tts_client(tts_config_id)
        rendered_items = []
        total_audio_seconds = 0.0
        total_items = max(len(items), 1)

        set_video_job_progress(report_id, 3, "开始生成 TTS 音频")

        domain_label = _domain_label(
            report_meta.get("report_type") or "general",
            report_meta.get("rss_category"),
        )
        intro_narration = (
            f"大家好，欢迎收看{datetime.now().strftime('%Y年%m月%d日')}的{domain_label}日报。"
            f"今天为你精选{len(items)}条{domain_label}重点消息。"
        )
        intro_audio_filename = audio_cache_name("intro", intro_narration)
        intro_audio_path = remotion_audio_dir / intro_audio_filename
        set_video_job_progress(report_id, 4, "生成开场音频")
        if not intro_audio_path.exists():
            intro_duration = tts_client.synthesize(intro_narration, intro_audio_path)
        else:
            intro_duration = audio_duration_seconds(intro_audio_path)
        total_audio_seconds += intro_duration

        for index, item in enumerate(items, start=1):
            _raise_if_cancelled(report_id)
            narration = item.narration
            if not narration:
                continue
            set_video_job_progress(
                report_id,
                8 + (index - 1) / total_items * 67,
                f"生成第 {index}/{total_items} 条音频",
            )

            tts_result = generate_tts_item_audio(item, force=False)
            audio_filename = _tts_audio_filename(item)
            duration = float(tts_result["duration_seconds"])

            total_audio_seconds += duration

            rendered_items.append(
                {
                    "itemIndex": item.item_index,
                    "title": item.news_title,
                    "pubdate": item.pubdate or "",
                    "summary": item.summary or "",
                    "relatedField": item.related_field or domain_label,
                    "importance": item.importance or "中",
                    "reserveReason": item.reserve_reason or "",
                    "link": item.link or "",
                    "voiceover": normalize_tts_text(narration),
                    "audioSrc": f"generated/report_{report_id}/{audio_filename}",
                    "duration": duration,
                }
            )

        outro_narration = f"以上就是今天的{domain_label}日报，感谢收看，我们下期再见。"
        outro_audio_filename = audio_cache_name("outro", outro_narration)
        outro_audio_path = remotion_audio_dir / outro_audio_filename
        set_video_job_progress(report_id, 76, "生成结束音频")
        if not outro_audio_path.exists():
            outro_duration = tts_client.synthesize(outro_narration, outro_audio_path)
        else:
            outro_duration = audio_duration_seconds(outro_audio_path)
        total_audio_seconds += outro_duration

        set_video_job_progress(report_id, 78, "生成视频模板参数")
        _raise_if_cancelled(report_id)
        props = {
            "title": title,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "introVoiceover": intro_narration,
            "introAudioSrc": f"generated/report_{report_id}/{intro_audio_filename}",
            "introDuration": intro_duration,
            "outroVoiceover": outro_narration,
            "outroAudioSrc": f"generated/report_{report_id}/{outro_audio_filename}",
            "outroDuration": outro_duration,
            "fps": settings.video_fps,
            "width": settings.video_width,
            "height": settings.video_height,
            "items": rendered_items,
        }

        engine_label = "FFmpeg 模板" if render_engine == "ffmpeg" else "Remotion"
        set_video_job_progress(report_id, 82, f"{engine_label} 渲染视频中")
        progress_throttler = ProgressThrottler(min_delta=1.0, max_interval_seconds=2.0)
        progress_throttler.reset(82)

        def update_render_progress(progress: float) -> None:
            percent = 82 + progress * 16
            if progress_throttler.should_update(percent, force=progress >= 1):
                set_video_job_progress(report_id, percent, f"{engine_label} 渲染视频中 {progress * 100:.0f}%")

        if render_engine == "ffmpeg":
            video_path = render_ffmpeg_template(report_id, props, report_dir, on_progress=update_render_progress)
        else:
            video_path = render_video(report_id, props, report_dir, on_progress=update_render_progress)
        duration_seconds = total_audio_seconds + 3.0
        set_video_job_done(report_id, str(video_path), duration_seconds)

        # auto_publish：视频渲染成功后自动标记该报告新闻已发布（按 report_type 隔离去重）
        if should_auto_publish(report_id):
            try:
                published = mark_report_news_published(report_id)
                print(
                    f"auto_publish: report_id={report_id} marked {published} news published",
                    flush=True,
                )
            except Exception as exc:
                print(f"auto_publish failed for report_id={report_id}: {exc}", flush=True)
    except RenderJobCancelled:
        set_video_job_cancelled(report_id)
    except Exception as exc:
        if _is_cancelled(report_id):
            set_video_job_cancelled(report_id)
            return
        set_video_job_failed(report_id, str(exc))


@app.get("/jobs/{job_id}")
def job(job_id: int) -> Dict[str, Any]:
    row = get_video_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    return row


@app.get("/video-jobs")
def video_jobs(
    limit: int = 50,
    status: Optional[str] = None,
    report_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict[str, Any]:
    safe_limit = max(1, min(limit, 200))
    safe_status = status if status in {None, "pending", "rendering", "done", "failed", "cancelled"} else None
    items = list_video_jobs(
        limit=safe_limit,
        status=safe_status,
        report_id=report_id,
        date_from=date_from,
        date_to=date_to,
    )
    for item in items:
        _enrich_video_asset(item)
    return {
        "count": len(items),
        "video_jobs": items,
    }


@app.post("/video-assets/{report_id}/cover")
def generate_video_cover(report_id: int) -> Dict[str, Any]:
    row = get_video_job_by_report_id(report_id)
    if not row:
        raise HTTPException(status_code=404, detail="Video job not found")
    report = get_report(report_id)
    news = list_report_news(report_id)
    try:
        cover_paths = generate_cover_images(report_id, row.get("video_path"), report=report, news=news)
        cover_path = cover_paths["16x9"]
        set_video_job_cover_path(report_id, str(cover_path))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)[:2000]) from exc

    return {
        "report_id": report_id,
        "cover_path": str(cover_path),
        "cover_download_url": f"/video-assets/{report_id}/cover",
        "cover_4x3_path": str(cover_paths["4x3"]),
        "cover_4x3_download_url": f"/video-assets/{report_id}/cover/4x3",
        "status": "cover_generated",
    }


@app.get("/video-assets/{report_id}/cover")
def video_cover(report_id: int):
    row = get_video_job_by_report_id(report_id)
    if not row or not row.get("cover_path"):
        raise HTTPException(status_code=404, detail="Cover not found")
    try:
        path = safe_output_path(row.get("cover_path"))
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="Cover file not found")
    return FileResponse(path, media_type="image/jpeg", filename=f"game_daily_report_{report_id}_cover.jpg")


@app.get("/video-assets/{report_id}/cover/4x3")
def video_cover_4x3(report_id: int):
    row = get_video_job_by_report_id(report_id)
    if not row:
        raise HTTPException(status_code=404, detail="Video job not found")
    path = report_output_dir(report_id) / "cover_4x3.jpg"
    try:
        resolved = safe_output_path(str(path))
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if resolved is None or not resolved.exists():
        raise HTTPException(status_code=404, detail="4:3 cover file not found")
    return FileResponse(resolved, media_type="image/jpeg", filename=f"game_daily_report_{report_id}_cover_4x3.jpg")


@app.delete("/video-assets/{report_id}")
def delete_video_asset(report_id: int, delete_audio: bool = True) -> Dict[str, Any]:
    row = get_video_job_by_report_id(report_id)
    if not row:
        raise HTTPException(status_code=404, detail="Video job not found")
    if row.get("status") in {"pending", "rendering"} and row.get("worker_id"):
        raise HTTPException(status_code=409, detail="Video job is running; cancel it before deleting assets")

    result = remove_report_assets(report_id, delete_audio=delete_audio)
    clear_video_job_assets(report_id)
    result["status"] = "assets_deleted"
    return result


@app.get("/jobs/{job_id}/stream")
def video_job_event_stream(job_id: int, request: Request) -> StreamingResponse:
    row = get_video_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    return StreamingResponse(
        _video_job_event_stream(request, job_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int) -> Dict[str, Any]:
    row = get_video_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    report_id = int(row["report_id"])
    if row["status"] not in {"pending", "rendering"}:
        return {
            "job_id": job_id,
            "report_id": report_id,
            "status": row["status"],
            "cancelled": False,
        }

    with _render_state_lock:
        _cancelled_reports.add(report_id)
    process_stopped = cancel_render(report_id)
    set_video_job_cancelled(report_id)
    return {
        "job_id": job_id,
        "report_id": report_id,
        "status": "cancelled",
        "cancelled": True,
        "active_process_stopped": process_stopped,
    }


@app.get("/videos/{report_id}")
def video(report_id: int):
    row = get_video_job_by_report_id(report_id)
    path = None
    if row and row.get("video_path"):
        try:
            path = safe_output_path(row.get("video_path"))
        except ValueError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    if path is None:
        path = settings.output_dir / f"report_{report_id}" / "final.mp4"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    return FileResponse(path, media_type="video/mp4", filename=f"game_daily_report_{report_id}.mp4")
