"""add cancelled video status and auth session_version

Revision ID: 20260629_0005
Revises: 20260628_0004
Create Date: 2026-06-29
"""

from alembic import op


revision = "20260629_0005"
down_revision = "20260628_0004"
branch_labels = None
depends_on = None


def _column_exists(table_name: str, column_name: str) -> bool:
    connection = op.get_bind()
    result = connection.exec_driver_sql(f"SHOW COLUMNS FROM {table_name} LIKE %s", (column_name,))
    return result.fetchone() is not None


def upgrade() -> None:
    # rss_video_job.status 增加 cancelled，区分用户取消与真正失败。
    op.execute(
        """
        ALTER TABLE rss_video_job
          MODIFY COLUMN status
          ENUM('pending', 'rendering', 'done', 'failed', 'cancelled')
          NOT NULL DEFAULT 'pending'
        """
    )
    # 把历史“任务由用户取消”的 failed 记录归一为 cancelled。
    op.execute(
        """
        UPDATE rss_video_job
        SET status = 'cancelled'
        WHERE status = 'failed' AND error_message = '任务由用户取消'
        """
    )

    # auth_users 增加 session_version，用于改密/吊销时让旧 session token 失效。
    if not _column_exists("auth_users", "session_version"):
        op.execute(
            "ALTER TABLE auth_users ADD COLUMN session_version INT NOT NULL DEFAULT 0"
        )


def downgrade() -> None:
    # 回退前把 cancelled 归并为 failed，避免 ENUM 收窄丢数据。
    op.execute(
        "UPDATE rss_video_job SET status = 'failed' WHERE status = 'cancelled'"
    )
    op.execute(
        """
        ALTER TABLE rss_video_job
          MODIFY COLUMN status
          ENUM('pending', 'rendering', 'done', 'failed')
          NOT NULL DEFAULT 'pending'
        """
    )
    if _column_exists("auth_users", "session_version"):
        op.execute("ALTER TABLE auth_users DROP COLUMN session_version")
