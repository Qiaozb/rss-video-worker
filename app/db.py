from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pymysql
from pymysql.cursors import DictCursor

from app.config import settings
from app.default_prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from app.scheduler import ScheduleConfig, next_run_after, utc_now
from app.secrets import decrypt_secret, encrypt_secret, mask_secret


WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


@dataclass
class TTSItem:
    id: int
    report_id: int
    news_id: int
    item_index: int
    report_title: str
    daily_trend: str
    news_count: int
    news_title: str
    pubdate: Optional[str]
    summary: str
    related_field: Optional[str]
    importance: str
    reserve_reason: Optional[str]
    link: Optional[str]
    voiceover_script: Optional[str]
    tts_text: Optional[str]
    tts_status: str = "pending"
    tts_audio_path: Optional[str] = None
    tts_error: Optional[str] = None

    @property
    def narration(self) -> str:
        return (self.voiceover_script or self.tts_text or self.summary or self.news_title).strip()


@dataclass
class RSSSource:
    id: int
    name: str
    url: str
    category: Optional[str]
    language: str
    priority: int
    enabled: int
    request_timeout_seconds: int


@dataclass
class ModelConfig:
    id: int
    name: str
    provider: str
    base_url: str
    model_name: str
    api_key: str
    timeout_seconds: int
    temperature: float
    max_retries: int
    enabled: int
    is_default: int
    model_type: str = "llm"
    voice: Optional[str] = None


@dataclass
class TTSVoice:
    id: int
    name: str
    language_code: str
    voice: str
    model_config_id: Optional[int]
    sample_text: str
    enabled: int
    is_default: int


@dataclass
class PromptVersion:
    id: int
    name: str
    system_prompt: str
    user_prompt_template: str
    enabled: int
    is_default: int


def get_connection(autocommit: bool = True):
    return pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=autocommit,
    )


def _add_columns_if_missing(cursor: Any, table_name: str, column_ddls: List[str]) -> None:
    """幂等加列：列已存在时跳过（MySQL error 1060）。column_ddls 形如 ['col TYPE DEFAULT ...']。"""
    for ddl in column_ddls:
        col_name = ddl.split()[0]
        try:
            cursor.execute(f"SHOW COLUMNS FROM {table_name} LIKE %s", (col_name,))
            if cursor.fetchone():
                continue
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {ddl}")
        except pymysql.err.OperationalError as exc:
            if exc.args and exc.args[0] == 1060:
                continue
            raise


def _replace_unique_key(
    cursor: Any,
    table_name: str,
    old_name: str,
    new_name: str,
    new_ddl: str,
) -> None:
    """把唯一键从 old_name 换成 new_name(new_ddl 形如 '(a, b)')。两者均幂等。"""
    if _index_exists(cursor, table_name, old_name):
        cursor.execute(f"ALTER TABLE {table_name} DROP INDEX {old_name}")
    if not _index_exists(cursor, table_name, new_name):
        cursor.execute(f"ALTER TABLE {table_name} ADD UNIQUE KEY {new_name} {new_ddl}")


def ensure_auth_tables() -> None:
    sql_users = """
    CREATE TABLE IF NOT EXISTS auth_users (
      id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
      username VARCHAR(100) NOT NULL,
      password_hash VARCHAR(255) NOT NULL,
      role ENUM('admin', 'editor', 'viewer') NOT NULL DEFAULT 'viewer',
      enabled TINYINT(1) NOT NULL DEFAULT 1,
      session_version INT NOT NULL DEFAULT 0,
      last_login_at DATETIME DEFAULT NULL,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      UNIQUE KEY uk_auth_users_username (username),
      KEY idx_auth_users_role_enabled (role, enabled)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """
    sql_audit = """
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
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql_users)
            cursor.execute(sql_audit)
            # auth_users 增加 session_version（与 migration 0005 对应，老库自愈）。
            try:
                cursor.execute(
                    "ALTER TABLE auth_users ADD COLUMN session_version INT NOT NULL DEFAULT 0"
                )
            except pymysql.err.OperationalError as exc:
                if not (exc.args and exc.args[0] == 1060):
                    raise


def get_auth_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    sql = "SELECT * FROM auth_users WHERE username = %s LIMIT 1"
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (username,))
            return cursor.fetchone()


def get_auth_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    sql = """
    SELECT id, username, role, enabled, session_version, last_login_at, created_at, updated_at
    FROM auth_users
    WHERE id = %s
    LIMIT 1
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (user_id,))
            return cursor.fetchone()


def list_auth_users() -> List[Dict[str, Any]]:
    sql = """
    SELECT id, username, role, enabled, session_version, last_login_at, created_at, updated_at
    FROM auth_users
    ORDER BY id ASC
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            return list(cursor.fetchall())


def create_auth_user(username: str, password_hash: str, role: str, enabled: bool = True) -> int:
    sql = """
    INSERT INTO auth_users (username, password_hash, role, enabled)
    VALUES (%s, %s, %s, %s)
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (username, password_hash, role, int(enabled)))
            return int(cursor.lastrowid)


def update_auth_user(user_id: int, role: str, enabled: bool) -> bool:
    sql = """
    UPDATE auth_users
    SET role = %s, enabled = %s
    WHERE id = %s
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (role, int(enabled), user_id))
            return cursor.rowcount > 0


def update_auth_user_password(user_id: int, password_hash: str) -> bool:
    sql = "UPDATE auth_users SET password_hash = %s, session_version = session_version + 1 WHERE id = %s"
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (password_hash, user_id))
            return cursor.rowcount > 0


def bump_auth_user_session_version(user_id: int) -> bool:
    """递增 session_version，使该用户已签发的旧 session token 立即失效。"""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE auth_users SET session_version = session_version + 1 WHERE id = %s",
                (user_id,),
            )
            return cursor.rowcount > 0


def mark_auth_user_login(username: str) -> None:
    sql = "UPDATE auth_users SET last_login_at = CURRENT_TIMESTAMP WHERE username = %s"
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (username,))


def ensure_default_admin_user(username: str, password_hash: str) -> None:
    if not username or not password_hash:
        return
    sql = """
    INSERT INTO auth_users (username, password_hash, role, enabled)
    VALUES (%s, %s, 'admin', 1)
    ON DUPLICATE KEY UPDATE
      password_hash = VALUES(password_hash),
      role = 'admin',
      enabled = 1
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (username, password_hash))


def insert_audit_log(
    *,
    username: Optional[str],
    role: Optional[str],
    action: str,
    method: Optional[str] = None,
    path: Optional[str] = None,
    status_code: Optional[int] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    message: Optional[str] = None,
) -> None:
    sql = """
    INSERT INTO auth_audit_logs (
      username, role, action, method, path, status_code, ip, user_agent, message
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                sql,
                (
                    username,
                    role,
                    action,
                    method,
                    path,
                    status_code,
                    ip,
                    (user_agent or "")[:512] if user_agent else None,
                    message,
                ),
            )


def list_audit_logs(limit: int = 100) -> List[Dict[str, Any]]:
    sql = """
    SELECT id, username, role, action, method, path, status_code, ip, user_agent, message, created_at
    FROM auth_audit_logs
    ORDER BY id DESC
    LIMIT %s
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (max(1, min(limit, 500)),))
            return list(cursor.fetchall())


