"""tts model type: add model_type and voice columns to model_configs

Revision ID: 20260629_0008
Revises: 20260629_0007
Create Date: 2026-06-29
"""

from alembic import op


revision = "20260629_0008"
down_revision = "20260629_0007"
branch_labels = None
depends_on = None


def _column_exists(table_name: str, column_name: str) -> bool:
    connection = op.get_bind()
    result = connection.exec_driver_sql(f"SHOW COLUMNS FROM {table_name} LIKE %s", (column_name,))
    return result.fetchone() is not None


def upgrade() -> None:
    # 模型配置表增加 model_type 和 voice 列，支持 TTS 配置
    if not _column_exists("model_configs", "model_type"):
        op.execute(
            "ALTER TABLE model_configs ADD COLUMN model_type VARCHAR(20) NOT NULL DEFAULT 'llm' COMMENT '模型类型: llm 或 tts'"
        )
    if not _column_exists("model_configs", "voice"):
        op.execute(
            "ALTER TABLE model_configs ADD COLUMN voice VARCHAR(100) DEFAULT NULL COMMENT 'TTS 音色名称'"
        )


def downgrade() -> None:
    if _column_exists("model_configs", "voice"):
        op.execute("ALTER TABLE model_configs DROP COLUMN voice")
    if _column_exists("model_configs", "model_type"):
        op.execute("ALTER TABLE model_configs DROP COLUMN model_type")
