from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.db import (
    insert_llm_raw,
    load_recent_rss_items,
    normalize_raw_report,
    recover_pipeline_run_report,
)
from app.llm import analyze_rss_markdown


@dataclass
class AnalyzeResult:
    raw_id: int
    report_id: int
    candidate_count: int
    selected_count: int
    report_json: Dict[str, Any]


_CATEGORY_LABELS: Dict[str, str] = {
    "game": "游戏",
    "tech": "科技",
    "finance": "财经",
    "general": "综合",
}


def _report_title_prefix(report_type: str, rss_category: Optional[str]) -> str:
    """根据 report_type / rss_category 生成报告标题前缀。"""
    if rss_category and rss_category in _CATEGORY_LABELS:
        return f"{_CATEGORY_LABELS[rss_category]}行业"
    type_labels = {
        "game_daily": "游戏行业",
        "tech_daily": "科技行业",
        "finance_daily": "财经行业",
    }
    return type_labels.get(report_type, "行业")


def report_title_fallback(report_type: str, rss_category: Optional[str]) -> str:
    """生成报告标题的回退值。"""
    return f"{_report_title_prefix(report_type, rss_category)}新闻精选"


def rss_items_to_markdown(
    items: List[Dict[str, Any]],
    report_type: str = "general",
    rss_category: Optional[str] = None,
) -> str:
    prefix = _report_title_prefix(report_type, rss_category)
    parts = [f"# RSS {prefix}新闻候选列表\n"]
    parts.append(f"候选新闻数量：{len(items)}\n")

    for index, item in enumerate(items, start=1):
        title = item.get("title") or ""
        link = item.get("link") or item.get("normalized_link") or ""
        pubdate = item.get("pubdate") or item.get("first_seen_at") or ""
        source_name = item.get("source_name") or ""
        description = item.get("description") or ""

        parts.append(
            f"""## {index}. [{title}]({link})

**来源：** {source_name}

**发布时间：** {pubdate}

{description}

---
"""
        )

    return "\n".join(parts)


def analyze_recent_rss_items(
    hours: int = 24,
    limit: int = 120,
    pipeline_run_id: int | None = None,
    rss_category: Optional[str] = None,
    model_config_id: Optional[int] = None,
    prompt_version_id: Optional[int] = None,
    report_type: str = "general",
) -> AnalyzeResult:
    if pipeline_run_id is not None:
        existing = recover_pipeline_run_report(pipeline_run_id)
        if existing:
            return AnalyzeResult(
                raw_id=int(existing["raw_id"]),
                report_id=int(existing["report_id"]),
                candidate_count=0,
                selected_count=int(existing.get("selected_count") or 0),
                report_json={},
            )

    items = load_recent_rss_items(hours=hours, limit=limit, category=rss_category)
    if not items:
        prefix = _report_title_prefix(report_type, rss_category)
        report_json = {
            "title": f"{prefix}新闻精选",
            "key_news": [],
            "daily_trend": "指定时间范围内没有采集到 RSS 新闻。",
        }
    else:
        markdown = rss_items_to_markdown(
            items, report_type=report_type, rss_category=rss_category
        )
        report_json = analyze_rss_markdown(
            markdown,
            pipeline_run_id=pipeline_run_id,
            model_config_id=model_config_id,
            prompt_version_id=prompt_version_id,
        )

    raw_id = insert_llm_raw(report_json, pipeline_run_id=pipeline_run_id)
    report_id = normalize_raw_report(
        raw_id,
        pipeline_run_id=pipeline_run_id,
        report_type=report_type,
        rss_category=rss_category,
        model_config_id=model_config_id,
        prompt_version_id=prompt_version_id,
    )
    key_news = report_json.get("key_news")
    selected_count = len(key_news) if isinstance(key_news, list) else 0

    return AnalyzeResult(
        raw_id=raw_id,
        report_id=report_id,
        candidate_count=len(items),
        selected_count=selected_count,
        report_json=report_json,
    )