def ensure_video_job_table() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS rss_video_job (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      report_id BIGINT UNSIGNED NOT NULL,
      status ENUM('pending', 'rendering', 'done', 'failed', 'cancelled') NOT NULL DEFAULT 'pending',
      video_title VARCHAR(255) NOT NULL,
      video_path VARCHAR(1024) DEFAULT NULL,
      cover_path VARCHAR(1024) DEFAULT NULL,
      duration_seconds DECIMAL(10,2) DEFAULT NULL,
      item_count INT UNSIGNED NOT NULL DEFAULT 0,
      progress_percent DECIMAL(5,2) NOT NULL DEFAULT 0,
      current_step VARCHAR(255) DEFAULT NULL,
      worker_id VARCHAR(255) DEFAULT NULL,
      heartbeat_at DATETIME DEFAULT NULL,
      error_message TEXT,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      UNIQUE KEY uk_report_id (report_id),
      KEY idx_status (status),
      KEY idx_created_at (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            for ddl in [
                "ALTER TABLE rss_video_job ADD COLUMN progress_percent DECIMAL(5,2) NOT NULL DEFAULT 0",
                "ALTER TABLE rss_video_job ADD COLUMN current_step VARCHAR(255) DEFAULT NULL",
                "ALTER TABLE rss_video_job ADD COLUMN worker_id VARCHAR(255) DEFAULT NULL",
                "ALTER TABLE rss_video_job ADD COLUMN heartbeat_at DATETIME DEFAULT NULL",
            ]:
                try:
                    cursor.execute(ddl)
                except pymysql.err.OperationalError as exc:
                    if exc.args and exc.args[0] == 1060:
                        continue
                    raise
            # status ENUM 增加 cancelled（与 migration 0005 对应，老库自愈）。重复 MODIFY 幂等。
            cursor.execute(
                """
                ALTER TABLE rss_video_job
                  MODIFY COLUMN status
                  ENUM('pending', 'rendering', 'done', 'failed', 'cancelled')
                  NOT NULL DEFAULT 'pending'
                """
            )


def ensure_schedule_tables() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS schedule_configs (
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
      name VARCHAR(100) NOT NULL,
      task_type ENUM('rss_collect', 'daily_report') NOT NULL,
      cron_expression VARCHAR(100) NOT NULL,
      timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Shanghai',
      enabled TINYINT(1) NOT NULL DEFAULT 1,
      prevent_overlap TINYINT(1) NOT NULL DEFAULT 1,
      max_runtime_seconds INT NOT NULL DEFAULT 3600,
      retry_count INT NOT NULL DEFAULT 2,
      retry_interval_seconds INT NOT NULL DEFAULT 300,
      rss_category VARCHAR(100) DEFAULT NULL COMMENT 'RSS 分类过滤，NULL 表示全部',
      model_config_id BIGINT DEFAULT NULL COMMENT '使用的模型配置',
      prompt_version_id BIGINT DEFAULT NULL COMMENT '使用的提示词版本',
      tts_config_id BIGINT DEFAULT NULL COMMENT '使用的 TTS 模型配置',
      report_type VARCHAR(50) NOT NULL DEFAULT 'general' COMMENT '报告类型',
      auto_render TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否自动生成视频',
      auto_publish TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否自动标记已发布',
      next_run_at DATETIME DEFAULT NULL,
      last_run_at DATETIME DEFAULT NULL,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      UNIQUE KEY uk_schedule_name (name),
      KEY idx_schedule_next_run (enabled, next_run_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            # 老库自愈：补齐阶段一新列，并把 task_type 唯一键换成 name 唯一键
            _add_columns_if_missing(
                cursor,
                "schedule_configs",
                [
                    "rss_category VARCHAR(100) DEFAULT NULL COMMENT 'RSS 分类过滤，NULL 表示全部'",
                    "model_config_id BIGINT DEFAULT NULL COMMENT '使用的模型配置'",
                    "prompt_version_id BIGINT DEFAULT NULL COMMENT '使用的提示词版本'",
                    "tts_config_id BIGINT DEFAULT NULL COMMENT '使用的 TTS 模型配置'",
                    "report_type VARCHAR(50) NOT NULL DEFAULT 'general' COMMENT '报告类型'",
                    "auto_render TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否自动生成视频'",
                    "auto_publish TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否自动标记已发布'",
                ],
            )
            _replace_unique_key(cursor, "schedule_configs", "uk_schedule_task_type", "uk_schedule_name", "(name)")
            _ensure_default_schedule(
                cursor,
                name="RSS 每两小时采集",
                task_type="rss_collect",
                cron_expression="0 */2 * * *",
                tz_name="Asia/Shanghai",
            )
            _ensure_default_schedule(
                cursor,
                name="每日 17 点生成游戏行业日报",
                task_type="daily_report",
                cron_expression="0 17 * * *",
                tz_name="Asia/Shanghai",
            )


def ensure_pipeline_tables() -> None:
    runs_sql = """
    CREATE TABLE IF NOT EXISTS pipeline_runs (
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
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
      worker_id VARCHAR(255) DEFAULT NULL,
      heartbeat_at DATETIME DEFAULT NULL,
      error_message TEXT DEFAULT NULL,
      started_at DATETIME DEFAULT NULL,
      finished_at DATETIME DEFAULT NULL,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      KEY idx_pipeline_status_created (status, created_at),
      KEY idx_pipeline_schedule (schedule_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """
    events_sql = """
    CREATE TABLE IF NOT EXISTS pipeline_run_events (
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
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
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(runs_sql)
            cursor.execute(events_sql)
            try:
                cursor.execute("ALTER TABLE pipeline_runs ADD COLUMN task_payload JSON DEFAULT NULL")
            except pymysql.err.OperationalError as exc:
                if exc.args and exc.args[0] == 1060:
                    pass
                else:
                    raise
            for ddl in [
                "ALTER TABLE pipeline_runs ADD COLUMN worker_id VARCHAR(255) DEFAULT NULL",
                "ALTER TABLE pipeline_runs ADD COLUMN heartbeat_at DATETIME DEFAULT NULL",
            ]:
                try:
                    cursor.execute(ddl)
                except pymysql.err.OperationalError as exc:
                    if exc.args and exc.args[0] == 1060:
                        continue
                    raise
            # 阶段 1.2：pipeline_runs 记录每次运行实际使用的分析配置
            _add_columns_if_missing(
                cursor,
                "pipeline_runs",
                [
                    "rss_category VARCHAR(100) DEFAULT NULL",
                    "model_config_id BIGINT DEFAULT NULL",
                    "prompt_version_id BIGINT DEFAULT NULL",
                    "tts_config_id BIGINT DEFAULT NULL",
                    "report_type VARCHAR(50) DEFAULT NULL",
                ],
            )


def ensure_model_config_table() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS model_configs (
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
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
      model_type VARCHAR(20) NOT NULL DEFAULT 'llm',
      voice VARCHAR(100) DEFAULT NULL,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      UNIQUE KEY uk_model_config_name (name),
      KEY idx_model_enabled_default (enabled, is_default)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            _add_columns_if_missing(
                cursor,
                "model_configs",
                [
                    "temperature DECIMAL(4,2) NOT NULL DEFAULT 0.20",
                    "max_retries INT NOT NULL DEFAULT 2",
                    "model_type VARCHAR(20) NOT NULL DEFAULT 'llm' COMMENT '模型类型: llm 或 tts'",
                    "voice VARCHAR(100) DEFAULT NULL COMMENT 'TTS 音色名称'",
                ],
            )
            if settings.llm_api_key:
                cursor.execute("SELECT id FROM model_configs WHERE is_default = 1 LIMIT 1")
                if not cursor.fetchone():
                    cursor.execute(
                        """
                        INSERT INTO model_configs (
                          name,
                          provider,
                          base_url,
                          model_name,
                          encrypted_api_key,
                          timeout_seconds,
                          temperature,
                          enabled,
                          is_default
                        )
                        VALUES (%s, 'openai-compatible', %s, %s, %s, %s, %s, 1, 1)
                        """,
                        (
                            "默认模型",
                            settings.llm_base_url,
                            settings.llm_model,
                            encrypt_secret(settings.llm_api_key),
                            settings.llm_timeout_seconds,
                            settings.llm_temperature,
                        ),
                    )


def ensure_tts_voice_table() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS tts_voices (
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
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            _add_columns_if_missing(
                cursor,
                "tts_voices",
                [
                    "model_config_id BIGINT DEFAULT NULL",
                    "sample_text VARCHAR(500) NOT NULL DEFAULT '大家好，欢迎收看今天的新闻播报。'",
                    "enabled TINYINT(1) NOT NULL DEFAULT 1",
                    "is_default TINYINT(1) NOT NULL DEFAULT 0",
                ],
            )
            cursor.execute("SELECT id FROM tts_voices WHERE is_default = 1 LIMIT 1")
            if not cursor.fetchone() and settings.tts_voice:
                cursor.execute(
                    """
                    INSERT INTO tts_voices (
                      name, language_code, voice, model_config_id, sample_text, enabled, is_default
                    )
                    VALUES (%s, 'zh-CN', %s, NULL, %s, 1, 1)
                    ON DUPLICATE KEY UPDATE
                      voice = VALUES(voice),
                      enabled = 1,
                      is_default = 1
                    """,
                    (
                        f"默认中文音色 {settings.tts_voice}",
                        settings.tts_voice,
                        "大家好，欢迎收看今天的新闻播报。",
                    ),
                )


def ensure_prompt_version_table() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS prompt_versions (
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
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
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            cursor.execute("SELECT id FROM prompt_versions WHERE is_default = 1 LIMIT 1")
            if not cursor.fetchone():
                cursor.execute(
                    """
                    INSERT INTO prompt_versions (
                      name,
                      system_prompt,
                      user_prompt_template,
                      enabled,
                      is_default
                    )
                    VALUES (%s, %s, %s, 1, 1)
                    ON DUPLICATE KEY UPDATE
                      enabled = 1,
                      is_default = 1
                    """,
                    ("默认游戏新闻筛选提示词", SYSTEM_PROMPT, USER_PROMPT_TEMPLATE),
                )


def ensure_llm_call_log_table() -> None:
    sql = """
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
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql)


def _index_exists(cursor: Any, table_name: str, index_name: str) -> bool:
    cursor.execute(
        "SHOW INDEX FROM `{}` WHERE Key_name = %s".format(table_name),
        (index_name,),
    )
    return cursor.fetchone() is not None


def _get_pk_columns(cursor: Any, table_name: str) -> List[str]:
    """按 Seq_in_index 顺序返回主键列名列表；无主键时返回空列表。"""
    cursor.execute(
        """
        SELECT COLUMN_NAME AS Column_name
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND INDEX_NAME = 'PRIMARY'
        ORDER BY SEQ_IN_INDEX
        """,
        (table_name,),
    )
    return [row["Column_name"] for row in cursor.fetchall()]


def ensure_llm_raw_table() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS rss_llm_raw (
      id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
      raw_json JSON NOT NULL,
      pipeline_run_id BIGINT DEFAULT NULL,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE KEY uk_rss_llm_raw_pipeline_run (pipeline_run_id),
      KEY idx_rss_llm_raw_created_at (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            try:
                cursor.execute(
                    "ALTER TABLE rss_llm_raw ADD COLUMN pipeline_run_id BIGINT DEFAULT NULL"
                )
            except pymysql.err.OperationalError as exc:
                if exc.args and exc.args[0] == 1060:
                    pass
                else:
                    raise
            if not _index_exists(cursor, "rss_llm_raw", "uk_rss_llm_raw_pipeline_run"):
                cursor.execute(
                    "ALTER TABLE rss_llm_raw ADD UNIQUE KEY uk_rss_llm_raw_pipeline_run (pipeline_run_id)"
                )


def ensure_rss_report_tables() -> None:
    """rss_llm_report / rss_news_dedupe / rss_llm_tts_queue 的阶段 1.3 / 5.2 / 6 自愈。"""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            # 阶段 1.3：rss_llm_report 关联任务类型与生成上下文
            _add_columns_if_missing(
                cursor,
                "rss_llm_report",
                [
                    "report_type VARCHAR(50) NOT NULL DEFAULT 'general'",
                    "rss_category VARCHAR(100) DEFAULT NULL",
                    "model_config_id BIGINT DEFAULT NULL",
                    "prompt_version_id BIGINT DEFAULT NULL",
                    "pipeline_run_id BIGINT DEFAULT NULL",
                ],
            )

            # 阶段 5.2：tts 队列区分报告类型
            _add_columns_if_missing(cursor, "rss_llm_tts_queue", ["report_type VARCHAR(50) DEFAULT NULL"])

            # 阶段 6：去重按 report_type 隔离
            _add_columns_if_missing(
                cursor,
                "rss_news_dedupe",
                ["report_type VARCHAR(50) NOT NULL DEFAULT 'general'"],
            )
            # 用首条报告的类型回填历史行
            cursor.execute(
                """
                UPDATE rss_news_dedupe d
                JOIN rss_llm_report r ON r.id = d.first_report_id
                SET d.report_type = COALESCE(NULLIF(r.report_type, ''), 'general')
                WHERE d.report_type = 'general'
                  AND r.report_type IS NOT NULL
                  AND r.report_type <> ''
                """
            )
            # rss_news_dedupe：news_hash 原本是 PRIMARY KEY，需要替换为
            # (report_type, news_hash) 复合主键以实现按 report_type 隔离去重。
            cursor.execute(
                "SHOW INDEX FROM `rss_news_dedupe` WHERE Key_name = 'PRIMARY'"
            )
            if cursor.fetchone() is not None:
                pk_cols = _get_pk_columns(cursor, "rss_news_dedupe")
                if pk_cols == ["news_hash"]:
                    cursor.execute("ALTER TABLE rss_news_dedupe DROP PRIMARY KEY")
            if not _index_exists(cursor, "rss_news_dedupe", "PRIMARY"):
                cursor.execute(
                    "ALTER TABLE rss_news_dedupe ADD PRIMARY KEY (report_type, news_hash)"
                )


def _ensure_default_schedule(
    cursor: Any,
    name: str,
    task_type: str,
    cron_expression: str,
    tz_name: str,
) -> None:
    cursor.execute("SELECT id FROM schedule_configs WHERE task_type = %s", (task_type,))
    if cursor.fetchone():
        return
    cursor.execute(
        """
        INSERT INTO schedule_configs (
          name,
          task_type,
          cron_expression,
          timezone,
          next_run_at
        )
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            name,
            task_type,
            cron_expression,
            tz_name,
            next_run_after(cron_expression, tz_name),
        ),
    )


def record_llm_call(
    purpose: str,
    model_name: str,
    status: str,
    duration_ms: int,
    pipeline_run_id: Optional[int] = None,
    model_config_id: Optional[int] = None,
    prompt_version_id: Optional[int] = None,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None,
    repair_attempt: int = 0,
    error_message: Optional[str] = None,
) -> None:
    """Best-effort LLM telemetry. Never break the main pipeline."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO llm_call_logs (
                      pipeline_run_id,
                      model_config_id,
                      prompt_version_id,
                      purpose,
                      model_name,
                      status,
                      duration_ms,
                      prompt_tokens,
                      completion_tokens,
                      total_tokens,
                      repair_attempt,
                      error_message
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        pipeline_run_id,
                        model_config_id,
                        prompt_version_id,
                        purpose,
                        model_name,
                        status,
                        max(0, int(duration_ms)),
                        prompt_tokens,
                        completion_tokens,
                        total_tokens,
                        max(0, int(repair_attempt)),
                        error_message[:6000] if error_message else None,
                    ),
                )
    except Exception:
        return


def list_llm_call_logs(
    limit: int = 100,
    pipeline_run_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    sql = """
    SELECT
      id,
      pipeline_run_id,
      model_config_id,
      prompt_version_id,
      purpose,
      model_name,
      status,
      duration_ms,
      prompt_tokens,
      completion_tokens,
      total_tokens,
      repair_attempt,
      error_message,
      created_at
    FROM llm_call_logs
    """
    params: tuple[Any, ...]
    if pipeline_run_id is not None:
        sql += " WHERE pipeline_run_id = %s"
        params = (pipeline_run_id, limit)
    else:
        params = (limit,)
    sql += " ORDER BY id DESC LIMIT %s"

    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                return cursor.fetchall()
    except Exception:
        return []


def llm_call_stats(days: int = 7) -> Dict[str, Any]:
    safe_days = max(1, min(int(days), 365))
    where_sql = "created_at >= UTC_TIMESTAMP() - INTERVAL %s DAY"
    params = (safe_days,)

    def _fetch_one(cursor: Any, sql: str) -> Dict[str, Any]:
        cursor.execute(sql, params)
        return cursor.fetchone() or {}

    def _fetch_all(cursor: Any, sql: str) -> List[Dict[str, Any]]:
        cursor.execute(sql, params)
        return cursor.fetchall()

    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                summary = _fetch_one(
                    cursor,
                    f"""
                    SELECT
                      COUNT(*) AS total_calls,
                      SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded_calls,
                      SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_calls,
                      ROUND(AVG(duration_ms), 0) AS avg_duration_ms,
                      MAX(duration_ms) AS max_duration_ms,
                      COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                      COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                      COALESCE(SUM(total_tokens), 0) AS total_tokens,
                      SUM(CASE WHEN repair_attempt > 0 THEN 1 ELSE 0 END) AS repair_calls,
                      COUNT(DISTINCT model_name) AS model_count,
                      MIN(created_at) AS first_call_at,
                      MAX(created_at) AS last_call_at
                    FROM llm_call_logs
                    WHERE {where_sql}
                    """,
                )
                by_day = _fetch_all(
                    cursor,
                    f"""
                    SELECT
                      DATE(created_at) AS day,
                      COUNT(*) AS total_calls,
                      SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded_calls,
                      SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_calls,
                      ROUND(AVG(duration_ms), 0) AS avg_duration_ms,
                      COALESCE(SUM(total_tokens), 0) AS total_tokens,
                      SUM(CASE WHEN repair_attempt > 0 THEN 1 ELSE 0 END) AS repair_calls
                    FROM llm_call_logs
                    WHERE {where_sql}
                    GROUP BY DATE(created_at)
                    ORDER BY day DESC
                    """,
                )
                by_model = _fetch_all(
                    cursor,
                    f"""
                    SELECT
                      model_name,
                      COUNT(*) AS total_calls,
                      SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded_calls,
                      SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_calls,
                      ROUND(AVG(duration_ms), 0) AS avg_duration_ms,
                      COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                      COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                      COALESCE(SUM(total_tokens), 0) AS total_tokens,
                      SUM(CASE WHEN repair_attempt > 0 THEN 1 ELSE 0 END) AS repair_calls
                    FROM llm_call_logs
                    WHERE {where_sql}
                    GROUP BY model_name
                    ORDER BY total_calls DESC, model_name ASC
                    """,
                )
                by_purpose = _fetch_all(
                    cursor,
                    f"""
                    SELECT
                      purpose,
                      COUNT(*) AS total_calls,
                      SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded_calls,
                      SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_calls,
                      ROUND(AVG(duration_ms), 0) AS avg_duration_ms,
                      COALESCE(SUM(total_tokens), 0) AS total_tokens,
                      SUM(CASE WHEN repair_attempt > 0 THEN 1 ELSE 0 END) AS repair_calls
                    FROM llm_call_logs
                    WHERE {where_sql}
                    GROUP BY purpose
                    ORDER BY total_calls DESC, purpose ASC
                    """,
                )

        total_calls = int(summary.get("total_calls") or 0)
        succeeded_calls = int(summary.get("succeeded_calls") or 0)
        failed_calls = int(summary.get("failed_calls") or 0)
        summary["success_rate"] = (
            round(succeeded_calls / total_calls * 100, 2) if total_calls else 0
        )
        summary["failure_rate"] = (
            round(failed_calls / total_calls * 100, 2) if total_calls else 0
        )
        return {
            "days": safe_days,
            "summary": summary,
            "by_day": by_day,
            "by_model": by_model,
            "by_purpose": by_purpose,
        }
    except Exception as exc:
        return {
            "days": safe_days,
            "summary": {
                "total_calls": 0,
                "succeeded_calls": 0,
                "failed_calls": 0,
                "success_rate": 0,
                "failure_rate": 0,
            },
            "by_day": [],
            "by_model": [],
            "by_purpose": [],
            "error": str(exc)[:1000],
        }


def get_latest_report_id() -> Optional[int]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT r.id
                FROM rss_llm_report r
                WHERE EXISTS (
                    SELECT 1
                    FROM rss_llm_tts_queue q
                    WHERE q.report_id = r.id
                )
                ORDER BY r.id DESC
                LIMIT 1
                """
            )
            row = cursor.fetchone()
            return int(row["id"]) if row else None


def list_model_configs(model_type: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            if model_type:
                cursor.execute(
                    """
                    SELECT
                      id,
                      name,
                      provider,
                      base_url,
                      model_name,
                      encrypted_api_key,
                      timeout_seconds,
                      temperature,
                      max_retries,
                      enabled,
                      is_default,
                      model_type,
                      voice,
                      created_at,
                      updated_at
                    FROM model_configs
                    WHERE model_type = %s
                    ORDER BY is_default DESC, enabled DESC, id ASC
                    """,
                    (model_type,),
                )
            else:
                cursor.execute(
                    """
                    SELECT
                      id,
                      name,
                      provider,
                      base_url,
                      model_name,
                      encrypted_api_key,
                      timeout_seconds,
                      temperature,
                      max_retries,
                      enabled,
                      is_default,
                      model_type,
                      voice,
                      created_at,
                      updated_at
                    FROM model_configs
                    ORDER BY is_default DESC, enabled DESC, id ASC
                    """
                )
            rows = cursor.fetchall()
            for row in rows:
                try:
                    api_key = decrypt_secret(row.pop("encrypted_api_key") or "")
                except Exception:
                    api_key = ""
                row["api_key_masked"] = mask_secret(api_key)
            return rows


def upsert_model_config(
    name: str,
    base_url: str,
    model_name: str,
    api_key: Optional[str],
    provider: str = "openai-compatible",
    timeout_seconds: int = 180,
    temperature: float = 0.2,
    max_retries: int = 2,
    enabled: bool = True,
    is_default: bool = False,
    model_type: str = "llm",
    voice: Optional[str] = None,
    config_id: Optional[int] = None,
) -> int:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            # 编辑模式：按 ID 查找已有记录，保留原 api_key（若未提供新值）
            if config_id:
                cursor.execute(
                    "SELECT encrypted_api_key FROM model_configs WHERE id = %s",
                    (config_id,),
                )
                existing = cursor.fetchone()
                if not existing:
                    raise RuntimeError(f"模型配置不存在 (id={config_id})")
            else:
                cursor.execute(
                    "SELECT encrypted_api_key FROM model_configs WHERE name = %s",
                    (name,),
                )
                existing = cursor.fetchone()

            encrypted_api_key = (
                encrypt_secret(api_key)
                if api_key
                else (existing["encrypted_api_key"] if existing else "")
            )

            if is_default:
                cursor.execute(
                    "UPDATE model_configs SET is_default = 0 WHERE model_type = %s",
                    (model_type,),
                )

            # 编辑模式：按 ID 更新
            if config_id:
                cursor.execute(
                    """
                    UPDATE model_configs
                    SET name = %s,
                        provider = %s,
                        base_url = %s,
                        model_name = %s,
                        encrypted_api_key = %s,
                        timeout_seconds = %s,
                        temperature = %s,
                        max_retries = %s,
                        enabled = %s,
                        is_default = %s,
                        model_type = %s,
                        voice = %s
                    WHERE id = %s
                    """,
                    (
                        name,
                        provider,
                        base_url,
                        model_name,
                        encrypted_api_key,
                        timeout_seconds,
                        temperature,
                        max_retries,
                        1 if enabled else 0,
                        1 if is_default else 0,
                        model_type,
                        voice,
                        config_id,
                    ),
                )
                return config_id

            # 新建模式：INSERT ... ON DUPLICATE KEY UPDATE
            cursor.execute(
                """
                INSERT INTO model_configs (
                  name,
                  provider,
                  base_url,
                  model_name,
                  encrypted_api_key,
                  timeout_seconds,
                  temperature,
                  max_retries,
                  enabled,
                  is_default,
                  model_type,
                  voice
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                  provider = VALUES(provider),
                  base_url = VALUES(base_url),
                  model_name = VALUES(model_name),
                  encrypted_api_key = VALUES(encrypted_api_key),
                  timeout_seconds = VALUES(timeout_seconds),
                  temperature = VALUES(temperature),
                  max_retries = VALUES(max_retries),
                  enabled = VALUES(enabled),
                  is_default = VALUES(is_default),
                  model_type = VALUES(model_type),
                  voice = VALUES(voice)
                """,
                (
                    name,
                    provider,
                    base_url,
                    model_name,
                    encrypted_api_key,
                    timeout_seconds,
                    temperature,
                    max_retries,
                    1 if enabled else 0,
                    1 if is_default else 0,
                    model_type,
                    voice,
                ),
            )
            cursor.execute("SELECT id FROM model_configs WHERE name = %s", (name,))
            row = cursor.fetchone()
            return int(row["id"])


def delete_model_config(config_id: int) -> bool:
    """删除模型配置。被启用定时计划引用的配置不允许删除。"""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) AS count
                FROM schedule_configs
                WHERE enabled = 1
                  AND (
                    model_config_id = %s
                    OR tts_config_id = %s
                  )
                """,
                (config_id, config_id),
            )
            row = cursor.fetchone() or {}
            if int(row.get("count") or 0) > 0:
                raise ValueError("模型配置正在被启用的定时计划引用，不能删除")

            cursor.execute("DELETE FROM model_configs WHERE id = %s", (config_id,))
            return cursor.rowcount > 0


def get_default_model_config(model_type: Optional[str] = None) -> Optional[ModelConfig]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            if model_type:
                cursor.execute(
                    """
                    SELECT
                      id,
                      name,
                      provider,
                      base_url,
                      model_name,
                      encrypted_api_key,
                      timeout_seconds,
                      temperature,
                      max_retries,
                      enabled,
                      is_default,
                      model_type,
                      voice
                    FROM model_configs
                    WHERE enabled = 1 AND model_type = %s
                    ORDER BY is_default DESC, id ASC
                    LIMIT 1
                    """,
                    (model_type,),
                )
            else:
                cursor.execute(
                    """
                    SELECT
                      id,
                      name,
                      provider,
                      base_url,
                      model_name,
                      encrypted_api_key,
                      timeout_seconds,
                      temperature,
                      max_retries,
                      enabled,
                      is_default,
                      model_type,
                      voice
                    FROM model_configs
                    WHERE enabled = 1
                    ORDER BY is_default DESC, id ASC
                    LIMIT 1
                    """
                )
            row = cursor.fetchone()
            if not row:
                return None
            encrypted_api_key = row.pop("encrypted_api_key") or ""
            row["api_key"] = decrypt_secret(encrypted_api_key)
            row["temperature"] = float(row["temperature"])
            return ModelConfig(**row)


def get_model_config(model_config_id: int) -> Optional[ModelConfig]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                  id,
                  name,
                  provider,
                  base_url,
                  model_name,
                  encrypted_api_key,
                  timeout_seconds,
                  temperature,
                  max_retries,
                  enabled,
                  is_default,
                  model_type,
                  voice
                FROM model_configs
                WHERE id = %s
                """,
                (model_config_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            encrypted_api_key = row.pop("encrypted_api_key") or ""
            row["api_key"] = decrypt_secret(encrypted_api_key)
            row["temperature"] = float(row["temperature"])
            return ModelConfig(**row)


def list_tts_voices(
    enabled_only: bool = False,
    model_config_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    where = []
    params: List[Any] = []
    if enabled_only:
        where.append("v.enabled = 1")
    if model_config_id is not None:
        where.append("(v.model_config_id = %s OR v.model_config_id IS NULL)")
        params.append(model_config_id)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                  v.id,
                  v.name,
                  v.language_code,
                  v.voice,
                  v.model_config_id,
                  m.name AS model_config_name,
                  m.model_name AS model_name,
                  v.sample_text,
                  v.enabled,
                  v.is_default,
                  v.created_at,
                  v.updated_at
                FROM tts_voices v
                LEFT JOIN model_configs m
                  ON m.id = v.model_config_id
                {where_sql}
                ORDER BY v.is_default DESC, v.enabled DESC, v.language_code ASC, v.id ASC
                """,
                tuple(params),
            )
            return cursor.fetchall()


def get_tts_voice(voice_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                  v.id,
                  v.name,
                  v.language_code,
                  v.voice,
                  v.model_config_id,
                  m.name AS model_config_name,
                  m.model_name AS model_name,
                  v.sample_text,
                  v.enabled,
                  v.is_default,
                  v.created_at,
                  v.updated_at
                FROM tts_voices v
                LEFT JOIN model_configs m
                  ON m.id = v.model_config_id
                WHERE v.id = %s
                """,
                (voice_id,),
            )
            return cursor.fetchone()


def get_default_tts_voice(model_config_id: Optional[int] = None) -> Optional[TTSVoice]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            if model_config_id:
                cursor.execute(
                    """
                    SELECT id, name, language_code, voice, model_config_id, sample_text, enabled, is_default
                    FROM tts_voices
                    WHERE enabled = 1
                      AND model_config_id = %s
                    ORDER BY is_default DESC, id ASC
                    LIMIT 1
                    """,
                    (model_config_id,),
                )
                row = cursor.fetchone()
                if row:
                    return TTSVoice(**row)
            cursor.execute(
                """
                SELECT id, name, language_code, voice, model_config_id, sample_text, enabled, is_default
                FROM tts_voices
                WHERE enabled = 1
                  AND model_config_id IS NULL
                ORDER BY is_default DESC, id ASC
                LIMIT 1
                """
            )
            row = cursor.fetchone()
            return TTSVoice(**row) if row else None


def upsert_tts_voice(
    name: str,
    language_code: str,
    voice: str,
    model_config_id: Optional[int] = None,
    sample_text: str = "大家好，欢迎收看今天的新闻播报。",
    enabled: bool = True,
    is_default: bool = False,
    voice_id: Optional[int] = None,
) -> int:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            if model_config_id is not None:
                cursor.execute(
                    "SELECT id FROM model_configs WHERE id = %s AND model_type = 'tts'",
                    (model_config_id,),
                )
                if not cursor.fetchone():
                    raise ValueError("绑定的 TTS 模型配置不存在")
            if is_default:
                if model_config_id is None:
                    cursor.execute(
                        "UPDATE tts_voices SET is_default = 0 WHERE model_config_id IS NULL"
                    )
                else:
                    cursor.execute(
                        "UPDATE tts_voices SET is_default = 0 WHERE model_config_id = %s",
                        (model_config_id,),
                    )

            if voice_id:
                cursor.execute("SELECT id FROM tts_voices WHERE id = %s", (voice_id,))
                if not cursor.fetchone():
                    raise RuntimeError(f"语音不存在 (id={voice_id})")
                cursor.execute(
                    """
                    UPDATE tts_voices
                    SET name = %s,
                        language_code = %s,
                        voice = %s,
                        model_config_id = %s,
                        sample_text = %s,
                        enabled = %s,
                        is_default = %s
                    WHERE id = %s
                    """,
                    (
                        name,
                        language_code,
                        voice,
                        model_config_id,
                        sample_text,
                        1 if enabled else 0,
                        1 if is_default else 0,
                        voice_id,
                    ),
                )
                return int(voice_id)

            cursor.execute(
                """
                INSERT INTO tts_voices (
                  name, language_code, voice, model_config_id, sample_text, enabled, is_default
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                  language_code = VALUES(language_code),
                  voice = VALUES(voice),
                  model_config_id = VALUES(model_config_id),
                  sample_text = VALUES(sample_text),
                  enabled = VALUES(enabled),
                  is_default = VALUES(is_default)
                """,
                (
                    name,
                    language_code,
                    voice,
                    model_config_id,
                    sample_text,
                    1 if enabled else 0,
                    1 if is_default else 0,
                ),
            )
            cursor.execute("SELECT id FROM tts_voices WHERE name = %s", (name,))
            row = cursor.fetchone()
            return int(row["id"])


def delete_tts_voice(voice_id: int) -> bool:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM tts_voices WHERE id = %s", (voice_id,))
            return cursor.rowcount > 0


def list_prompt_versions() -> List[Dict[str, Any]]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                  id,
                  name,
                  system_prompt,
                  user_prompt_template,
                  enabled,
                  is_default,
                  created_at,
                  updated_at
                FROM prompt_versions
                ORDER BY is_default DESC, enabled DESC, id DESC
                """
            )
            return cursor.fetchall()


def upsert_prompt_version(
    name: str,
    system_prompt: str,
    user_prompt_template: str,
    enabled: bool = True,
    is_default: bool = False,
    prompt_id: Optional[int] = None,
) -> int:
    """新建或更新提示词版本。有 prompt_id 时按 id 更新，否则新建。"""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            if is_default:
                cursor.execute("UPDATE prompt_versions SET is_default = 0")
            if prompt_id:
                cursor.execute(
                    """
                    UPDATE prompt_versions
                    SET name = %s,
                        system_prompt = %s,
                        user_prompt_template = %s,
                        enabled = %s,
                        is_default = %s
                    WHERE id = %s
                    """,
                    (
                        name,
                        system_prompt,
                        user_prompt_template,
                        1 if enabled else 0,
                        1 if is_default else 0,
                        prompt_id,
                    ),
                )
                if cursor.rowcount == 0:
                    raise RuntimeError(f"提示词版本不存在 (id={prompt_id})")
                return prompt_id
            cursor.execute(
                """
                INSERT INTO prompt_versions (
                  name,
                  system_prompt,
                  user_prompt_template,
                  enabled,
                  is_default
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    name,
                    system_prompt,
                    user_prompt_template,
                    1 if enabled else 0,
                    1 if is_default else 0,
                ),
            )
            return int(cursor.lastrowid)


def get_prompt_version(prompt_id: int) -> Optional[PromptVersion]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                  id,
                  name,
                  system_prompt,
                  user_prompt_template,
                  enabled,
                  is_default
                FROM prompt_versions
                WHERE id = %s
                """,
                (prompt_id,),
            )
            row = cursor.fetchone()
            return PromptVersion(**row) if row else None


def get_default_prompt_version() -> Optional[PromptVersion]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                  id,
                  name,
                  system_prompt,
                  user_prompt_template,
                  enabled,
                  is_default
                FROM prompt_versions
                WHERE enabled = 1
                ORDER BY is_default DESC, id DESC
                LIMIT 1
                """
            )
            row = cursor.fetchone()
            return PromptVersion(**row) if row else None


def delete_prompt_version(prompt_id: int) -> bool:
    """永久删除指定提示词版本。返回是否成功删除（False 表示 ID 不存在）。"""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM prompt_versions WHERE id = %s",
                (prompt_id,),
            )
            return cursor.rowcount > 0


def create_pipeline_run(
    task_type: str,
    trigger_type: str,
    schedule_id: Optional[int] = None,
    total_count: int = 0,
    current_step: str = "任务已创建",
    task_payload: Optional[Dict[str, Any]] = None,
    rss_category: Optional[str] = None,
    model_config_id: Optional[int] = None,
    prompt_version_id: Optional[int] = None,
    tts_config_id: Optional[int] = None,
    report_type: Optional[str] = None,
) -> int:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO pipeline_runs (
                  task_type,
                  trigger_type,
                  schedule_id,
                  status,
                  progress_percent,
                  current_step,
                  total_count,
                  task_payload,
                  rss_category,
                  model_config_id,
                  prompt_version_id,
                  tts_config_id,
                  report_type
                )
                VALUES (%s, %s, %s, 'pending', 0, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    task_type,
                    trigger_type,
                    schedule_id,
                    current_step,
                    total_count,
                    json.dumps(task_payload or {}, ensure_ascii=False),
                    rss_category,
                    model_config_id,
                    prompt_version_id,
                    tts_config_id,
                    report_type,
                ),
            )
            return int(cursor.lastrowid)


def start_pipeline_run(run_id: int, current_step: str = "任务开始执行") -> None:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE pipeline_runs
                SET status = 'running',
                    started_at = COALESCE(started_at, UTC_TIMESTAMP()),
                    current_step = %s,
                    worker_id = %s,
                    heartbeat_at = UTC_TIMESTAMP()
                WHERE id = %s
                """,
                (current_step[:255], WORKER_ID, run_id),
            )


def update_pipeline_run(
    run_id: int,
    progress_percent: float,
    current_step: str,
    processed_count: Optional[int] = None,
    total_count: Optional[int] = None,
    report_id: Optional[int] = None,
) -> None:
    assignments = [
        "progress_percent = %s",
        "current_step = %s",
        "heartbeat_at = UTC_TIMESTAMP()",
    ]
    params: List[Any] = [max(0.0, min(float(progress_percent), 99.0)), current_step[:255]]
    if processed_count is not None:
        assignments.append("processed_count = %s")
        params.append(processed_count)
    if total_count is not None:
        assignments.append("total_count = %s")
        params.append(total_count)
    if report_id is not None:
        assignments.append("report_id = %s")
        params.append(report_id)
    params.append(run_id)

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE pipeline_runs
                SET {", ".join(assignments)}
                WHERE id = %s
                """,
                tuple(params),
            )


def finish_pipeline_run(
    run_id: int,
    status: str,
    current_step: str,
    progress_percent: float = 100,
    error_message: Optional[str] = None,
    report_id: Optional[int] = None,
) -> None:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE pipeline_runs
                SET status = %s,
                    progress_percent = %s,
                    current_step = %s,
                    error_message = %s,
                    report_id = COALESCE(%s, report_id),
                    worker_id = NULL,
                    heartbeat_at = UTC_TIMESTAMP(),
                    finished_at = UTC_TIMESTAMP()
                WHERE id = %s
                """,
                (
                    status,
                    max(0.0, min(float(progress_percent), 100.0)),
                    current_step[:255],
                    error_message[:6000] if error_message else None,
                    report_id,
                    run_id,
                ),
            )


def add_pipeline_event(
    run_id: int,
    stage: str,
    message: str,
    level: str = "info",
    progress_percent: Optional[float] = None,
) -> None:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO pipeline_run_events (
                  run_id,
                  level,
                  stage,
                  message,
                  progress_percent
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (run_id, level, stage[:100], message, progress_percent),
            )


def list_pipeline_runs(limit: int = 50) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM pipeline_runs
                ORDER BY id DESC
                LIMIT %s
                """,
                (limit,),
            )
            return cursor.fetchall()


def get_pipeline_run(run_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM pipeline_runs WHERE id = %s", (run_id,))
            return cursor.fetchone()


def list_pipeline_run_events(run_id: int, limit: int = 200) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM pipeline_run_events
                WHERE run_id = %s
                ORDER BY id ASC
                LIMIT %s
                """,
                (run_id, limit),
            )
            return cursor.fetchall()


def list_pipeline_run_events_after(
    run_id: int,
    last_event_id: int,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM pipeline_run_events
                WHERE run_id = %s
                  AND id > %s
                ORDER BY id ASC
                LIMIT %s
                """,
                (run_id, last_event_id, limit),
            )
            return cursor.fetchall()


def fail_stale_pipeline_runs() -> None:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE pipeline_runs
                SET status = 'failed',
                    current_step = '任务因服务重启而中断',
                    error_message = 'video-worker restarted before the pipeline completed',
                    worker_id = NULL,
                    heartbeat_at = UTC_TIMESTAMP(),
                    finished_at = UTC_TIMESTAMP()
                WHERE status IN ('pending', 'running')
                """
            )


def recover_unfinished_pipeline_runs() -> List[Dict[str, Any]]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM pipeline_runs
                WHERE status IN ('pending', 'running')
                ORDER BY id ASC
                """
            )
            rows = cursor.fetchall()
            if rows:
                cursor.execute(
                    """
                    UPDATE pipeline_runs
                    SET status = 'pending',
                        progress_percent = 0,
                        current_step = '服务重启后重新排队',
                        worker_id = NULL,
                        heartbeat_at = NULL,
                        error_message = NULL,
                        started_at = NULL,
                        finished_at = NULL
                    WHERE status IN ('pending', 'running')
                    """
                )
            return rows


def should_auto_publish(report_id: int) -> bool:
    """视频渲染成功后是否自动标记发布：读取该报告所属 pipeline_run 的 task_payload.auto_publish。"""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT task_payload
                FROM pipeline_runs
                WHERE report_id = %s
                ORDER BY id DESC
                LIMIT 1
                """,
                (report_id,),
            )
            row = cursor.fetchone()
    if not row:
        return False
    raw_payload = row.get("task_payload") or {}
    if isinstance(raw_payload, str):
        try:
            raw_payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            raw_payload = {}
    return bool(raw_payload.get("auto_publish") if isinstance(raw_payload, dict) else False)


def mark_report_news_published(report_id: int) -> int:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE rss_news_dedupe d
                JOIN rss_llm_tts_queue q
                  ON d.news_hash = SHA2(
                    LOWER(
                      TRIM(
                        COALESCE(NULLIF(q.link, ''), q.news_title)
                      )
                    ),
                    256
                  )
                JOIN rss_llm_report r
                  ON r.id = q.report_id
                SET d.published_at = CURRENT_TIMESTAMP
                WHERE q.report_id = %s
                  AND CONVERT(d.report_type USING utf8mb4) COLLATE utf8mb4_unicode_ci =
                      CONVERT(r.report_type USING utf8mb4) COLLATE utf8mb4_unicode_ci
                  AND d.published_at IS NULL
                """,
                (report_id,),
            )
            return int(cursor.rowcount)


def list_reports(
    limit: int = 50,
    report_type: Optional[str] = None,
    rss_category: Optional[str] = None,
    model_config_id: Optional[int] = None,
    prompt_version_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> List[Dict[str, Any]]:
    where_clauses = [
        "(%s IS NULL OR r.report_type = %s)",
        "(%s IS NULL OR r.rss_category = %s)",
        "(%s IS NULL OR r.model_config_id = %s)",
        "(%s IS NULL OR r.prompt_version_id = %s)",
    ]
    params: List[Any] = [
        report_type, report_type,
        rss_category, rss_category,
        model_config_id, model_config_id,
        prompt_version_id, prompt_version_id,
    ]
    if date_from:
        where_clauses.append("DATE(r.created_at) >= %s")
        params.append(date_from)
    if date_to:
        where_clauses.append("DATE(r.created_at) <= %s")
        params.append(date_to)
    where_sql = " WHERE " + " AND ".join(where_clauses)
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                  r.id,
                  r.raw_id,
                  r.title,
                  r.daily_trend,
                  r.news_count,
                  r.report_type,
                  r.rss_category,
                  r.model_config_id,
                  r.prompt_version_id,
                  r.pipeline_run_id,
                  COUNT(DISTINCT n.id) AS key_news_count,
                  COUNT(DISTINCT q.id) AS tts_item_count,
                  SUM(CASE WHEN d.published_at IS NOT NULL THEN 1 ELSE 0 END) AS published_count,
                  MIN(d.published_at) AS first_published_at,
                  MAX(d.published_at) AS last_published_at,
                  v.id AS video_job_id,
                  v.status AS video_status,
                  v.video_path,
                  v.duration_seconds,
                  v.progress_percent AS video_progress_percent,
                  v.current_step AS video_current_step,
                  v.updated_at AS video_updated_at
                FROM rss_llm_report r
                LEFT JOIN rss_llm_key_news n
                  ON n.report_id = r.id
                LEFT JOIN rss_llm_tts_queue q
                  ON q.news_id = n.id
                LEFT JOIN rss_news_dedupe d
                  ON d.news_hash = SHA2(
                    LOWER(
                      TRIM(
                        COALESCE(NULLIF(n.link, ''), n.title)
                      )
                    ),
                    256
                  )
                  AND CONVERT(d.report_type USING utf8mb4) COLLATE utf8mb4_unicode_ci =
                      CONVERT(r.report_type USING utf8mb4) COLLATE utf8mb4_unicode_ci
                LEFT JOIN rss_video_job v
                  ON v.report_id = r.id
                {where_sql}
                GROUP BY
                  r.id,
                  r.raw_id,
                  r.title,
                  r.daily_trend,
                  r.news_count,
                  r.report_type,
                  r.rss_category,
                  r.model_config_id,
                  r.prompt_version_id,
                  r.pipeline_run_id,
                  v.id,
                  v.status,
                  v.video_path,
                  v.duration_seconds,
                  v.progress_percent,
                  v.current_step,
                  v.updated_at
                ORDER BY r.id DESC
                LIMIT %s
                """,
                (*params, limit),
            )
            return cursor.fetchall()


