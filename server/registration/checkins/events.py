"""Event management, calendar selection and QR presentation endpoints."""
from datetime import datetime
import json
import secrets
import sqlite3
import time

from flask import Response, abort, jsonify, request

from ..permissions import is_admin
from ..private_data import actor, audit, encrypt
from .calendar import calendar_events, calendar_events_on_day
from .deletion import delete_event
from .domain import LEGACY_RETAIN_FOREVER, ZONE, page_slug
from .http import checkin, payload
from .permissions import administrator, organizer, require_factor
from .presentation import managed_event
from .repository import find_event, store
from .validation import validate_event


@checkin.get("/api/checkin/calendar-events")
def selectable_calendar_events():
    with store().connection() as connection:
        organizer(connection)
    day = request.args.get("date", datetime.now(ZONE).date().isoformat())
    return jsonify(date=day, events=calendar_events_on_day(day))


@checkin.route("/api/checkin/events", methods=("GET", "POST"))
def events():
    with store().connection() as connection:
        if request.method == "POST":
            connection.execute("BEGIN IMMEDIATE")
        user = organizer(connection)
        if request.method == "GET":
            connection.row_factory = sqlite3.Row
            manager = is_admin(connection, user["id"])
            items = connection.execute("SELECT * FROM checkin_events" +
                                       ("" if manager else " WHERE creator_id=?") +
                                       " ORDER BY created_at DESC,id DESC", () if manager else (user["id"],)).fetchall()
            return jsonify(events=[managed_event(connection, dict(event)) for event in items])
        value = validate_event(payload())
        if value["calendar_uid"] and not any(
                event["uid"] == value["calendar_uid"] for event in calendar_events()):
            abort(422, description="关联的日历事件已不存在，请重新选择。")
        if connection.execute("SELECT count(*) FROM checkin_events WHERE creator_id=?",
                              (user["id"],)).fetchone()[0] >= 100:
            abort(409, description="已达到 100 个签到表的创建上限，请联系管理员。")
        event_id, token, now = secrets.token_hex(16), secrets.token_urlsafe(32), int(time.time())
        connection.execute(
            "INSERT INTO checkin_events VALUES(?,?,?,?,?,?,?,?,?,0,?,?,?)",
            (event_id, user["id"], value["title"], value["location"], value["purpose"],
             json.dumps(value["fields"]), value["starts_at"], value["ends_at"], now,
             store().token_hash(token), encrypt(event_id, "checkin:join", token), LEGACY_RETAIN_FOREVER))
        connection.execute("INSERT INTO checkin_options VALUES(?,?,?)",
                           (event_id, value["calendar_uid"], json.dumps(value["custom_fields"], ensure_ascii=False)))
        connection.execute("INSERT INTO checkin_pages(event_id,slug,description,visibility) VALUES(?,?,?,?)",
                           (event_id, page_slug(value["title"], event_id), value["description"], value["visibility"]))
        audit(connection, user["id"], "checkin.create", value["fields"])
        return jsonify(event=managed_event(connection, find_event(connection, event_id=event_id))), 201


@checkin.route("/api/checkin/events/<event_id>", methods=("GET", "PATCH", "DELETE"))
def event_detail(event_id):
    with store().connection() as connection:
        if request.method != "GET":
            connection.execute("BEGIN IMMEDIATE")
        event = find_event(connection, event_id=event_id)
        user = administrator(connection) if request.method != "GET" else actor()
        if request.method == "PATCH":
            value = payload()
            if value == {"rotate": True}:
                token = secrets.token_urlsafe(32)
                connection.execute("UPDATE checkin_events SET token_hash=?,token_cipher=? WHERE id=?",
                                   (store().token_hash(token), encrypt(event_id, "checkin:join", token), event_id))
            elif set(value) == {"closed"} and type(value["closed"]) is bool:
                connection.execute("UPDATE checkin_events SET closed=? WHERE id=?", (int(value["closed"]), event_id))
            else:
                abort(422, description="仅支持暂停/恢复签到或更换二维码。")
            audit(connection, user["id"], "checkin.rotate" if "rotate" in value else "checkin.status", [event_id])
            event = find_event(connection, event_id=event_id)
        if request.method == "DELETE":
            require_factor(connection, user)
            delete_event(connection, event, user)
            return jsonify(ok=True)
        return jsonify(event=managed_event(connection, event))


@checkin.get("/api/checkin/events/<event_id>/qr.svg")
def qr(event_id):
    import qrcode
    from qrcode.image.svg import SvgPathFillImage
    with store().connection() as connection:
        event = managed_event(connection, find_event(connection, event_id=event_id))
    image = qrcode.make(event["join_url"], image_factory=SvgPathFillImage, border=4,
                        error_correction=qrcode.constants.ERROR_CORRECT_M)
    return Response(image.to_string(), content_type="image/svg+xml")
