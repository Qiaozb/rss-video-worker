import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    project_root: Path = PROJECT_ROOT

    mysql_host: str = os.getenv("MYSQL_HOST", "127.0.0.1")
    mysql_port: int = int(os.getenv("MYSQL_PORT", "3306"))
    mysql_user: str = os.getenv("MYSQL_USER", "root")
    mysql_password: str = os.getenv("MYSQL_PASSWORD", "")
    mysql_database: str = os.getenv("MYSQL_DATABASE", "dify_test")

    app_secret_key: str = os.getenv("APP_SECRET_KEY", "")
    auth_required: bool = os.getenv("AUTH_REQUIRED", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    admin_username: str = os.getenv("ADMIN_USERNAME", "admin")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "")
    admin_session_hours: int = int(os.getenv("ADMIN_SESSION_HOURS", "12"))
    admin_cookie_secure: bool = os.getenv("ADMIN_COOKIE_SECURE", "false").lower() in {
        "1",
        "true",
        "yes",
    }

    tts_base_url: str = os.getenv("TTS_BASE_URL", "http://127.0.0.1:9880")
    tts_voice: str = os.getenv("TTS_VOICE", "Serena")

    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "deepseek-chat")
    llm_timeout_seconds: int = int(os.getenv("LLM_TIMEOUT_SECONDS", "180"))
    llm_temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.2"))

    output_dir: Path = Path(os.getenv("OUTPUT_DIR", str(PROJECT_ROOT / "output"))).expanduser().resolve()
    remotion_root: Path = Path(os.getenv("REMOTION_ROOT", str(PROJECT_ROOT / "remotion"))).expanduser().resolve()
    remotion_public_dir: Path = Path(
        os.getenv("REMOTION_PUBLIC_DIR", str(PROJECT_ROOT / "remotion" / "public"))
    ).expanduser().resolve()

    video_width: int = int(os.getenv("VIDEO_WIDTH", "1920"))
    video_height: int = int(os.getenv("VIDEO_HEIGHT", "1080"))
    video_fps: int = int(os.getenv("VIDEO_FPS", "30"))

    # 单次 Remotion 渲染的墙上时钟超时（秒）。超时后 kill 掉子进程组，
    # 避免卡死的 Chrome/FFmpeg 永久阻塞单消费者渲染队列。
    render_timeout_seconds: int = int(os.getenv("RENDER_TIMEOUT_SECONDS", "1800"))
    # 调度器判定任务僵尸的心跳阈值（秒）。heartbeat_at 早于该阈值且仍处于
    # running/rendering 的任务会被回收：渲染任务杀进程组，pipeline 标记失败。
    video_stale_seconds: int = int(os.getenv("VIDEO_STALE_SECONDS", "900"))
    pipeline_stale_seconds: int = int(os.getenv("PIPELINE_STALE_SECONDS", "900"))
    # 维护清理：DB 日志保留天数（0 表示不清理）。
    audit_log_retention_days: int = int(os.getenv("AUDIT_LOG_RETENTION_DAYS", "90"))
    llm_call_log_retention_days: int = int(os.getenv("LLM_CALL_LOG_RETENTION_DAYS", "60"))


settings = Settings()