def get_report(report_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                  r.id,
                  r.raw_id,
                  r.title,
                  r.daily_trend,
                  r.news_count,
                  r.report_type,
                  r.rss_category,
                  r.model_config_id,
                  r.prompt_version_id,
                  r.pipeline_run_id,
                  COUNT(DISTINCT n.id) AS key_news_count,
                  COUNT(DISTINCT q.id) AS tts_item_count,
                  SUM(CASE WHEN d.published_at IS NOT NULL THEN 1 ELSE 0 END) AS published_count,
                  MIN(d.published_at) AS first_published_at,
                  MAX(d.published_at) AS last_published_at,
                  v.id AS video_job_id,
                  v.status AS video_status,
                  v.video_path,
                  v.duration_seconds,
                  v.progress_percent AS video_progress_percent,
                  v.current_step AS video_current_step,
                  v.error_message AS video_error_message,
                  v.updated_at AS video_updated_at
                FROM rss_llm_report r
                LEFT JOIN rss_llm_key_news n
                  ON n.report_id = r.id
                LEFT JOIN rss_llm_tts_queue q
                  ON q.news_id = n.id
                LEFT JOIN rss_news_dedupe d
                  ON d.news_hash = SHA2(
                    LOWER(
                      TRIM(
                        COALESCE(NULLIF(n.link, ''), n.title)
                      )
                    ),
                    256
                  )
                  AND CONVERT(d.report_type USING utf8mb4) COLLATE utf8mb4_unicode_ci =
                      CONVERT(r.report_type USING utf8mb4) COLLATE utf8mb4_unicode_ci
                LEFT JOIN rss_video_job v
                  ON v.report_id = r.id
                WHERE r.id = %s
                GROUP BY
                  r.id,
                  r.raw_id,
                  r.title,
                  r.daily_trend,
                  r.news_count,
                  r.report_type,
                  r.rss_category,
                  r.model_config_id,
                  r.prompt_version_id,
                  r.pipeline_run_id,
                  v.id,
                  v.status,
                  v.video_path,
                  v.duration_seconds,
                  v.progress_percent,
                  v.current_step,
                  v.error_message,
                  v.updated_at
                """,
                (report_id,),
            )
            return cursor.fetchone()


def list_report_news(report_id: int) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                  n.id,
                  n.report_id,
                  n.item_index,
                  n.title,
                  n.pubdate,
                  n.summary,
                  n.related_field,
                  n.importance,
                  n.reserve_reason,
                  n.link,
                  n.voiceover_script,
                  q.id AS tts_queue_id,
                  q.tts_status,
                  q.tts_audio_path,
                  q.tts_error,
                  q.tts_text,
                  d.published_at,
                  CASE WHEN d.published_at IS NULL THEN 0 ELSE 1 END AS published
                FROM rss_llm_key_news n
                LEFT JOIN rss_llm_report r
                  ON r.id = n.report_id
                LEFT JOIN rss_llm_tts_queue q
                  ON q.news_id = n.id
                LEFT JOIN rss_news_dedupe d
                  ON d.news_hash = SHA2(
                    LOWER(
                      TRIM(
                        COALESCE(NULLIF(n.link, ''), n.title)
                      )
                    ),
                    256
                  )
                  AND CONVERT(d.report_type USING utf8mb4) COLLATE utf8mb4_unicode_ci =
                      CONVERT(r.report_type USING utf8mb4) COLLATE utf8mb4_unicode_ci
                WHERE n.report_id = %s
                ORDER BY n.item_index ASC, n.id ASC
                """,
                (report_id,),
            )
            return cursor.fetchall()


