from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ScheduleConfig:
    id: int
    name: str
    task_type: str
    cron_expression: str
    timezone: str
    enabled: int
    prevent_overlap: int
    max_runtime_seconds: int
    retry_count: int
    retry_interval_seconds: int
    next_run_at: Optional[datetime]
    last_run_at: Optional[datetime]
    rss_category: Optional[str] = None
    model_config_id: Optional[int] = None
    prompt_version_id: Optional[int] = None
    tts_config_id: Optional[int] = None
    report_type: str = "general"
    auto_render: int = 1
    auto_publish: int = 0
    render_engine: str = "remotion"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_cron_field(field: str, minimum: int, maximum: int) -> set[int]:
    values: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue

        if part == "*":
            values.update(range(minimum, maximum + 1))
            continue

        if part.startswith("*/"):
            step = int(part[2:])
            if step <= 0:
                raise ValueError(f"Invalid cron step: {part}")
            values.update(range(minimum, maximum + 1, step))
            continue

        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if start > end:
                raise ValueError(f"Invalid cron range: {part}")
            values.update(range(start, end + 1))
            continue

        values.add(int(part))

    invalid = [value for value in values if value < minimum or value > maximum]
    if invalid:
        raise ValueError(f"Invalid cron value {invalid[0]} for range {minimum}-{maximum}")
    return values


def cron_matches(moment: datetime, cron_expression: str) -> bool:
    fields = cron_expression.split()
    if len(fields) != 5:
        raise ValueError("Only 5-field cron expressions are supported")

    minutes = parse_cron_field(fields[0], 0, 59)
    hours = parse_cron_field(fields[1], 0, 23)
    days = parse_cron_field(fields[2], 1, 31)
    months = parse_cron_field(fields[3], 1, 12)
    weekdays = parse_cron_field(fields[4], 0, 6)

    cron_weekday = (moment.weekday() + 1) % 7
    return (
        moment.minute in minutes
        and moment.hour in hours
        and moment.day in days
        and moment.month in months
        and cron_weekday in weekdays
    )


def next_run_after(
    cron_expression: str,
    tz_name: str,
    after_utc: Optional[datetime] = None,
) -> datetime:
    tz = ZoneInfo(tz_name)
    base_utc = after_utc or utc_now()
    if base_utc.tzinfo is None:
        base = base_utc.replace(tzinfo=timezone.utc).astimezone(tz)
    else:
        base = base_utc.astimezone(tz)

    candidate = (base + timedelta(minutes=1)).replace(second=0, microsecond=0)
    max_checks = 366 * 24 * 60
    for _ in range(max_checks):
        if cron_matches(candidate, cron_expression):
            return candidate.astimezone(timezone.utc).replace(tzinfo=None)
        candidate += timedelta(minutes=1)

    raise ValueError(f"Unable to calculate next run for cron: {cron_expression}")
