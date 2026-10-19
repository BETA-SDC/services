"""Validate event settings, introduction visibility and exact submitted fields."""
import re
import time

from ..private_validation import validate_private
from ..validation import InvalidRegistration
from .domain import FIELDS


def text(value, label, maximum, optional=False):
    if not isinstance(value, str):
        raise InvalidRegistration(f"请填写{label}。")
    value = value.strip()
    if (not value and not optional) or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise InvalidRegistration(f"{label}长度或格式无效。")
    return value


def validate_event(value):
    required = {"title", "location", "purpose", "fields", "starts_at", "ends_at"}
    if not required <= set(value) or set(value) - required - {"calendar_uid", "custom_fields", "description", "visibility"}:
        raise InvalidRegistration("请填写活动、用途、签到时段及需要的信息。")
    page = validate_page_content({"description": value.get("description", ""),
                                  "visibility": value.get("visibility", "public")})
    custom = value.get("custom_fields", [])
    if not isinstance(custom, list) or len(custom) > 8:
        raise InvalidRegistration("自定义条目最多 8 项。")
    custom_fields = []
    for index, item in enumerate(custom, 1):
        if not isinstance(item, dict) or set(item) != {"id", "label"} or item["id"] != f"custom_{index}":
            raise InvalidRegistration("自定义条目格式无效。")
        custom_fields.append({"id": item["id"], "label": text(item["label"], "条目名称", 60)})
    names = [item["label"].casefold() for item in custom_fields] + [v.casefold() for v in FIELDS.values()]
    if len(names) != len(set(names)):
        raise InvalidRegistration("条目名称不能重复或使用内置条目名称。")
    custom_ids = [item["id"] for item in custom_fields]
    fields = value["fields"]
    if (not isinstance(fields, list) or not fields
            or any(not isinstance(f, str) or f not in (*FIELDS, *custom_ids) for f in fields)
            or len(fields) != len(set(fields)) or not set(fields) & {"email", "student_id", "phone", "document"}
            or not set(custom_ids) <= set(fields)):
        raise InvalidRegistration("请选择需要的信息，并至少包含邮箱、学号、手机号或证件中的一项以防重复。")
    start, end, now = value["starts_at"], value["ends_at"], int(time.time())
    if (type(start) is not int or type(end) is not int or not now - 300 <= start <= now + 90 * 86400
            or not start < end <= start + 7 * 86400 or end <= now):
        raise InvalidRegistration("签到时段无效；单次最长 7 天，最远可提前 90 天创建。")
    return {"title": text(value["title"], "活动名称", 120),
            "location": text(value["location"], "地点", 160, optional=True),
            "purpose": text(value["purpose"], "信息使用目的", 300),
            "fields": [field for field in (*FIELDS, *custom_ids) if field in fields],
            "custom_fields": custom_fields,
            "calendar_uid": text(value.get("calendar_uid", ""), "日历事件编号", 500, optional=True),
            "starts_at": start, "ends_at": end} | page


def validate_page_content(data):
    if set(data) != {"description", "visibility"} or data["visibility"] not in ("public", "members", "private"):
        raise InvalidRegistration("请选择有效的介绍查看权限。")
    description = data["description"]
    if not isinstance(description, str) or len(description) > 2000 or "\x00" in description:
        raise InvalidRegistration("活动介绍最多 2000 字。")
    return {"description": description.strip(), "visibility": data["visibility"]}


def validate_answers(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise InvalidRegistration("请完整填写本次要求的信息，不要提交额外资料。")
    result = {}
    for field in fields:
        if field in ("phone", "student_id", "document"):
            result[field] = validate_private({field: value[field]})[field]
            if not result[field]:
                raise InvalidRegistration(f"请填写{FIELDS[field]}。")
        elif field.startswith("custom_"):
            result[field] = text(value[field], "自定义条目", 500)
        else:
            result[field] = text(value[field], FIELDS[field], 80 if field == "name" else 254)
            if field == "email":
                if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}",
                                    result[field]):
                    raise InvalidRegistration("邮箱格式无效。")
                result[field] = result[field].casefold()
    return result