def update_report_news(
    report_id: int,
    news_id: int,
    item_index: int,
    title: str,
    pubdate: Optional[str],
    summary: str,
    related_field: Optional[str],
    importance: str,
    reserve_reason: Optional[str],
    link: Optional[str],
    voiceover_script: Optional[str],
) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id
                FROM rss_llm_key_news
                WHERE id = %s
                  AND report_id = %s
                """,
                (news_id, report_id),
            )
            if not cursor.fetchone():
                return None

            cursor.execute(
                """
                UPDATE rss_llm_key_news
                SET item_index = %s,
                    title = %s,
                    pubdate = %s,
                    summary = %s,
                    related_field = %s,
                    importance = %s,
                    reserve_reason = %s,
                    link = %s,
                    voiceover_script = %s
                WHERE id = %s
                  AND report_id = %s
                """,
                (
                    item_index,
                    title,
                    pubdate,
                    summary,
                    related_field,
                    importance,
                    reserve_reason,
                    link,
                    voiceover_script,
                    news_id,
                    report_id,
                ),
            )
            cursor.execute(
                """
                UPDATE rss_llm_tts_queue q
                JOIN rss_llm_report r
                  ON r.id = q.report_id
                SET q.item_index = %s,
                    q.news_title = %s,
                    q.pubdate = %s,
                    q.summary = %s,
                    q.related_field = %s,
                    q.importance = %s,
                    q.reserve_reason = %s,
                    q.link = %s,
                    q.voiceover_script = %s,
                    q.tts_text = CONCAT(
                        '【', r.title, '】', '\\n\\n',
                        '今日趋势：', COALESCE(r.daily_trend, ''), '\\n\\n',
                        '新闻：', COALESCE(%s, ''), '\\n',
                        '重要性：', COALESCE(%s, '中'), '\\n',
                        '相关领域：', COALESCE(%s, ''), '\\n\\n',
                        '新闻摘要：', COALESCE(%s, ''), '\\n\\n',
                        '保留理由：', COALESCE(%s, ''), '\\n\\n',
                        '口播稿：', COALESCE(%s, ''), '\\n\\n',
                        '原文链接：', COALESCE(%s, '')
                    ),
                    q.tts_audio_path = IF(q.tts_status = 'done', NULL, q.tts_audio_path),
                    q.tts_error = NULL,
                    q.tts_status = IF(q.tts_status = 'done', 'pending', q.tts_status)
                WHERE q.news_id = %s
                  AND q.report_id = %s
                """,
                (
                    item_index,
                    title,
                    pubdate,
                    summary,
                    related_field,
                    importance,
                    reserve_reason,
                    link,
                    voiceover_script,
                    title,
                    importance,
                    related_field,
                    summary,
                    reserve_reason,
                    voiceover_script,
                    link,
                    news_id,
                    report_id,
                ),
            )
            cursor.execute(
                """
                SELECT
                  n.id,
                  n.report_id,
                  n.item_index,
                  n.title,
                  n.pubdate,
                  n.summary,
                  n.related_field,
                  n.importance,
                  n.reserve_reason,
                  n.link,
                  n.voiceover_script,
                  q.id AS tts_queue_id,
                  q.tts_status,
                  q.tts_audio_path,
                  q.tts_error,
                  q.tts_text,
                  d.published_at,
                  CASE WHEN d.published_at IS NULL THEN 0 ELSE 1 END AS published
                FROM rss_llm_key_news n
                LEFT JOIN rss_llm_report r
                  ON r.id = n.report_id
                LEFT JOIN rss_llm_tts_queue q
                  ON q.news_id = n.id
                LEFT JOIN rss_news_dedupe d
                  ON d.news_hash = SHA2(
                    LOWER(
                      TRIM(
                        COALESCE(NULLIF(n.link, ''), n.title)
                      )
                    ),
                    256
                  )
                  AND CONVERT(d.report_type USING utf8mb4) COLLATE utf8mb4_unicode_ci =
                      CONVERT(r.report_type USING utf8mb4) COLLATE utf8mb4_unicode_ci
                WHERE n.id = %s
                  AND n.report_id = %s
                """,
                (news_id, report_id),
            )
            return cursor.fetchone()


def list_schedule_configs() -> List[Dict[str, Any]]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM schedule_configs
                ORDER BY id ASC
                """
            )
            return cursor.fetchall()


