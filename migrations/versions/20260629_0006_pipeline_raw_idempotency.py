"""link llm raw records to pipeline runs

Revision ID: 20260629_0006
Revises: 20260629_0005
Create Date: 2026-06-29
"""

from alembic import op


revision = "20260629_0006"
down_revision = "20260629_0005"
branch_labels = None
depends_on = None


def _column_exists(table_name: str, column_name: str) -> bool:
    connection = op.get_bind()
    result = connection.exec_driver_sql(f"SHOW COLUMNS FROM {table_name} LIKE %s", (column_name,))
    return result.fetchone() is not None


def _index_exists(table_name: str, index_name: str) -> bool:
    connection = op.get_bind()
    result = connection.exec_driver_sql(f"SHOW INDEX FROM {table_name} WHERE Key_name = %s", (index_name,))
    return result.fetchone() is not None


def upgrade() -> None:
    if not _column_exists("rss_llm_raw", "pipeline_run_id"):
        op.execute("ALTER TABLE rss_llm_raw ADD COLUMN pipeline_run_id BIGINT DEFAULT NULL")

    if not _index_exists("rss_llm_raw", "uk_rss_llm_raw_pipeline_run"):
        op.execute(
            "ALTER TABLE rss_llm_raw ADD UNIQUE KEY uk_rss_llm_raw_pipeline_run (pipeline_run_id)"
        )


def downgrade() -> None:
    if _index_exists("rss_llm_raw", "uk_rss_llm_raw_pipeline_run"):
        op.execute("ALTER TABLE rss_llm_raw DROP INDEX uk_rss_llm_raw_pipeline_run")
    if _column_exists("rss_llm_raw", "pipeline_run_id"):
        op.execute("ALTER TABLE rss_llm_raw DROP COLUMN pipeline_run_id")
