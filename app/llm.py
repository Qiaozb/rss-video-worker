from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List

import requests

from app.config import settings
from app.db import (
    ModelConfig,
    PromptVersion,
    get_default_model_config,
    get_default_prompt_version,
    get_model_config,
    get_prompt_version,
    record_llm_call,
)
from app.default_prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE


class LLMConfigurationError(RuntimeError):
    pass


class LLMResponseValidationError(ValueError):
    def __init__(self, issues: List[str]):
        self.issues = issues
        preview = "; ".join(issues[:8])
        if len(issues) > 8:
            preview += f"; ... 共 {len(issues)} 个问题"
        super().__init__(f"LLM response schema validation failed: {preview}")


REPORT_NEWS_FIELDS = [
    "title",
    "pubdate",
    "summary",
    "related_field",
    "importance",
    "reserve_reason",
    "link",
]
VOICEOVER_FIELD_ALIASES = ["voiceoverscript", "voiceover_script", "voiceoverScript"]
IMPORTANCE_VALUES = {"高", "中", "低"}


def extract_json_object(text: str) -> Dict[str, Any]:
    stripped = text.strip()
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
    stripped = re.sub(r"\s*```$", "", stripped)

    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end < start:
            raise
        value = json.loads(stripped[start : end + 1])

    if not isinstance(value, dict):
        raise ValueError("LLM response JSON root must be an object")
    return value


def validate_report_json(value: Dict[str, Any]) -> None:
    issues: List[str] = []

    if not isinstance(value.get("title"), str):
        issues.append("title must be a string")

    if not isinstance(value.get("daily_trend"), str):
        issues.append("daily_trend must be a string")

    news = value.get("key_news")
    if not isinstance(news, list):
        issues.append("key_news must be an array")
    else:
        for index, item in enumerate(news, start=1):
            path = f"key_news[{index}]"
            if not isinstance(item, dict):
                issues.append(f"{path} must be an object")
                continue

            for field in REPORT_NEWS_FIELDS:
                if field not in item:
                    issues.append(f"{path}.{field} is required")
                elif not isinstance(item.get(field), str):
                    issues.append(f"{path}.{field} must be a string")

            importance = item.get("importance")
            if isinstance(importance, str) and importance not in IMPORTANCE_VALUES:
                issues.append(f"{path}.importance must be one of 高/中/低")

            voiceover_value = None
            for field in VOICEOVER_FIELD_ALIASES:
                if field in item:
                    voiceover_value = item.get(field)
                    break
            if voiceover_value is None:
                issues.append(f"{path}.voiceoverscript is required")
            elif not isinstance(voiceover_value, str):
                issues.append(f"{path}.voiceoverscript must be a string")

    if issues:
        raise LLMResponseValidationError(issues)


def normalize_report_json(value: Dict[str, Any]) -> Dict[str, Any]:
    news = value.get("key_news")
    if not isinstance(news, list):
        news = []

    normalized_news = []
    for item in news:
        if not isinstance(item, dict):
            continue
        importance = item.get("importance") or "中"
        if importance not in {"高", "中", "低"}:
            importance = "中"
        normalized_news.append(
            {
                "title": str(item.get("title") or ""),
                "pubdate": str(item.get("pubdate") or ""),
                "summary": str(item.get("summary") or ""),
                "related_field": str(item.get("related_field") or ""),
                "importance": importance,
                "reserve_reason": str(item.get("reserve_reason") or ""),
                "link": str(item.get("link") or ""),
                "voiceoverscript": str(
                    item.get("voiceoverscript")
                    or item.get("voiceover_script")
                    or item.get("voiceoverScript")
                    or ""
                ),
            }
        )

    return {
        "title": str(value.get("title") or "新闻精选"),
        "key_news": normalized_news,
        "daily_trend": str(value.get("daily_trend") or ""),
    }


def parse_report_response(content: str) -> Dict[str, Any]:
    parsed = extract_json_object(content)
    validate_report_json(parsed)
    return normalize_report_json(parsed)