def get_schedule_config(schedule_id: int) -> Optional[ScheduleConfig]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                  id,
                  name,
                  task_type,
                  cron_expression,
                  timezone,
                  enabled,
                  prevent_overlap,
                  max_runtime_seconds,
                  retry_count,
                  retry_interval_seconds,
                  next_run_at,
                  last_run_at,
                  rss_category,
                  model_config_id,
                  prompt_version_id,
                  tts_config_id,
                  report_type,
                  auto_render,
                  auto_publish
                FROM schedule_configs
                WHERE id = %s
                """,
                (schedule_id,),
            )
            row = cursor.fetchone()
            return ScheduleConfig(**row) if row else None


def upsert_schedule_config(
    name: str,
    task_type: str,
    cron_expression: str,
    tz_name: str,
    enabled: bool = True,
    prevent_overlap: bool = True,
    max_runtime_seconds: int = 3600,
    retry_count: int = 2,
    retry_interval_seconds: int = 300,
    rss_category: Optional[str] = None,
    model_config_id: Optional[int] = None,
    prompt_version_id: Optional[int] = None,
    tts_config_id: Optional[int] = None,
    report_type: str = "general",
    auto_render: bool = True,
    auto_publish: bool = False,
    schedule_id: Optional[int] = None,
) -> int:
    next_run = next_run_after(cron_expression, tz_name) if enabled else None
    with get_connection() as conn:
        with conn.cursor() as cursor:
            if schedule_id is not None:
                cursor.execute("SELECT id FROM schedule_configs WHERE id = %s", (schedule_id,))
                if cursor.fetchone():
                    cursor.execute(
                        """
                        UPDATE schedule_configs
                        SET name = %s,
                            task_type = %s,
                            cron_expression = %s,
                            timezone = %s,
                            enabled = %s,
                            prevent_overlap = %s,
                            max_runtime_seconds = %s,
                            retry_count = %s,
                            retry_interval_seconds = %s,
                            rss_category = %s,
                            model_config_id = %s,
                            prompt_version_id = %s,
                            tts_config_id = %s,
                            report_type = %s,
                            auto_render = %s,
                            auto_publish = %s,
                            next_run_at = %s
                        WHERE id = %s
                        """,
                        (
                            name,
                            task_type,
                            cron_expression,
                            tz_name,
                            1 if enabled else 0,
                            1 if prevent_overlap else 0,
                            max_runtime_seconds,
                            retry_count,
                            retry_interval_seconds,
                            rss_category,
                            model_config_id,
                            prompt_version_id,
                            tts_config_id,
                            report_type,
                            1 if auto_render else 0,
                            1 if auto_publish else 0,
                            next_run,
                            schedule_id,
                        ),
                    )
                    return int(schedule_id)

            cursor.execute(
                """
                INSERT INTO schedule_configs (
                  name,
                  task_type,
                  cron_expression,
                  timezone,
                  enabled,
                  prevent_overlap,
                  max_runtime_seconds,
                  retry_count,
                  retry_interval_seconds,
                  rss_category,
                  model_config_id,
                  prompt_version_id,
                  tts_config_id,
                  report_type,
                  auto_render,
                  auto_publish,
                  next_run_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                  task_type = VALUES(task_type),
                  cron_expression = VALUES(cron_expression),
                  timezone = VALUES(timezone),
                  enabled = VALUES(enabled),
                  prevent_overlap = VALUES(prevent_overlap),
                  max_runtime_seconds = VALUES(max_runtime_seconds),
                  retry_count = VALUES(retry_count),
                  retry_interval_seconds = VALUES(retry_interval_seconds),
                  rss_category = VALUES(rss_category),
                  model_config_id = VALUES(model_config_id),
                  prompt_version_id = VALUES(prompt_version_id),
                  tts_config_id = VALUES(tts_config_id),
                  report_type = VALUES(report_type),
                  auto_render = VALUES(auto_render),
                  auto_publish = VALUES(auto_publish),
                  next_run_at = VALUES(next_run_at)
                """,
                (
                    name,
                    task_type,
                    cron_expression,
                    tz_name,
                    1 if enabled else 0,
                    1 if prevent_overlap else 0,
                    max_runtime_seconds,
                    retry_count,
                    retry_interval_seconds,
                    rss_category,
                    model_config_id,
                    prompt_version_id,
                    tts_config_id,
                    report_type,
                    1 if auto_render else 0,
                    1 if auto_publish else 0,
                    next_run,
                ),
            )
            cursor.execute("SELECT id FROM schedule_configs WHERE name = %s", (name,))
            row = cursor.fetchone()
            return int(row["id"])


def due_schedule_configs() -> List[ScheduleConfig]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                  id,
                  name,
                  task_type,
                  cron_expression,
                  timezone,
                  enabled,
                  prevent_overlap,
                  max_runtime_seconds,
                  retry_count,
                  retry_interval_seconds,
                  next_run_at,
                  last_run_at,
                  rss_category,
                  model_config_id,
                  prompt_version_id,
                  tts_config_id,
                  report_type,
                  auto_render,
                  auto_publish
                FROM schedule_configs
                WHERE enabled = 1
                  AND next_run_at IS NOT NULL
                  AND next_run_at <= UTC_TIMESTAMP()
                ORDER BY next_run_at ASC, id ASC
                """
            )
            return [ScheduleConfig(**row) for row in cursor.fetchall()]


