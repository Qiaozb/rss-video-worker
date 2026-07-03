from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from app.config import settings


ProgressCallback = Callable[[float], None]
_active_processes: Dict[int, subprocess.Popen[str]] = {}
_active_processes_lock = threading.Lock()


class RemotionRenderError(RuntimeError):
    def __init__(
        self,
        return_code: int,
        details: str,
        *,
        timed_out: bool,
        attempt_label: str,
    ) -> None:
        self.return_code = return_code
        self.details = details
        self.timed_out = timed_out
        self.attempt_label = attempt_label
        suffix = "\n渲染超时，已强制终止子进程组。" if timed_out else ""
        super().__init__(
            f"Remotion render failed with exit code {return_code} "
            f"({attempt_label}).\n{details}{suffix}"
        )

    @property
    def retryable(self) -> bool:
        lowered = self.details.lower()
        return (
            self.timed_out
            or self.return_code in {-9, -15}
            or "target-closed" in lowered
            or "browser crashed" in lowered
            or "got no response" in lowered
        )


def _signal_process_group(process: subprocess.Popen[str], sig: signal.Signals) -> None:
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass


def _cleanup_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    _signal_process_group(process, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _signal_process_group(process, signal.SIGKILL)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


def cancel_render(report_id: int) -> bool:
    with _active_processes_lock:
        process = _active_processes.get(report_id)
    if process is None:
        return False
    _cleanup_process_group(process)
    return True


def cancel_all_renders() -> None:
    with _active_processes_lock:
        processes = list(_active_processes.values())
    for process in processes:
        _cleanup_process_group(process)


def _progress_from_payload(payload: Dict[str, Any]) -> Optional[float]:
    for key in ("totalProgress", "progress"):
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return max(0.0, min(float(value), 1.0))

    frame_value = payload.get("frame") or payload.get("renderedFrames")
    total_value = payload.get("totalFrames") or payload.get("frames")
    if isinstance(frame_value, (int, float)) and isinstance(total_value, (int, float)) and total_value > 0:
        return max(0.0, min(float(frame_value) / float(total_value), 1.0))

    return None


def render_video(
    report_id: int,
    props: Dict[str, Any],
    work_dir: Path,
    on_progress: Optional[ProgressCallback] = None,
) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    props_path = work_dir / "props.json"
    output_path = work_dir / "final.mp4"

    props_path.write_text(json.dumps(props, ensure_ascii=False, indent=2), encoding="utf-8")

    attempts = [
        ("normal", {}, settings.render_timeout_seconds),
        (
            "safe-retry",
            {
                "REMOTION_CONCURRENCY": "1",
                "REMOTION_MAX_CONCURRENCY": "1",
                "REMOTION_HARDWARE_ACCELERATION": "disable",
                "REMOTION_ALLOW_HARDWARE_ACCELERATION": "0",
            },
            max(settings.render_timeout_seconds, 7200),
        ),
    ]

    last_error: Optional[RemotionRenderError] = None
    for attempt_index, (attempt_label, env_overrides, timeout_seconds) in enumerate(attempts):
        try:
            return _run_render_process(
                report_id=report_id,
                props_path=props_path,
                output_path=output_path,
                env_overrides=env_overrides,
                timeout_seconds=timeout_seconds,
                attempt_label=attempt_label,
                on_progress=on_progress,
            )
        except RemotionRenderError as exc:
            last_error = exc
            if attempt_index == 0 and exc.retryable:
                continue
            raise

    if last_error is not None:
        raise last_error
    return output_path


def _run_render_process(
    *,
    report_id: int,
    props_path: Path,
    output_path: Path,
    env_overrides: Dict[str, str],
    timeout_seconds: int,
    attempt_label: str,
    on_progress: Optional[ProgressCallback],
) -> Path:
    env = os.environ.copy()
    env.update(env_overrides)
    process = subprocess.Popen(
        [
            "node",
            str(settings.remotion_root / "render.mjs"),
            str(props_path),
            str(output_path),
        ],
        cwd=str(settings.remotion_root.parent),
        stderr=subprocess.STDOUT,
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
        env=env,
    )

    with _active_processes_lock:
        _active_processes[report_id] = process

    # 墙上时钟超时看门狗：超过 render_timeout_seconds 仍未结束时，杀掉整个
    # 子进程组（Node + Chrome + FFmpeg），让下方 process.stdout 的读取 EOF
    # 退出，避免单消费者渲染队列被卡死的渲染进程永久阻塞。
    finished_event = threading.Event()
    timed_out = False

    def _watchdog() -> None:
        if finished_event.wait(timeout_seconds):
            return  # 进程已正常结束，看门狗提前退出
        nonlocal timed_out
        timed_out = True
        _cleanup_process_group(process)

    watchdog = threading.Thread(
        target=_watchdog,
        name=f"render-watchdog-{report_id}",
        daemon=True,
    )
    watchdog.start()

    try:
        output_lines: list[str] = []
        assert process.stdout is not None
        for line in process.stdout:
            line = line.rstrip()
            if line:
                output_lines.append(line)

            if not line.startswith("REMOTION_PROGRESS "):
                continue

            try:
                payload = json.loads(line.removeprefix("REMOTION_PROGRESS "))
            except json.JSONDecodeError:
                continue

            progress = _progress_from_payload(payload)
            if progress is not None and on_progress is not None:
                on_progress(progress)

        return_code = process.wait()
        if return_code != 0:
            details = "\n".join(output_lines[-40:])
            raise RemotionRenderError(
                return_code,
                details,
                timed_out=timed_out,
                attempt_label=attempt_label,
            )

        return output_path
    finally:
        finished_event.set()
        _cleanup_process_group(process)
        with _active_processes_lock:
            if _active_processes.get(report_id) is process:
                del _active_processes[report_id]