def fallback_model_config() -> ModelConfig:
    return ModelConfig(
        id=0,
        name="env",
        provider="openai-compatible",
        base_url=settings.llm_base_url,
        model_name=settings.llm_model,
        api_key=settings.llm_api_key,
        timeout_seconds=settings.llm_timeout_seconds,
        temperature=settings.llm_temperature,
        max_retries=0,
        enabled=1,
        is_default=0,
    )


def active_model_config() -> ModelConfig:
    try:
        configured = get_default_model_config()
    except Exception:
        configured = None
    return configured or fallback_model_config()


def fallback_prompt_version() -> PromptVersion:
    return PromptVersion(
        id=0,
        name="内置默认提示词",
        system_prompt=SYSTEM_PROMPT,
        user_prompt_template=USER_PROMPT_TEMPLATE,
        enabled=1,
        is_default=0,
    )


def active_prompt_version() -> PromptVersion:
    try:
        configured = get_default_prompt_version()
    except Exception:
        configured = None
    return configured or fallback_prompt_version()


def render_user_prompt(template: str, markdown: str) -> str:
    return template.replace("{markdown}", markdown)


def request_chat_completion(
    config: ModelConfig,
    messages: list[Dict[str, str]],
    *,
    pipeline_run_id: int | None = None,
    prompt_version_id: int | None = None,
    purpose: str = "chat_completion",
    repair_attempt: int = 0,
    response_format: Dict[str, str] | None = {"type": "json_object"},
    max_tokens: int | None = None,
) -> str:
    if not config.api_key:
        record_llm_call(
            purpose=purpose,
            model_name=config.model_name,
            status="failed",
            duration_ms=0,
            pipeline_run_id=pipeline_run_id,
            model_config_id=config.id or None,
            prompt_version_id=prompt_version_id,
            repair_attempt=repair_attempt,
            error_message="LLM_API_KEY is not configured",
        )
        raise LLMConfigurationError("LLM_API_KEY is not configured")

    base_url = config.base_url.rstrip("/")
    url = f"{base_url}/chat/completions"
    started_at = time.perf_counter()
    try:
        request_payload: Dict[str, Any] = {
            "model": config.model_name,
            "messages": messages,
            "temperature": config.temperature,
        }
        if response_format is not None:
            request_payload["response_format"] = response_format
        if max_tokens is not None:
            request_payload["max_tokens"] = max_tokens

        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
            json=request_payload,
            timeout=config.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        usage = payload.get("usage") if isinstance(payload, dict) else None
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        record_llm_call(
            purpose=purpose,
            model_name=config.model_name,
            status="succeeded",
            duration_ms=duration_ms,
            pipeline_run_id=pipeline_run_id,
            model_config_id=config.id or None,
            prompt_version_id=prompt_version_id,
            prompt_tokens=_int_usage(usage, "prompt_tokens"),
            completion_tokens=_int_usage(usage, "completion_tokens"),
            total_tokens=_int_usage(usage, "total_tokens"),
            repair_attempt=repair_attempt,
        )
        return payload["choices"][0]["message"]["content"]
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        record_llm_call(
            purpose=purpose,
            model_name=config.model_name,
            status="failed",
            duration_ms=duration_ms,
            pipeline_run_id=pipeline_run_id,
            model_config_id=config.id or None,
            prompt_version_id=prompt_version_id,
            repair_attempt=repair_attempt,
            error_message=str(exc),
        )
        raise