def mark_schedule_started(schedule: ScheduleConfig) -> None:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE schedule_configs
                SET last_run_at = UTC_TIMESTAMP(),
                    next_run_at = %s
                WHERE id = %s
                """,
                (
                    next_run_after(schedule.cron_expression, schedule.timezone, utc_now()),
                    schedule.id,
                ),
            )


def disable_schedule(schedule_id: int) -> None:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE schedule_configs
                SET enabled = 0,
                    next_run_at = NULL
                WHERE id = %s
                """,
                (schedule_id,),
            )


def enable_schedule(schedule_id: int) -> bool:
    schedule = get_schedule_config(schedule_id)
    if schedule is None:
        return False

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE schedule_configs
                SET enabled = 1,
                    next_run_at = %s
                WHERE id = %s
                """,
                (
                    next_run_after(schedule.cron_expression, schedule.timezone, utc_now()),
                    schedule_id,
                ),
            )
            return cursor.rowcount > 0


def list_rss_sources(enabled_only: bool = False) -> List[Dict[str, Any]]:
    sql = """
    SELECT
      id,
      name,
      url,
      category,
      language,
      priority,
      enabled,
      request_timeout_seconds,
      last_success_at,
      last_error_at,
      consecutive_failures,
      last_error,
      created_at,
      updated_at
    FROM rss_sources
    """
    params: tuple[Any, ...] = ()
    if enabled_only:
        sql += " WHERE enabled = %s"
        params = (1,)
    sql += " ORDER BY enabled DESC, priority ASC, id ASC"

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()


def get_rss_source(source_id: int) -> Optional[RSSSource]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                  id,
                  name,
                  url,
                  category,
                  language,
                  priority,
                  enabled,
                  request_timeout_seconds
                FROM rss_sources
                WHERE id = %s
                """,
                (source_id,),
            )
            row = cursor.fetchone()
            return RSSSource(**row) if row else None


def get_enabled_rss_sources(category: Optional[str] = None) -> List[RSSSource]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            if category:
                cursor.execute(
                    """
                    SELECT
                      id,
                      name,
                      url,
                      category,
                      language,
                      priority,
                      enabled,
                      request_timeout_seconds
                    FROM rss_sources
                    WHERE enabled = 1 AND category = %s
                    ORDER BY priority ASC, id ASC
                    """,
                    (category,),
                )
            else:
                cursor.execute(
                    """
                    SELECT
                      id,
                      name,
                      url,
                      category,
                      language,
                      priority,
                      enabled,
                      request_timeout_seconds
                    FROM rss_sources
                    WHERE enabled = 1
                    ORDER BY priority ASC, id ASC
                    """
                )
            return [RSSSource(**row) for row in cursor.fetchall()]


def upsert_rss_source(
    name: str,
    url: str,
    category: Optional[str] = None,
    language: str = "zh-CN",
    priority: int = 100,
    enabled: bool = True,
    request_timeout_seconds: int = 20,
    source_id: Optional[int] = None,
) -> int:
    if source_id is not None:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE rss_sources
                    SET name = %s,
                        url = %s,
                        category = %s,
                        language = %s,
                        priority = %s,
                        enabled = %s,
                        request_timeout_seconds = %s
                    WHERE id = %s
                    """,
                    (
                        name,
                        url,
                        category,
                        language,
                        priority,
                        1 if enabled else 0,
                        request_timeout_seconds,
                        source_id,
                    ),
                )
                if cursor.rowcount == 0:
                    raise ValueError(f"RSS source not found: {source_id}")
                return source_id

    sql = """
    INSERT INTO rss_sources (
      name,
      url,
      category,
      language,
      priority,
      enabled,
      request_timeout_seconds
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
      name = VALUES(name),
      category = VALUES(category),
      language = VALUES(language),
      priority = VALUES(priority),
      enabled = VALUES(enabled),
      request_timeout_seconds = VALUES(request_timeout_seconds)
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                sql,
                (
                    name,
                    url,
                    category,
                    language,
                    priority,
                    1 if enabled else 0,
                    request_timeout_seconds,
                ),
            )
            cursor.execute("SELECT id FROM rss_sources WHERE url = %s", (url,))
            row = cursor.fetchone()
            return int(row["id"])


def disable_rss_source(source_id: int) -> bool:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE rss_sources
                SET enabled = 0
                WHERE id = %s
                """,
                (source_id,),
            )
            return cursor.rowcount > 0


def hard_delete_rss_source(source_id: int) -> bool:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM rss_items
                WHERE source_id = %s
                """,
                (source_id,),
            )
            cursor.execute(
                """
                DELETE FROM rss_sources
                WHERE id = %s
                """,
                (source_id,),
            )
            return cursor.rowcount > 0


