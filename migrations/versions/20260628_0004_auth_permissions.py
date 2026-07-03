"""add local auth users and audit logs

Revision ID: 20260628_0004
Revises: 20260628_0003
Create Date: 2026-06-28
"""

from alembic import op


revision = "20260628_0004"
down_revision = "20260628_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_users (
          id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
          username VARCHAR(100) NOT NULL,
          password_hash VARCHAR(255) NOT NULL,
          role ENUM('admin', 'editor', 'viewer') NOT NULL DEFAULT 'viewer',
          enabled TINYINT(1) NOT NULL DEFAULT 1,
          last_login_at DATETIME DEFAULT NULL,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uk_auth_users_username (username),
          KEY idx_auth_users_role_enabled (role, enabled)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_audit_logs (
          id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
          username VARCHAR(100) DEFAULT NULL,
          role VARCHAR(30) DEFAULT NULL,
          action VARCHAR(100) NOT NULL,
          method VARCHAR(10) DEFAULT NULL,
          path VARCHAR(512) DEFAULT NULL,
          status_code INT DEFAULT NULL,
          ip VARCHAR(100) DEFAULT NULL,
          user_agent VARCHAR(512) DEFAULT NULL,
          message TEXT DEFAULT NULL,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          KEY idx_auth_audit_created (created_at),
          KEY idx_auth_audit_username_created (username, created_at),
          KEY idx_auth_audit_action_created (action, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS auth_audit_logs")
    op.execute("DROP TABLE IF EXISTS auth_users")