def _int_usage(usage: Any, key: str) -> int | None:
    if not isinstance(usage, dict):
        return None
    value = usage.get(key)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def repair_report_response(
    config: ModelConfig,
    content: str,
    error: Exception,
    attempt: int,
    pipeline_run_id: int | None = None,
    prompt_version_id: int | None = None,
) -> str:
    if isinstance(error, LLMResponseValidationError):
        error_text = "\n".join(f"- {issue}" for issue in error.issues)
    else:
        error_text = str(error)

    clipped_content = content
    if len(clipped_content) > 40000:
        clipped_content = clipped_content[:40000] + "\n...（内容过长，已截断）"

    return request_chat_completion(
        config,
        [
            {
                "role": "system",
                "content": (
                    "你是一个 JSON 修复器。你只能输出严格 JSON，不能输出 Markdown、代码块、解释、"
                    "分析过程或 <think>。不要新增事实，不要编造链接，只修复字段、类型和格式。"
                ),
            },
            {
                "role": "user",
                "content": f"""下面是一次 RSS 新闻筛选模型的输出，但它不符合目标 JSON Schema。

请修复为严格 JSON，格式必须是：
{{
  "title": "<与任务类型匹配的报告标题>",
  "key_news": [
    {{
      "title": "",
      "pubdate": "",
      "summary": "",
      "related_field": "",
      "importance": "高",
      "reserve_reason": "",
      "link": "",
      "voiceoverscript": ""
    }}
  ],
  "daily_trend": ""
}}

规则：
- 根节点必须是对象。
- title 和 daily_trend 必须是字符串。
- key_news 必须是数组。
- key_news 中每个对象必须包含 title、pubdate、summary、related_field、importance、reserve_reason、link、voiceoverscript。
- importance 只能是“高”“中”“低”。
- 没有内容时使用空字符串或空数组。
- 不要新增事实，不要编造 source link。
- 只输出修复后的 JSON。

这是第 {attempt} 次修复。

校验错误：
{error_text}

原始输出：
{clipped_content}
""",
            },
        ],
        pipeline_run_id=pipeline_run_id,
        prompt_version_id=prompt_version_id,
        purpose="rss_report_repair",
        repair_attempt=attempt,
    )


def resolve_model_config(model_config_id: int | None = None) -> ModelConfig:
    """使用指定模型配置；ID 无效或被禁用时抛出 ValueError，避免静默回退。"""
    if model_config_id:
        configured = get_model_config(model_config_id)
        if not configured or not configured.enabled:
            raise ValueError(
                f"指定的模型配置不可用 (model_config_id={model_config_id})，"
                "请检查配置是否存在且已启用"
            )
        return configured
    return active_model_config()


def resolve_prompt_version(prompt_version_id: int | None = None) -> PromptVersion:
    """使用指定提示词版本；ID 无效或被禁用时抛出 ValueError，避免静默回退。"""
    if prompt_version_id:
        configured = get_prompt_version(prompt_version_id)
        if not configured or not configured.enabled:
            raise ValueError(
                f"指定的提示词版本不可用 (prompt_version_id={prompt_version_id})，"
                "请检查配置是否存在且已启用"
            )
        return configured
    return active_prompt_version()


def analyze_rss_markdown(
    markdown: str,
    pipeline_run_id: int | None = None,
    model_config_id: int | None = None,
    prompt_version_id: int | None = None,
) -> Dict[str, Any]:
    config = resolve_model_config(model_config_id)
    prompt = resolve_prompt_version(prompt_version_id)
    content = request_chat_completion(
        config,
        [
            {"role": "system", "content": prompt.system_prompt},
            {"role": "user", "content": render_user_prompt(prompt.user_prompt_template, markdown)},
        ],
        pipeline_run_id=pipeline_run_id,
        prompt_version_id=prompt.id or None,
        purpose="rss_report_analyze",
    )

    try:
        return parse_report_response(content)
    except Exception as exc:
        last_error: Exception = exc

    repair_attempts = max(1, int(config.max_retries or 0))
    for attempt in range(1, repair_attempts + 1):
        content = repair_report_response(
            config,
            content,
            last_error,
            attempt,
            pipeline_run_id=pipeline_run_id,
            prompt_version_id=prompt.id or None,
        )
        try:
            return parse_report_response(content)
        except Exception as exc:
            last_error = exc

    raise last_error


def test_model_config(config: ModelConfig) -> Dict[str, Any]:
    started_at = time.perf_counter()
    content = request_chat_completion(
        config,
        [
            {"role": "system", "content": "你是连接测试助手，只输出 pong。"},
            {"role": "user", "content": "pong"},
        ],
        purpose="model_config_test",
        response_format=None,
        max_tokens=8,
    )
    duration_ms = int((time.perf_counter() - started_at) * 1000)
    return {
        "ok": True,
        "message": f"API 连接成功，模型 {config.model_name} 响应正常（耗时 {duration_ms}ms）",
        "model": config.model_name,
        "base_url": config.base_url,
        "duration_ms": duration_ms,
        "response": content.strip(),
    }