def mark_rss_source_success(source_id: int) -> None:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE rss_sources
                SET last_success_at = CURRENT_TIMESTAMP,
                    consecutive_failures = 0,
                    last_error = NULL
                WHERE id = %s
                """,
                (source_id,),
            )


def mark_rss_source_error(source_id: int, error_message: str) -> None:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE rss_sources
                SET last_error_at = CURRENT_TIMESTAMP,
                    consecutive_failures = consecutive_failures + 1,
                    last_error = %s
                WHERE id = %s
                """,
                (error_message[:4000], source_id),
            )


def upsert_rss_item(
    source_id: int,
    title: str,
    link: Optional[str],
    normalized_link: Optional[str],
    pubdate: Optional[str],
    description: str,
    content_hash: str,
    raw_json: str,
) -> bool:
    sql = """
    INSERT INTO rss_items (
      source_id,
      title,
      link,
      normalized_link,
      pubdate,
      description,
      content_hash,
      raw_json
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
      last_seen_at = CURRENT_TIMESTAMP,
      source_id = VALUES(source_id),
      title = VALUES(title),
      link = VALUES(link),
      normalized_link = VALUES(normalized_link),
      pubdate = VALUES(pubdate),
      description = VALUES(description),
      raw_json = VALUES(raw_json)
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                sql,
                (
                    source_id,
                    title,
                    link,
                    normalized_link,
                    pubdate,
                    description,
                    content_hash,
                    raw_json,
                ),
            )
            return cursor.rowcount == 1


def load_tts_items(report_id: int) -> List[TTSItem]:
    sql = """
    SELECT
      q.id,
      q.report_id,
      q.news_id,
      q.item_index,
      q.report_title,
      q.daily_trend,
      q.news_count,
      q.news_title,
      q.pubdate,
      q.summary,
      q.related_field,
      q.importance,
      q.reserve_reason,
      q.link,
      q.voiceover_script,
      q.tts_text,
      q.tts_status,
      q.tts_audio_path,
      q.tts_error
    FROM rss_llm_tts_queue q
    WHERE q.report_id = %s
      AND q.importance IN ('高', '中')
    ORDER BY q.item_index ASC
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (report_id,))
            rows = cursor.fetchall()
            return [TTSItem(**row) for row in rows]


def get_tts_item(queue_id: int) -> Optional[TTSItem]:
    sql = """
    SELECT
      q.id,
      q.report_id,
      q.news_id,
      q.item_index,
      q.report_title,
      q.daily_trend,
      q.news_count,
      q.news_title,
      q.pubdate,
      q.summary,
      q.related_field,
      q.importance,
      q.reserve_reason,
      q.link,
      q.voiceover_script,
      q.tts_text,
      q.tts_status,
      q.tts_audio_path,
      q.tts_error
    FROM rss_llm_tts_queue q
    WHERE q.id = %s
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (queue_id,))
            row = cursor.fetchone()
            return TTSItem(**row) if row else None


def list_tts_audio_assets(
    limit: int = 50,
    status: Optional[str] = None,
    report_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> List[Dict[str, Any]]:
    where: List[str] = []
    params: List[Any] = []
    if status:
        where.append("q.tts_status = %s")
        params.append(status)
    if report_id is not None:
        where.append("q.report_id = %s")
        params.append(report_id)
    if date_from:
        where.append("DATE(q.updated_at) >= %s")
        params.append(date_from)
    if date_to:
        where.append("DATE(q.updated_at) <= %s")
        params.append(date_to)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(limit)
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                  q.id,
                  q.report_id,
                  q.news_id,
                  q.item_index,
                  q.report_title,
                  q.news_title,
                  q.pubdate,
                  q.summary,
                  q.related_field,
                  q.importance,
                  q.voiceover_script,
                  q.tts_text,
                  q.tts_status,
                  q.tts_audio_path,
                  q.tts_audio_url,
                  q.tts_error,
                  q.created_at,
                  q.updated_at,
                  r.report_type,
                  r.rss_category,
                  p.tts_config_id
                FROM rss_llm_tts_queue q
                LEFT JOIN rss_llm_report r
                  ON r.id = q.report_id
                LEFT JOIN pipeline_runs p
                  ON p.id = r.pipeline_run_id
                {where_sql}
                ORDER BY q.updated_at DESC, q.id DESC
                LIMIT %s
                """,
                tuple(params),
            )
            return cursor.fetchall()


def upsert_video_job(report_id: int, title: str, status: str, item_count: int) -> int:
    sql = """
    INSERT INTO rss_video_job (
      report_id,
      video_title,
      status,
      item_count,
      progress_percent,
      current_step,
      worker_id,
      heartbeat_at
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, IF(%s = 'rendering', UTC_TIMESTAMP(), NULL))
    ON DUPLICATE KEY UPDATE
      video_title = VALUES(video_title),
      status = VALUES(status),
      item_count = VALUES(item_count),
      progress_percent = VALUES(progress_percent),
      current_step = VALUES(current_step),
      worker_id = VALUES(worker_id),
      heartbeat_at = VALUES(heartbeat_at),
      error_message = NULL
    """
    current_step = "任务已排队" if status == "pending" else "任务已创建"
    worker_id = WORKER_ID if status == "rendering" else None
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                sql,
                (
                    report_id,
                    title,
                    status,
                    item_count,
                    0,
                    current_step,
                    worker_id,
                    status,
                ),
            )
            cursor.execute("SELECT id FROM rss_video_job WHERE report_id = %s", (report_id,))
            row = cursor.fetchone()
            return int(row["id"])


def set_video_job_done(report_id: int, video_path: str, duration_seconds: float) -> None:
    sql = """
    UPDATE rss_video_job
    SET status = 'done',
        video_path = %s,
        duration_seconds = %s,
        progress_percent = 100,
        current_step = '视频生成完成',
        worker_id = NULL,
        heartbeat_at = UTC_TIMESTAMP(),
        error_message = NULL
    WHERE report_id = %s
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (video_path, duration_seconds, report_id))


def set_video_job_cover_path(report_id: int, cover_path: str) -> None:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE rss_video_job
                SET cover_path = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE report_id = %s
                """,
                (cover_path, report_id),
            )


def clear_video_job_assets(report_id: int) -> None:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE rss_video_job
                SET status = 'pending',
                    video_path = NULL,
                    cover_path = NULL,
                    duration_seconds = NULL,
                    progress_percent = 0,
                    current_step = '成品文件已删除',
                    worker_id = NULL,
                    heartbeat_at = NULL,
                    error_message = NULL
                WHERE report_id = %s
                """,
                (report_id,),
            )


def set_video_job_failed(report_id: int, error_message: str) -> None:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE rss_video_job
                SET status = 'failed',
                    current_step = '任务失败',
                    worker_id = NULL,
                    heartbeat_at = UTC_TIMESTAMP(),
                    error_message = %s
                WHERE report_id = %s
                """,
                (error_message[:6000], report_id),
            )


def set_video_job_cancelled(report_id: int) -> None:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE rss_video_job
                SET status = 'cancelled',
                    current_step = '任务已取消',
                    worker_id = NULL,
                    heartbeat_at = UTC_TIMESTAMP(),
                    error_message = '任务由用户取消'
                WHERE report_id = %s
                """,
                (report_id,),
            )


def fail_stale_rendering_jobs() -> None:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE rss_video_job
                SET status = 'failed',
                    current_step = '任务因服务重启而中断',
                    worker_id = NULL,
                    heartbeat_at = UTC_TIMESTAMP(),
                    error_message = 'video-worker restarted before the render completed'
                WHERE status IN ('pending', 'rendering')
                """
            )


def recover_unfinished_video_jobs() -> List[Dict[str, Any]]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM rss_video_job
                WHERE status IN ('pending', 'rendering')
                ORDER BY id ASC
                """
            )
            rows = cursor.fetchall()
            if rows:
                cursor.execute(
                    """
                    UPDATE rss_video_job
                    SET status = 'pending',
                        progress_percent = 0,
                        current_step = '服务重启后重新排队',
                        worker_id = NULL,
                        heartbeat_at = NULL,
                        error_message = NULL
                    WHERE status IN ('pending', 'rendering')
                    """
                )
            return rows


def set_video_job_progress(report_id: int, progress_percent: float, current_step: str) -> None:
    progress = max(0.0, min(float(progress_percent), 99.0))
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE rss_video_job
                SET progress_percent = %s,
                    current_step = %s,
                    worker_id = %s,
                    heartbeat_at = UTC_TIMESTAMP()
                WHERE report_id = %s
                """,
                (progress, current_step[:255], WORKER_ID, report_id),
            )


def set_tts_item_done(queue_id: int, audio_path: str) -> None:
    """Best-effort update. Older schemas may not have tts_audio_path yet."""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            try:
                cursor.execute(
                    """
                    UPDATE rss_llm_tts_queue
                    SET tts_status = 'done',
                        tts_audio_path = %s,
                        tts_error = NULL
                    WHERE id = %s
                    """,
                    (audio_path, queue_id),
                )
            except pymysql.err.OperationalError:
                cursor.execute(
                    """
                    UPDATE rss_llm_tts_queue
                    SET tts_status = 'done',
                        tts_error = NULL
                    WHERE id = %s
                    """,
                    (queue_id,),
                )


def set_tts_item_pending(queue_id: int) -> None:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE rss_llm_tts_queue
                SET tts_status = 'pending',
                    tts_audio_path = NULL,
                    tts_error = NULL
                WHERE id = %s
                """,
                (queue_id,),
            )


def set_tts_item_failed(queue_id: int, error_message: str) -> None:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE rss_llm_tts_queue
                SET tts_status = 'failed',
                    tts_error = %s
                WHERE id = %s
                """,
                (error_message[:6000], queue_id),
            )


