"""add tts_config_id to schedule_configs and pipeline_runs

Revision ID: 20260629_0009
Revises: 20260629_0008
Create Date: 2026-06-29

This migration adds tts_config_id column to both schedule_configs and pipeline_runs
tables, allowing each scheduled task to specify its own TTS configuration.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260629_0009'
down_revision = '20260629_0008'
branch_labels = None
depends_on = None


def _column_exists(table_name: str, column_name: str) -> bool:
    connection = op.get_bind()
    result = connection.exec_driver_sql(f"SHOW COLUMNS FROM {table_name} LIKE %s", (column_name,))
    return result.fetchone() is not None


def upgrade() -> None:
    # Add tts_config_id to schedule_configs
    if not _column_exists('schedule_configs', 'tts_config_id'):
        op.add_column(
            'schedule_configs',
            sa.Column('tts_config_id', sa.Integer(), nullable=True, comment='TTS 模型配置 ID')
        )

    # Add tts_config_id to pipeline_runs
    if not _column_exists('pipeline_runs', 'tts_config_id'):
        op.add_column(
            'pipeline_runs',
            sa.Column('tts_config_id', sa.Integer(), nullable=True, comment='TTS 模型配置 ID')
        )


def downgrade() -> None:
    # Remove tts_config_id from pipeline_runs
    if _column_exists('pipeline_runs', 'tts_config_id'):
        op.drop_column('pipeline_runs', 'tts_config_id')

    # Remove tts_config_id from schedule_configs
    if _column_exists('schedule_configs', 'tts_config_id'):
        op.drop_column('schedule_configs', 'tts_config_id')
