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


def upgrade() -> None:
    # Add tts_config_id to schedule_configs
    op.add_column(
        'schedule_configs',
        sa.Column('tts_config_id', sa.Integer(), nullable=True, comment='TTS 模型配置 ID')
    )

    # Add tts_config_id to pipeline_runs
    op.add_column(
        'pipeline_runs',
        sa.Column('tts_config_id', sa.Integer(), nullable=True, comment='TTS 模型配置 ID')
    )


def downgrade() -> None:
    # Remove tts_config_id from pipeline_runs
    op.drop_column('pipeline_runs', 'tts_config_id')

    # Remove tts_config_id from schedule_configs
    op.drop_column('schedule_configs', 'tts_config_id')