def get_video_job(job_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM rss_video_job WHERE id = %s", (job_id,))
            return cursor.fetchone()


def get_video_job_by_report_id(report_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM rss_video_job WHERE report_id = %s", (report_id,))
            return cursor.fetchone()


def list_video_jobs(
    limit: int = 50,
    status: Optional[str] = None,
    report_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> List[Dict[str, Any]]:
    where = []
    params: List[Any] = []
    if status:
        where.append("v.status = %s")
        params.append(status)
    if report_id is not None:
        where.append("v.report_id = %s")
        params.append(report_id)
    if date_from:
        where.append("DATE(v.updated_at) >= %s")
        params.append(date_from)
    if date_to:
        where.append("DATE(v.updated_at) <= %s")
        params.append(date_to)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(limit)
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                  v.*,
                  r.title AS report_title,
                  r.daily_trend AS report_daily_trend,
                  r.news_count AS report_news_count
                FROM rss_video_job v
                LEFT JOIN rss_llm_report r
                  ON r.id = v.report_id
                {where_sql}
                ORDER BY v.id DESC
                LIMIT %s
                """,
                tuple(params),
            )
            return cursor.fetchall()


def list_stale_video_jobs(stale_seconds: int) -> List[Dict[str, Any]]:
    """心跳超时仍处于 rendering 的视频任务（worker 可能已僵尸）。"""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, report_id, worker_id
                FROM rss_video_job
                WHERE status = 'rendering'
                  AND worker_id IS NOT NULL
                  AND heartbeat_at IS NOT NULL
                  AND heartbeat_at < UTC_TIMESTAMP() - INTERVAL %s SECOND
                """,
                (stale_seconds,),
            )
            return cursor.fetchall()


def list_stale_pipeline_runs(stale_seconds: int) -> List[Dict[str, Any]]:
    """心跳超时仍处于 running 的 pipeline 任务。"""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, task_type, worker_id
                FROM pipeline_runs
                WHERE status = 'running'
                  AND worker_id IS NOT NULL
                  AND heartbeat_at IS NOT NULL
                  AND heartbeat_at < UTC_TIMESTAMP() - INTERVAL %s SECOND
                """,
                (stale_seconds,),
            )
            return cursor.fetchall()


def delete_old_audit_logs(retention_days: int) -> int:
    if retention_days <= 0:
        return 0
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM auth_audit_logs WHERE created_at < UTC_TIMESTAMP() - INTERVAL %s DAY",
                (retention_days,),
            )
            return int(cursor.rowcount)


def delete_old_llm_call_logs(retention_days: int) -> int:
    if retention_days <= 0:
        return 0
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM llm_call_logs WHERE created_at < UTC_TIMESTAMP() - INTERVAL %s DAY",
                (retention_days,),
            )
            return int(cursor.rowcount)


def load_recent_rss_items(
    hours: int = 24,
    limit: int = 120,
    category: Optional[str] = None,
) -> List[Dict[str, Any]]:
    sql = """
    SELECT
      i.id,
      i.source_id,
      s.name AS source_name,
      s.category AS source_category,
      i.title,
      i.link,
      i.normalized_link,
      i.pubdate,
      i.description,
      i.content_hash,
      i.first_seen_at,
      i.last_seen_at
    FROM rss_items i
    JOIN rss_sources s
      ON s.id = i.source_id
    WHERE COALESCE(i.pubdate, i.first_seen_at) >= UTC_TIMESTAMP() - INTERVAL %s HOUR
      AND (%s IS NULL OR s.category = %s)
    ORDER BY COALESCE(i.pubdate, i.first_seen_at) DESC, i.id DESC
    LIMIT %s
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (hours, category, category, limit))
            return cursor.fetchall()


def insert_llm_raw(raw_json: Dict[str, Any], pipeline_run_id: Optional[int] = None) -> int:
    payload = json.dumps(raw_json, ensure_ascii=False)
    with get_connection() as conn:
        with conn.cursor() as cursor:
            if pipeline_run_id is None:
                cursor.execute("INSERT INTO rss_llm_raw (raw_json) VALUES (%s)", (payload,))
            else:
                cursor.execute(
                    """
                    INSERT INTO rss_llm_raw (raw_json, pipeline_run_id)
                    VALUES (%s, %s)
                    ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)
                    """,
                    (payload, pipeline_run_id),
                )
            return int(cursor.lastrowid)


def recover_pipeline_run_report(run_id: int) -> Optional[Dict[str, Any]]:
    """Return the report already produced by a pipeline run, normalizing raw JSON if needed."""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                  raw.id AS raw_id,
                  report.id AS report_id,
                  report.news_count AS selected_count
                FROM rss_llm_raw raw
                LEFT JOIN rss_llm_report report
                  ON report.raw_id = raw.id
                WHERE raw.pipeline_run_id = %s
                ORDER BY raw.id DESC
                LIMIT 1
                """,
                (run_id,),
            )
            row = cursor.fetchone()
    if not row:
        return None

    raw_id = int(row["raw_id"])
    report_id = row.get("report_id")
    if report_id is None:
        # 从 pipeline_runs 读取原始配置，避免恢复时丢失 report_type 等上下文
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT report_type, rss_category, model_config_id, prompt_version_id
                    FROM pipeline_runs
                    WHERE id = %s
                    """,
                    (run_id,),
                )
                run_row = cursor.fetchone() or {}
        report_id = normalize_raw_report(
            raw_id,
            pipeline_run_id=run_id,
            report_type=run_row.get("report_type") or "general",
            rss_category=run_row.get("rss_category"),
            model_config_id=run_row.get("model_config_id"),
            prompt_version_id=run_row.get("prompt_version_id"),
        )
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT news_count FROM rss_llm_report WHERE id = %s",
                    (report_id,),
                )
                report_row = cursor.fetchone() or {}
        selected_count = int(report_row.get("news_count") or 0)
    else:
        report_id = int(report_id)
        selected_count = int(row.get("selected_count") or 0)

    return {
        "raw_id": raw_id,
        "report_id": report_id,
        "selected_count": selected_count,
    }


def normalize_raw_report(
    raw_id: int,
    pipeline_run_id: Optional[int] = None,
    report_type: str = "general",
    rss_category: Optional[str] = None,
    model_config_id: Optional[int] = None,
    prompt_version_id: Optional[int] = None,
) -> int:
    conn = get_connection(autocommit=False)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO rss_llm_report (
                    raw_id,
                    title,
                    daily_trend,
                    news_count,
                    report_type,
                    rss_category,
                    model_config_id,
                    prompt_version_id,
                    pipeline_run_id
                )
                SELECT
                    r.id AS raw_id,
                    JSON_UNQUOTE(JSON_EXTRACT(r.raw_json, '$.title')) AS title,
                    JSON_UNQUOTE(JSON_EXTRACT(r.raw_json, '$.daily_trend')) AS daily_trend,
                    COALESCE(JSON_LENGTH(JSON_EXTRACT(r.raw_json, '$.key_news')), 0) AS news_count,
                    %s AS report_type,
                    %s AS rss_category,
                    %s AS model_config_id,
                    %s AS prompt_version_id,
                    %s AS pipeline_run_id
                FROM rss_llm_raw r
                WHERE r.id = %s
                  AND JSON_VALID(r.raw_json)
                ON DUPLICATE KEY UPDATE
                    title = VALUES(title),
                    daily_trend = VALUES(daily_trend),
                    news_count = VALUES(news_count),
                    report_type = VALUES(report_type),
                    rss_category = VALUES(rss_category),
                    model_config_id = VALUES(model_config_id),
                    prompt_version_id = VALUES(prompt_version_id),
                    pipeline_run_id = COALESCE(VALUES(pipeline_run_id), rss_llm_report.pipeline_run_id)
                """,
                (
                    report_type,
                    rss_category,
                    model_config_id,
                    prompt_version_id,
                    pipeline_run_id,
                    raw_id,
                ),
            )
            cursor.execute("SELECT id FROM rss_llm_report WHERE raw_id = %s", (raw_id,))
            row = cursor.fetchone()
            if not row:
                raise RuntimeError(f"Failed to normalize rss_llm_report for raw_id={raw_id}")
            report_id = int(row["id"])

            cursor.execute(
                """
                INSERT INTO rss_llm_key_news (
                    report_id,
                    item_index,
                    title,
                    pubdate,
                    summary,
                    related_field,
                    importance,
                    reserve_reason,
                    link,
                    voiceover_script
                )
                SELECT
                    report.id AS report_id,
                    jt.item_index,
                    COALESCE(jt.title, '') AS title,
                    jt.pubdate,
                    jt.summary,
                    jt.related_field,
                    CASE
                        WHEN jt.importance IN ('高', '中', '低') THEN jt.importance
                        ELSE '中'
                    END AS importance,
                    jt.reserve_reason,
                    jt.link,
                    jt.voiceoverscript AS voiceover_script
                FROM rss_llm_raw raw
                JOIN rss_llm_report report
                    ON report.raw_id = raw.id
                CROSS JOIN JSON_TABLE(
                    raw.raw_json,
                    '$.key_news[*]'
                    COLUMNS (
                        item_index FOR ORDINALITY,
                        title VARCHAR(500) PATH '$.title' NULL ON EMPTY,
                        pubdate VARCHAR(100) PATH '$.pubdate' NULL ON EMPTY,
                        summary TEXT PATH '$.summary' NULL ON EMPTY,
                        related_field VARCHAR(255) PATH '$.related_field' NULL ON EMPTY,
                        importance VARCHAR(10) PATH '$.importance' NULL ON EMPTY,
                        reserve_reason TEXT PATH '$.reserve_reason' NULL ON EMPTY,
                        link VARCHAR(2048) PATH '$.link' NULL ON EMPTY,
                        voiceoverscript LONGTEXT PATH '$.voiceoverscript' NULL ON EMPTY
                    )
                ) AS jt
                WHERE raw.id = %s
                  AND JSON_VALID(raw.raw_json)
                ON DUPLICATE KEY UPDATE
                    title = VALUES(title),
                    pubdate = VALUES(pubdate),
                    summary = VALUES(summary),
                    related_field = VALUES(related_field),
                    importance = VALUES(importance),
                    reserve_reason = VALUES(reserve_reason),
                    link = VALUES(link),
                    voiceover_script = VALUES(voiceover_script)
                """,
                (raw_id,),
            )

            cursor.execute(
                """
                INSERT INTO rss_news_dedupe (
                    news_hash,
                    first_report_id,
                    first_news_id,
                    title,
                    link,
                    report_type
                )
                SELECT
                    SHA2(
                        LOWER(
                            TRIM(
                                COALESCE(NULLIF(n.link, ''), n.title)
                            )
                        ),
                        256
                    ) AS news_hash,
                    n.report_id,
                    n.id,
                    n.title,
                    n.link,
                    r.report_type AS report_type
                FROM rss_llm_key_news n
                JOIN rss_llm_report r
                    ON r.id = n.report_id
                WHERE n.report_id = %s
                  AND n.title IS NOT NULL
                  AND n.title <> ''
                ON DUPLICATE KEY UPDATE
                    last_seen_at = CURRENT_TIMESTAMP
                """,
                (report_id,),
            )

            cursor.execute(
                """
                INSERT INTO rss_llm_tts_queue (
                    report_id,
                    news_id,
                    item_index,
                    report_title,
                    daily_trend,
                    news_count,
                    news_title,
                    pubdate,
                    summary,
                    related_field,
                    importance,
                    reserve_reason,
                    link,
                    voiceover_script,
                    tts_text,
                    tts_status,
                    report_type
                )
                SELECT
                    r.id AS report_id,
                    n.id AS news_id,
                    n.item_index,
                    r.title AS report_title,
                    r.daily_trend,
                    r.news_count,
                    n.title AS news_title,
                    n.pubdate,
                    n.summary,
                    n.related_field,
                    n.importance,
                    n.reserve_reason,
                    n.link,
                    n.voiceover_script,
                    CONCAT(
                        '【', r.title, '】', '\\n\\n',
                        '今日趋势：', COALESCE(r.daily_trend, ''), '\\n\\n',
                        '新闻：', COALESCE(n.title, ''), '\\n',
                        '重要性：', COALESCE(n.importance, '中'), '\\n',
                        '相关领域：', COALESCE(n.related_field, ''), '\\n\\n',
                        '新闻摘要：', COALESCE(n.summary, ''), '\\n\\n',
                        '保留理由：', COALESCE(n.reserve_reason, ''), '\\n\\n',
                        '口播稿：', COALESCE(n.voiceover_script, ''), '\\n\\n',
                        '原文链接：', COALESCE(n.link, '')
                    ) AS tts_text,
                    'pending' AS tts_status,
                    r.report_type AS report_type
                FROM rss_llm_report r
                JOIN rss_llm_key_news n
                    ON n.report_id = r.id
                JOIN rss_news_dedupe d
                    ON d.news_hash = SHA2(LOWER(TRIM(COALESCE(NULLIF(n.link, ''), n.title))), 256)
                   AND CONVERT(d.report_type USING utf8mb4) COLLATE utf8mb4_unicode_ci =
                       CONVERT(r.report_type USING utf8mb4) COLLATE utf8mb4_unicode_ci
                WHERE r.id = %s
                  AND d.published_at IS NULL
                ON DUPLICATE KEY UPDATE
                    item_index = VALUES(item_index),
                    report_title = VALUES(report_title),
                    daily_trend = VALUES(daily_trend),
                    news_count = VALUES(news_count),
                    news_title = VALUES(news_title),
                    pubdate = VALUES(pubdate),
                    summary = VALUES(summary),
                    related_field = VALUES(related_field),
                    importance = VALUES(importance),
                    reserve_reason = VALUES(reserve_reason),
                    link = VALUES(link),
                    voiceover_script = VALUES(voiceover_script),
                    tts_text = VALUES(tts_text),
                    tts_status = IF(tts_status = 'done', tts_status, VALUES(tts_status)),
                    report_type = VALUES(report_type)
                """,
                (report_id,),
            )

            conn.commit()
            return report_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
