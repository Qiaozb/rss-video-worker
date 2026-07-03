"""add tts voice management

Revision ID: 20260702_0010
Revises: 20260629_0009
Create Date: 2026-07-02
"""

from alembic import op


revision = "20260702_0010"
down_revision = "20260629_0009"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    connection = op.get_bind()
    result = connection.exec_driver_sql("SHOW TABLES LIKE %s", (table_name,))
    return result.fetchone() is not None


def upgrade() -> None:
    if _table_exists("tts_voices"):
        return
    op.execute(
        """
        CREATE TABLE tts_voices (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          name VARCHAR(100) NOT NULL,
          language_code VARCHAR(20) NOT NULL DEFAULT 'zh-CN',
          voice VARCHAR(100) NOT NULL,
          model_config_id BIGINT DEFAULT NULL,
          sample_text VARCHAR(500) NOT NULL DEFAULT '大家好，欢迎收看今天的新闻播报。',
          enabled TINYINT(1) NOT NULL DEFAULT 1,
          is_default TINYINT(1) NOT NULL DEFAULT 0,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uk_tts_voice_name (name),
          KEY idx_tts_voice_enabled_default (enabled, is_default),
          KEY idx_tts_voice_model (model_config_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )


def downgrade() -> None:
    if _table_exists("tts_voices"):
        op.execute("DROP TABLE tts_voices")
