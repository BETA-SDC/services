"""Explicit public/managed views and introduction-scoped event access."""
import json
import sqlite3

from flask import abort

from ..access_control.policy import is_admin
from ..privacy.identity import actor
from ..privacy.crypto import decrypt
from ..site_accounts.domain import ACCOUNT_HOST
from .domain import POLICY, status
from .permissions import can_edit_page, eligible, member
from .repository import event_options, page_options


def visible_page(connection, creator_id, slug):
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "SELECT e.* FROM checkin_events e JOIN checkin_pages p ON p.event_id=e.id "
        "WHERE e.creator_id=? AND p.slug=?", (creator_id, slug)).fetchone()
    if not row:
        abort(404, description="签到介绍不存在或无权访问。")
    event = dict(row)
    options = page_options(connection, event)
    user = actor()
    editable = can_edit_page(connection, event, user)
    if not (editable or options["visibility"] == "public"
            or options["visibility"] == "members" and member(connection, user)):
        abort(404, description="签到介绍不存在或无权访问。")
    creator = connection.execute("SELECT real_name FROM registrations WHERE id=?", (creator_id,)).fetchone()
    result = {
        "event": {"title": event["title"], "description": options["description"],
                  "organizer": ((creator[0] or "").strip() if creator else "") or "BETA-SDC 活动发起人",
                  "count": connection.execute("SELECT count(*) FROM checkin_entries WHERE event_id=?",
                                             (event["id"],)).fetchone()[0]},
        "can_edit": editable,
    }
    if eligible(connection, event, user):
        result.update(event_id=event["id"], records_path=f"/checkin/records/{event['id']}")
    if editable:
        result.update(event_id=event["id"], visibility=options["visibility"],
                      manage_path=f"/checkin?event={event['id']}")
    return result


def public_event(connection, event, options=None):
    creator = connection.execute("SELECT real_name FROM registrations WHERE id=?",
                                 (event["creator_id"],)).fetchone()
    options = event_options(connection, event["id"]) if options is None else options
    real_name = (creator[0] or "").strip() if creator else ""
    return {key: event[key] for key in (
        "id", "title", "location", "purpose", "starts_at", "ends_at")} | {
            "retain_until": None, "retention": "until_admin_deletion",
            "fields": json.loads(event["fields_json"]), "custom_fields": options["custom_fields"],
            "status": status(event), "policy": POLICY,
            "organizer": real_name or "BETA-SDC 活动发起人"}


def managed_event(connection, event):
    options = event_options(connection, event["id"])
    token = decrypt(event["id"], "checkin:join", event["token_cipher"])
    deletion = connection.execute(
        "SELECT id,status,reason,note FROM checkin_deletion_requests WHERE event_id=? ORDER BY id DESC LIMIT 1",
        (event["id"],)).fetchone()
    return public_event(connection, event, options) | options | page_options(connection, event) | {
        "can_manage": is_admin(connection, actor()["id"]),
        "deletion_request": dict(zip(("id", "status", "reason", "note"), deletion)) if deletion else None,
        "join_url": f"https://{ACCOUNT_HOST}/checkin/join/{token}",
        "qr_url": f"/api/checkin/events/{event['id']}/qr.svg",
        "count": connection.execute("SELECT count(*) FROM checkin_entries WHERE event_id=?",
                                    (event["id"],)).fetchone()[0]}


def scoped_event(connection, event_id, user):
    connection.row_factory = sqlite3.Row
    row = connection.execute("SELECT * FROM checkin_events WHERE id=?", (event_id,)).fetchone()
    if not row:
        abort(404, description="签到不存在或无权访问。")
    event = dict(row)
    if not eligible(connection, event, user):
        abort(403, description="仅发起人、BETA-SDC 成员或管理员可以申请。")
    options = page_options(connection, event)
    slug = connection.execute("SELECT slug FROM checkin_pages WHERE event_id=?", (event_id,)).fetchone()[0]
    visible_page(connection, event["creator_id"], slug)
    return event, options
