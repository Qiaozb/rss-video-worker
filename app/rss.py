from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

try:
    from defusedxml import ElementTree
except ModuleNotFoundError:  # pragma: no cover - defusedxml 是 requirements 强依赖
    from xml.etree import ElementTree  # type: ignore[no-redef]

import requests

from app.db import (
    RSSSource,
    get_enabled_rss_sources,
    get_rss_source,
    mark_rss_source_error,
    mark_rss_source_success,
    upsert_rss_item,
)


TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {
    "from",
    "spm",
    "seid",
    "share",
    "share_source",
    "share_token",
    "timestamp",
    "weiboauthoruid",
}

# 限制单条 feed 体量，防御实体膨胀 / billion laughs 等 XML DoS。
MAX_FEED_BYTES = 5 * 1024 * 1024


@dataclass
class ParsedRSSItem:
    title: str
    link: Optional[str]
    normalized_link: Optional[str]
    pubdate: Optional[str]
    description: str
    content_hash: str
    raw: Dict[str, Any]


@dataclass
class SourceCollectResult:
    source_id: int
    source_name: str
    url: str
    fetched_count: int
    inserted_count: int
    duplicate_count: int
    status: str
    error: Optional[str] = None


def clean_text(value: Optional[str]) -> str:
    if not value:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_pubdate(value: Optional[str]) -> Optional[str]:
    if not value:
        return None

    raw = clean_text(value)
    if not raw:
        return None

    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            continue

    return None


def normalize_link(link: Optional[str]) -> Optional[str]:
    if not link:
        return None

    link = html.unescape(str(link)).strip()
    if not link:
        return None

    parts = urlsplit(link)
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lower_key = key.lower()
        if lower_key in TRACKING_QUERY_KEYS:
            continue
        if any(lower_key.startswith(prefix) for prefix in TRACKING_QUERY_PREFIXES):
            continue
        query.append((key, value))

    normalized = urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/") or parts.path,
            urlencode(query, doseq=True),
            "",
        )
    )
    return normalized or link


def content_hash(normalized_link: Optional[str], title: str, pubdate: Optional[str]) -> str:
    basis = normalized_link or f"{title}|{pubdate or ''}"
    return hashlib.sha256(basis.strip().lower().encode("utf-8")).hexdigest()


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def child_text(element: ElementTree.Element, names: Iterable[str]) -> str:
    wanted = {name.lower() for name in names}
    for child in list(element):
        if local_name(child.tag) in wanted:
            return "".join(child.itertext()).strip()
    return ""


def atom_link(element: ElementTree.Element) -> str:
    fallback = ""
    for child in list(element):
        if local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href", "").strip()
        rel = child.attrib.get("rel", "alternate").strip().lower()
        if href and rel == "alternate":
            return href
        if href and not fallback:
            fallback = href
    return fallback


def parse_feed(feed_xml: str) -> List[ParsedRSSItem]:
    root = ElementTree.fromstring(feed_xml)
    candidates = [
        element
        for element in root.iter()
        if local_name(element.tag) in {"item", "entry"}
    ]

    parsed: List[ParsedRSSItem] = []
    for element in candidates:
        is_atom = local_name(element.tag) == "entry"
        title = clean_text(child_text(element, ["title"]))
        link = atom_link(element) if is_atom else clean_text(child_text(element, ["link"]))
        description = clean_text(
            child_text(element, ["description", "summary", "content", "encoded"])
        )
        pubdate_raw = child_text(element, ["pubDate", "published", "updated", "date", "dc:date"])
        pubdate = parse_pubdate(pubdate_raw)

        if not title and not link:
            continue

        normalized = normalize_link(link)
        raw = {
            "title": title,
            "link": link,
            "normalized_link": normalized,
            "pubdate": pubdate_raw,
            "description": description,
        }
        parsed.append(
            ParsedRSSItem(
                title=title or link,
                link=link or None,
                normalized_link=normalized,
                pubdate=pubdate,
                description=description,
                content_hash=content_hash(normalized, title or link, pubdate),
                raw=raw,
            )
        )

    return parsed


def fetch_feed(source: RSSSource) -> str:
    response = requests.get(
        source.url,
        timeout=source.request_timeout_seconds,
        headers={
            "User-Agent": "game-daily-rss-bot/0.1",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        },
    )
    response.raise_for_status()
    if len(response.content) > MAX_FEED_BYTES:
        raise RuntimeError(
            f"RSS feed too large ({len(response.content)} bytes > {MAX_FEED_BYTES}); "
            f"source_id={source.id}"
        )
    return response.text


def collect_source(source: RSSSource) -> SourceCollectResult:
    try:
        feed_xml = fetch_feed(source)
        items = parse_feed(feed_xml)
        inserted_count = 0

        for item in items:
            inserted = upsert_rss_item(
                source_id=source.id,
                title=item.title,
                link=item.link,
                normalized_link=item.normalized_link,
                pubdate=item.pubdate,
                description=item.description,
                content_hash=item.content_hash,
                raw_json=json.dumps(item.raw, ensure_ascii=False),
            )
            if inserted:
                inserted_count += 1

        mark_rss_source_success(source.id)
        return SourceCollectResult(
            source_id=source.id,
            source_name=source.name,
            url=source.url,
            fetched_count=len(items),
            inserted_count=inserted_count,
            duplicate_count=len(items) - inserted_count,
            status="ok",
        )
    except Exception as exc:
        error = str(exc)
        mark_rss_source_error(source.id, error)
        return SourceCollectResult(
            source_id=source.id,
            source_name=source.name,
            url=source.url,
            fetched_count=0,
            inserted_count=0,
            duplicate_count=0,
            status="failed",
            error=error,
        )


def collect_enabled_sources(category: Optional[str] = None) -> Dict[str, Any]:
    sources = get_enabled_rss_sources(category=category)
    results = [collect_source(source) for source in sources]
    return summarize_results(results)


def collect_one_source(source_id: int) -> Optional[Dict[str, Any]]:
    source = get_rss_source(source_id)
    if source is None:
        return None
    return summarize_results([collect_source(source)])


def preview_source(source_id: int, limit: int = 10) -> Optional[Dict[str, Any]]:
    source = get_rss_source(source_id)
    if source is None:
        return None

    feed_xml = fetch_feed(source)
    items = parse_feed(feed_xml)
    preview_limit = max(1, min(limit, 50))
    preview_items = [
        {
            "title": item.title,
            "link": item.link,
            "normalized_link": item.normalized_link,
            "pubdate": item.pubdate,
            "description": item.description,
            "content_hash": item.content_hash,
        }
        for item in items[:preview_limit]
    ]
    return {
        "source_id": source.id,
        "source_name": source.name,
        "url": source.url,
        "fetched_count": len(items),
        "preview_count": len(preview_items),
        "items": preview_items,
    }


def summarize_results(results: List[SourceCollectResult]) -> Dict[str, Any]:
    return {
        "source_count": len(results),
        "ok_count": sum(1 for item in results if item.status == "ok"),
        "failed_count": sum(1 for item in results if item.status == "failed"),
        "fetched_count": sum(item.fetched_count for item in results),
        "inserted_count": sum(item.inserted_count for item in results),
        "duplicate_count": sum(item.duplicate_count for item in results),
        "sources": [item.__dict__ for item in results],
    }
