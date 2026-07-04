"""add render engine to schedules

Revision ID: 20260704_0012
Revises: 20260704_0011
Create Date: 2026-07-04
"""

from alembic import op


revision = "20260704_0012"
down_revision = "20260704_0011"
branch_labels = None
depends_on = None


def _column_exists(table_name: str, column_name: str) -> bool:
    connection = op.get_bind()
    result = connection.exec_driver_sql(
        """
        SELECT COUNT(*) AS count
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND COLUMN_NAME = %s
        """,
        (table_name, column_name),
    )
    row = result.fetchone()
    return bool(row and row[0])


def upgrade() -> None:
    if not _column_exists("schedule_configs", "render_engine"):
        op.execute(
            """
            ALTER TABLE schedule_configs
              ADD COLUMN render_engine ENUM('remotion', 'ffmpeg')
              NOT NULL DEFAULT 'remotion'
              COMMENT '视频渲染方式'
              AFTER auto_publish
            """
        )


def downgrade() -> None:
    if _column_exists("schedule_configs", "render_engine"):
        op.execute("ALTER TABLE schedule_configs DROP COLUMN render_engine")
