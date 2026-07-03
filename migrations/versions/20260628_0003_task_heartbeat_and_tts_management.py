"""add task heartbeat fields and tts management columns

Revision ID: 20260628_0003
Revises: 20260628_0002
Create Date: 2026-06-28
"""

from alembic import op


revision = "20260628_0003"
down_revision = "20260628_0002"
branch_labels = None
depends_on = None


def _column_exists(table_name: str, column_name: str) -> bool:
    connection = op.get_bind()
    result = connection.exec_driver_sql(f"SHOW COLUMNS FROM {table_name} LIKE %s", (column_name,))
    return result.fetchone() is not None


def _add_column_if_missing(table_name: str, column_name: str, ddl: str) -> None:
    if not _column_exists(table_name, column_name):
        op.execute(ddl)


def upgrade() -> None:
    _add_column_if_missing(
        "pipeline_runs",
        "worker_id",
        "ALTER TABLE pipeline_runs ADD COLUMN worker_id VARCHAR(255) DEFAULT NULL",
    )
    _add_column_if_missing(
        "pipeline_runs",
        "heartbeat_at",
        "ALTER TABLE pipeline_runs ADD COLUMN heartbeat_at DATETIME DEFAULT NULL",
    )
    _add_column_if_missing(
        "rss_video_job",
        "worker_id",
        "ALTER TABLE rss_video_job ADD COLUMN worker_id VARCHAR(255) DEFAULT NULL",
    )
    _add_column_if_missing(
        "rss_video_job",
        "heartbeat_at",
        "ALTER TABLE rss_video_job ADD COLUMN heartbeat_at DATETIME DEFAULT NULL",
    )
    _add_column_if_missing(
        "rss_llm_tts_queue",
        "tts_audio_path",
        "ALTER TABLE rss_llm_tts_queue ADD COLUMN tts_audio_path VARCHAR(1024) DEFAULT NULL",
    )
    _add_column_if_missing(
        "rss_llm_tts_queue",
        "tts_error",
        "ALTER TABLE rss_llm_tts_queue ADD COLUMN tts_error TEXT DEFAULT NULL",
    )


def downgrade() -> None:
    for table_name, column_name in [
        ("rss_llm_tts_queue", "tts_error"),
        ("rss_llm_tts_queue", "tts_audio_path"),
        ("rss_video_job", "heartbeat_at"),
        ("rss_video_job", "worker_id"),
        ("pipeline_runs", "heartbeat_at"),
        ("pipeline_runs", "worker_id"),
    ]:
        if _column_exists(table_name, column_name):
            op.execute(f"ALTER TABLE {table_name} DROP COLUMN {column_name}")
