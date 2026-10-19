"""Shared attendance policy, field classification, time and output formatting."""
from zoneinfo import ZoneInfo
import re
import time

from ..private_validation import DOCUMENTS


FIELDS = {"name": "姓名", "email": "邮箱", "student_id": "学号", "phone": "手机号码", "document": "身份证／其他证件"}

BASIC_FIELDS = frozenset(("name", "email", "student_id"))


POLICY = "checkin-2026-10-19-v5"


LEGACY_RETAIN_FOREVER = 253402300799


ZONE = ZoneInfo("Asia/Shanghai")


PAT_PATTERN = r"beta_checkin_[A-Za-z0-9_-]{43}"


def page_slug(title, event_id):
    name = re.sub(r"[^\w-]+", "-", title).strip("-_")[:64] or "event"
    return f"{name}--{event_id}"


def status(event):
    now = int(time.time())
    return ("closed" if event["closed"] else "scheduled" if now < event["starts_at"]
            else "ended" if now >= event["ends_at"] else "open")


def csv_safe(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def format_answer(value):
    if isinstance(value, dict):
        return f"{DOCUMENTS.get(value['type'], value['type'])} / {value['country']} / {value['number']}"
    return value
