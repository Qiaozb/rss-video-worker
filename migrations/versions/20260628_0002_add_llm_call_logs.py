"""add llm call telemetry table

Revision ID: 20260628_0002
Revises: 20260628_0001
Create Date: 2026-06-28
"""

from alembic import op


revision = "20260628_0002"
down_revision = "20260628_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_call_logs (
          id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
          pipeline_run_id BIGINT DEFAULT NULL,
          model_config_id BIGINT DEFAULT NULL,
          prompt_version_id BIGINT DEFAULT NULL,
          purpose VARCHAR(50) NOT NULL,
          model_name VARCHAR(255) NOT NULL,
          status ENUM('succeeded', 'failed') NOT NULL,
          duration_ms INT NOT NULL DEFAULT 0,
          prompt_tokens INT DEFAULT NULL,
          completion_tokens INT DEFAULT NULL,
          total_tokens INT DEFAULT NULL,
          repair_attempt INT NOT NULL DEFAULT 0,
          error_message TEXT DEFAULT NULL,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          KEY idx_llm_call_pipeline (pipeline_run_id, id),
          KEY idx_llm_call_created_at (created_at),
          KEY idx_llm_call_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS llm_call_logs")
