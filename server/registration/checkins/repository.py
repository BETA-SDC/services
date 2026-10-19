"""Attendance lookups and transient cleanup using the caller's connection."""
from urllib.parse import quote
import json
import re
import sqlite3
import time

from flask import abort, current_app, session

from ..access_control.policy import is_admin
from .permissions import organizer


def store():
    return current_app.extensions["registration_store"]


def purge_expired(connection, now=None):
    now = int(time.time()) if now is None else now
    # Only transient grants expire; attendance and approval records never age out.
    connection.execute("DELETE FROM checkin_grants WHERE expires_at<=? OR event_id NOT IN "
                       "(SELECT id FROM checkin_events)", (now,))


def find_event(connection, event_id=None, token=None):
    connection.row_factory = sqlite3.Row
    if token is not None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            abort(404, description="签到链接无效或已过期。")
        row = connection.execute("SELECT * FROM checkin_events WHERE token_hash=?",
                                 (store().token_hash(token),)).fetchone()
    else:
        user = organizer(connection)
        row = connection.execute("SELECT * FROM checkin_events WHERE id=?" +
                                 ("" if is_admin(connection, user["id"]) else " AND creator_id=?"),
                                 (event_id,) if is_admin(connection, user["id"]) else (event_id, user["id"])).fetchone()
    if not row:
        abort(404, description="签到不存在、已过期或无权访问。")
    return dict(row)


def event_options(connection, event_id):
    row = connection.execute("SELECT calendar_uid,custom_json FROM checkin_options WHERE event_id=?",
                             (event_id,)).fetchone()
    return {"calendar_uid": row[0], "custom_fields": json.loads(row[1])} if row else {
        "calendar_uid": "", "custom_fields": []}


def page_options(connection, event):
    row = connection.execute("SELECT slug,description,visibility FROM checkin_pages WHERE event_id=?",
                             (event["id"],)).fetchone()
    return {"detail_path": f"/checkin/events/{event['creator_id']}/{quote(row[0], safe='')}",
            "description": row[1], "visibility": row[2]}


def receipt(connection, event, user):
    # A signed browser receipt or the same logged-in account may retrieve only its timestamp.
    receipt_id = session.get("checkin_receipts", {}).get(event["id"], "")
    row = connection.execute(
        "SELECT id,checked_at FROM checkin_entries WHERE event_id=? AND "
        "(id=? OR (user_id IS NOT NULL AND user_id=?)) LIMIT 1",
        (event["id"], receipt_id, user["id"] if user else None)).fetchone()
    return {"id": row[0], "checked_at": row[1]} if row else None


def access_rows(connection, user_id=None, page=1):
    connection.row_factory = sqlite3.Row
    condition = " WHERE a.applicant_id=?" if user_id is not None else ""
    args = (user_id,) if user_id is not None else ()
    rows = connection.execute(
        "SELECT a.*,r.real_name AS applicant,g.expires_at,g.revoked_at,g.allow_export,g.mailed_at,"
        "EXISTS(SELECT 1 FROM system_mail_outbox o WHERE o.grant_id=a.id AND o.kind='checkin.access.issued' "
        "AND o.attempts>=8) AS mail_failed "
        "FROM checkin_access_requests a JOIN registrations r ON r.id=a.applicant_id "
        "LEFT JOIN checkin_access_grants g ON g.request_id=a.id" + condition +
        " ORDER BY CASE a.status WHEN 'pending' THEN 0 ELSE 1 END,a.id DESC LIMIT 50 OFFSET ?",
        (*args, (page - 1) * 50)).fetchall()
    return [dict(row) for row in rows]
