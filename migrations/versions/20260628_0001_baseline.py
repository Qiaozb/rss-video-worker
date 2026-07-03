"""baseline schema for local RSS video pipeline

Revision ID: 20260628_0001
Revises:
Create Date: 2026-06-28
"""

from alembic import op


revision = "20260628_0001"
down_revision = None
branch_labels = None
depends_on = None


TABLES_IN_REVERSE_DEPENDENCY_ORDER = [
    "pipeline_run_events",
    "rss_llm_tts_queue",
    "rss_video_job",
    "rss_news_dedupe",
    "rss_llm_key_news",
    "rss_llm_report",
    "rss_llm_raw",
    "rss_items",
    "rss_sources",
    "pipeline_runs",
    "schedule_configs",
    "prompt_versions",
    "model_configs",
]


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS rss_sources (
          id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
          name VARCHAR(255) NOT NULL,
          url VARCHAR(2048) NOT NULL,
          category VARCHAR(100) DEFAULT NULL,
          language VARCHAR(20) NOT NULL DEFAULT 'zh-CN',
          priority INT NOT NULL DEFAULT 100,
          enabled TINYINT(1) NOT NULL DEFAULT 1,
          request_timeout_seconds INT NOT NULL DEFAULT 20,
          last_success_at DATETIME DEFAULT NULL,
          last_error_at DATETIME DEFAULT NULL,
          consecutive_failures INT NOT NULL DEFAULT 0,
          last_error TEXT DEFAULT NULL,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uk_rss_source_url (url(512))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS rss_items (
          id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
          source_id BIGINT NOT NULL,
          title VARCHAR(500) NOT NULL,
          link VARCHAR(2048) DEFAULT NULL,
          normalized_link VARCHAR(2048) DEFAULT NULL,
          pubdate DATETIME DEFAULT NULL,
          description MEDIUMTEXT,
          content_hash CHAR(64) NOT NULL,
          raw_json JSON DEFAULT NULL,
          first_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          CONSTRAINT fk_rss_item_source FOREIGN KEY (source_id) REFERENCES rss_sources(id),
          UNIQUE KEY uk_rss_content_hash (content_hash),
          KEY idx_rss_pubdate (pubdate),
          KEY idx_rss_source_pubdate (source_id, pubdate)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS rss_llm_raw (
          id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
          raw_json JSON NOT NULL,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          KEY idx_rss_llm_raw_created_at (created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS rss_llm_report (
          id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
          raw_id BIGINT NOT NULL,
          title VARCHAR(255) DEFAULT NULL,
          daily_trend LONGTEXT,
          news_count INT NOT NULL DEFAULT 0,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uk_rss_llm_report_raw_id (raw_id),
          KEY idx_rss_llm_report_created_at (created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS rss_llm_key_news (
          id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
          report_id BIGINT NOT NULL,
          item_index INT NOT NULL,
          title VARCHAR(500) NOT NULL DEFAULT '',
          pubdate VARCHAR(100) DEFAULT NULL,
          summary TEXT,
          related_field VARCHAR(255) DEFAULT NULL,
          importance VARCHAR(10) NOT NULL DEFAULT '中',
          reserve_reason TEXT,
          link VARCHAR(2048) DEFAULT NULL,
          voiceover_script LONGTEXT,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uk_rss_llm_key_news_report_item (report_id, item_index),
          KEY idx_rss_llm_key_news_report (report_id),
          KEY idx_rss_llm_key_news_importance (importance)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS rss_news_dedupe (
          news_hash CHAR(64) NOT NULL PRIMARY KEY,
          first_report_id BIGINT NOT NULL,
          first_news_id BIGINT NOT NULL,
          title VARCHAR(500) NOT NULL DEFAULT '',
          link VARCHAR(2048) DEFAULT NULL,
          first_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          published_at DATETIME DEFAULT NULL,
          KEY idx_rss_news_dedupe_published_at (published_at),
          KEY idx_rss_news_dedupe_first_report (first_report_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS rss_llm_tts_queue (
          id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
          report_id BIGINT NOT NULL,
          news_id BIGINT NOT NULL,
          item_index INT NOT NULL,
          report_title VARCHAR(255) DEFAULT NULL,
          daily_trend LONGTEXT,
          news_count INT NOT NULL DEFAULT 0,
          news_title VARCHAR(500) NOT NULL DEFAULT '',
          pubdate VARCHAR(100) DEFAULT NULL,
          summary TEXT,
          related_field VARCHAR(255) DEFAULT NULL,
          importance VARCHAR(10) NOT NULL DEFAULT '中',
          reserve_reason TEXT,
          link VARCHAR(2048) DEFAULT NULL,
          voiceover_script LONGTEXT,
          tts_text LONGTEXT,
          tts_status VARCHAR(30) NOT NULL DEFAULT 'pending',
          tts_audio_path VARCHAR(1024) DEFAULT NULL,
          tts_error TEXT DEFAULT NULL,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uk_rss_llm_tts_queue_news (news_id),
          KEY idx_rss_llm_tts_queue_report_item (report_id, item_index),
          KEY idx_rss_llm_tts_queue_status (tts_status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS rss_video_job (
          id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
          report_id BIGINT UNSIGNED NOT NULL,
          status ENUM('pending', 'rendering', 'done', 'failed') NOT NULL DEFAULT 'pending',
          video_title VARCHAR(255) NOT NULL,
          video_path VARCHAR(1024) DEFAULT NULL,
          cover_path VARCHAR(1024) DEFAULT NULL,
          duration_seconds DECIMAL(10,2) DEFAULT NULL,
          item_count INT UNSIGNED NOT NULL DEFAULT 0,
          progress_percent DECIMAL(5,2) NOT NULL DEFAULT 0,
          current_step VARCHAR(255) DEFAULT NULL,
          error_message TEXT,
          created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uk_report_id (report_id),
          KEY idx_status (status),
          KEY idx_created_at (created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS schedule_configs (
          id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
          name VARCHAR(100) NOT NULL,
          task_type ENUM('rss_collect', 'daily_report') NOT NULL,
          cron_expression VARCHAR(100) NOT NULL,
          timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Shanghai',
          enabled TINYINT(1) NOT NULL DEFAULT 1,
          prevent_overlap TINYINT(1) NOT NULL DEFAULT 1,
          max_runtime_seconds INT NOT NULL DEFAULT 3600,
          retry_count INT NOT NULL DEFAULT 2,
          retry_interval_seconds INT NOT NULL DEFAULT 300,
          next_run_at DATETIME DEFAULT NULL,
          last_run_at DATETIME DEFAULT NULL,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uk_schedule_task_type (task_type),
          KEY idx_schedule_next_run (enabled, next_run_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS pipeline_runs (
          id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
          task_type VARCHAR(50) NOT NULL,
          trigger_type ENUM('schedule', 'manual', 'retry') NOT NULL,
          schedule_id BIGINT DEFAULT NULL,
          report_id BIGINT DEFAULT NULL,
          status ENUM('pending', 'running', 'succeeded', 'failed', 'cancelled') NOT NULL,
          progress_percent DECIMAL(5,2) NOT NULL DEFAULT 0,
          current_step VARCHAR(255) DEFAULT NULL,
          processed_count INT NOT NULL DEFAULT 0,
          total_count INT NOT NULL DEFAULT 0,
          task_payload JSON DEFAULT NULL,
          error_message TEXT DEFAULT NULL,
          started_at DATETIME DEFAULT NULL,
          finished_at DATETIME DEFAULT NULL,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          KEY idx_pipeline_status_created (status, created_at),
          KEY idx_pipeline_schedule (schedule_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS pipeline_run_events (
          id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
          run_id BIGINT NOT NULL,
          level ENUM('info', 'warning', 'error') NOT NULL DEFAULT 'info',
          stage VARCHAR(100) NOT NULL,
          message TEXT NOT NULL,
          progress_percent DECIMAL(5,2) DEFAULT NULL,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CONSTRAINT fk_pipeline_event_run FOREIGN KEY (run_id) REFERENCES pipeline_runs(id),
          KEY idx_pipeline_event_run_id (run_id, id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS model_configs (
          id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
          name VARCHAR(100) NOT NULL,
          provider VARCHAR(50) NOT NULL DEFAULT 'openai-compatible',
          base_url VARCHAR(1024) NOT NULL,
          model_name VARCHAR(255) NOT NULL,
          encrypted_api_key TEXT NOT NULL,
          timeout_seconds INT NOT NULL DEFAULT 180,
          temperature DECIMAL(4,2) NOT NULL DEFAULT 0.20,
          max_retries INT NOT NULL DEFAULT 2,
          enabled TINYINT(1) NOT NULL DEFAULT 1,
          is_default TINYINT(1) NOT NULL DEFAULT 0,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uk_model_config_name (name),
          KEY idx_model_enabled_default (enabled, is_default)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS prompt_versions (
          id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
          name VARCHAR(100) NOT NULL,
          system_prompt LONGTEXT NOT NULL,
          user_prompt_template LONGTEXT NOT NULL,
          enabled TINYINT(1) NOT NULL DEFAULT 1,
          is_default TINYINT(1) NOT NULL DEFAULT 0,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uk_prompt_version_name (name),
          KEY idx_prompt_enabled_default (enabled, is_default)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )


def downgrade() -> None:
    for table_name in TABLES_IN_REVERSE_DEPENDENCY_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table_name}")
