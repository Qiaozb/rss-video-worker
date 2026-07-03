"""custom rss analysis: per-schedule config + report_type isolation

Revision ID: 20260629_0007
Revises: 20260629_0006
Create Date: 2026-06-29
"""

from alembic import op


revision = "20260629_0007"
down_revision = "20260629_0006"
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


def _add_column(table_name: str, ddl: str) -> None:
    if not _column_exists(table_name, ddl.split()[0]):
        op.execute(f"ALTER TABLE {table_name} ADD COLUMN {ddl}")


def upgrade() -> None:
    # 阶段 1.1：schedule_configs 扩展为「分析任务配置」
    _add_column("schedule_configs", "rss_category VARCHAR(100) DEFAULT NULL COMMENT 'RSS 分类过滤，NULL 表示全部'")
    _add_column("schedule_configs", "model_config_id BIGINT DEFAULT NULL COMMENT '使用的模型配置'")
    _add_column("schedule_configs", "prompt_version_id BIGINT DEFAULT NULL COMMENT '使用的提示词版本'")
    _add_column("schedule_configs", "report_type VARCHAR(50) NOT NULL DEFAULT 'general' COMMENT '报告类型'")
    _add_column("schedule_configs", "auto_render TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否自动生成视频'")
    _add_column("schedule_configs", "auto_publish TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否自动标记已发布'")

    # task_type 唯一键不再适用（同一 task_type 允许多个分析计划并存），改为按 name 唯一。
    if _index_exists("schedule_configs", "uk_schedule_task_type"):
        op.execute("ALTER TABLE schedule_configs DROP INDEX uk_schedule_task_type")
    if not _index_exists("schedule_configs", "uk_schedule_name"):
        op.execute("ALTER TABLE schedule_configs ADD UNIQUE KEY uk_schedule_name (name)")

    # 阶段 1.2：pipeline_runs 记录每次运行实际使用的配置
    _add_column("pipeline_runs", "rss_category VARCHAR(100) DEFAULT NULL")
    _add_column("pipeline_runs", "model_config_id BIGINT DEFAULT NULL")
    _add_column("pipeline_runs", "prompt_version_id BIGINT DEFAULT NULL")
    _add_column("pipeline_runs", "report_type VARCHAR(50) DEFAULT NULL")

    # 阶段 1.3：rss_llm_report 关联任务类型与生成上下文
    _add_column("rss_llm_report", "report_type VARCHAR(50) NOT NULL DEFAULT 'general'")
    _add_column("rss_llm_report", "rss_category VARCHAR(100) DEFAULT NULL")
    _add_column("rss_llm_report", "model_config_id BIGINT DEFAULT NULL")
    _add_column("rss_llm_report", "prompt_version_id BIGINT DEFAULT NULL")
    _add_column("rss_llm_report", "pipeline_run_id BIGINT DEFAULT NULL")

    # 阶段 5.2：tts 队列区分报告类型
    _add_column("rss_llm_tts_queue", "report_type VARCHAR(50) DEFAULT NULL")

    # 阶段 6：去重按 report_type 隔离，避免不同报告类型互相污染
    _add_column("rss_news_dedupe", "report_type VARCHAR(50) NOT NULL DEFAULT 'general'")
    # 用首条报告的类型回填历史行，尽量贴合真实归属
    op.execute(
        """
        UPDATE rss_news_dedupe d
        JOIN rss_llm_report r ON r.id = d.first_report_id
        SET d.report_type = COALESCE(NULLIF(r.report_type, ''), 'general')
        WHERE d.report_type = 'general'
          AND r.report_type IS NOT NULL
          AND r.report_type <> ''
        """
    )
    # news_hash 原本是 PRIMARY KEY（Key_name='PRIMARY'），不是名为 uk_news_hash 的唯一索引。
    # 必须删除旧主键后重建为 (report_type, news_hash) 复合主键，否则同一条新闻在不同
    # report_type 下仍会因主键冲突而无法插入。
    connection = op.get_bind()
    pk_result = connection.exec_driver_sql(
        "SHOW INDEX FROM rss_news_dedupe WHERE Key_name = 'PRIMARY'"
    )
    if pk_result.fetchone() is not None:
        op.execute("ALTER TABLE rss_news_dedupe DROP PRIMARY KEY")
    # 兼容早期版本可能已创建过 uk_news_hash 唯一索引的情况
    if _index_exists("rss_news_dedupe", "uk_news_hash"):
        op.execute("ALTER TABLE rss_news_dedupe DROP INDEX uk_news_hash")
    if not _index_exists("rss_news_dedupe", "PRIMARY"):
        op.execute(
            "ALTER TABLE rss_news_dedupe ADD PRIMARY KEY (report_type, news_hash)"
        )


def downgrade() -> None:
    connection = op.get_bind()
    pk_result = connection.exec_driver_sql(
        "SHOW INDEX FROM rss_news_dedupe WHERE Key_name = 'PRIMARY'"
    )
    if pk_result.fetchone() is not None:
        op.execute("ALTER TABLE rss_news_dedupe DROP PRIMARY KEY")
    if not _index_exists("rss_news_dedupe", "PRIMARY"):
        op.execute("ALTER TABLE rss_news_dedupe ADD PRIMARY KEY (news_hash)")
    if _column_exists("rss_news_dedupe", "report_type"):
        op.execute("ALTER TABLE rss_news_dedupe DROP COLUMN report_type")

    if _column_exists("rss_llm_tts_queue", "report_type"):
        op.execute("ALTER TABLE rss_llm_tts_queue DROP COLUMN report_type")

    for col in ("pipeline_run_id", "prompt_version_id", "model_config_id", "rss_category", "report_type"):
        if _column_exists("rss_llm_report", col):
            op.execute(f"ALTER TABLE rss_llm_report DROP COLUMN {col}")

    for col in ("report_type", "prompt_version_id", "model_config_id", "rss_category"):
        if _column_exists("pipeline_runs", col):
            op.execute(f"ALTER TABLE pipeline_runs DROP COLUMN {col}")

    if _index_exists("schedule_configs", "uk_schedule_name"):
        op.execute("ALTER TABLE schedule_configs DROP INDEX uk_schedule_name")
    if not _index_exists("schedule_configs", "uk_schedule_task_type"):
        op.execute("ALTER TABLE schedule_configs ADD UNIQUE KEY uk_schedule_task_type (task_type)")
    for col in ("auto_publish", "auto_render", "report_type", "prompt_version_id", "model_config_id", "rss_category"):
        if _column_exists("schedule_configs", col):
            op.execute(f"ALTER TABLE schedule_configs DROP COLUMN {col}")
