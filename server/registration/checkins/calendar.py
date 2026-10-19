"""Read-only calendar integration with Beijing-date overlap filtering."""
from datetime import date, datetime, timedelta
from pathlib import Path
import json
import os
import re

from flask import abort, current_app

from .domain import ZONE


def calendar_events():
    state = Path(current_app.config.get("CHECKIN_CALENDAR_STATE") or
                 os.environ.get("CALENDAR_STATE", "/var/lib/betasdc-calendar"))
    try:
        source = json.loads((state / "current/server-events.json").read_text(encoding="utf-8"))
        if not isinstance(source, dict) or not isinstance(source.get("events"), list):
            raise ValueError()
        events = []
        for item in source["events"]:
            if (not isinstance(item, dict) or not all(isinstance(item.get(key), str)
                    for key in ("uid", "title", "start"))):
                raise ValueError()
            events.append({key: item.get(key) for key in
                           ("uid", "title", "location", "start", "end", "allDay", "scope", "status")})
        return events
    except (OSError, ValueError, TypeError):
        abort(503, description="日历暂时不可用，可先手动创建签到。")


def calendar_events_on_day(value):
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError()
        day = date.fromisoformat(value)
        start = datetime.combine(day, datetime.min.time(), ZONE)
        end = start + timedelta(days=1)
    except (ValueError, OverflowError):
        abort(422, description="请选择有效的事件日期。")

    def parse(value):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=ZONE) if parsed.tzinfo is None else parsed.astimezone(ZONE)

    selected = []
    try:
        for event in calendar_events():
            first = parse(event["start"])
            last = parse(event["end"]) if event["end"] else first + (
                timedelta(days=1) if event["allDay"] else timedelta(microseconds=1))
            if last <= first:
                last = first + timedelta(microseconds=1)
            if first < end and last > start:
                selected.append(event)
    except (ValueError, TypeError, OverflowError):
        abort(503, description="日历事件时间无效，可先手动创建签到。")
    return selected
