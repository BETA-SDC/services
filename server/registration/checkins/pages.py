"""Attendance page, introduction, session and approved static-asset endpoints."""
import sqlite3

from flask import abort, jsonify, request

from ..auth import csrf_token
from ..access_control.policy import is_admin
from ..privacy.identity import actor
from ..privacy.consent import audit
from ..private_validation import DOCUMENTS
from ..routes import asset
from .domain import FIELDS, POLICY
from .http import access, checkin, payload
from .notifications import notify
from .permissions import account, administrator, can_edit_page, member
from .presentation import scoped_event, visible_page
from .repository import find_event, store
from .validation import validate_page_content


@checkin.get("/checkin")
@checkin.get("/checkin/requests")
@checkin.get("/checkin/access-requests")
@checkin.get("/checkin/join/<token>")
def checkin_page(token=None):
    csrf_token()
    if request.path in ("/checkin/requests", "/checkin/access-requests"):
        with store().connection() as connection:
            administrator(connection)
    if token:
        with store().connection() as connection:
            find_event(connection, token=token)
    return asset("checkin.html")


@checkin.get("/checkin/events/<int:creator_id>/<slug>")
def event_page(creator_id, slug):
    csrf_token()
    with store().connection() as connection:
        visible_page(connection, creator_id, slug)
    return asset("checkin.html")


@checkin.get("/api/checkin/pages/<int:creator_id>/<slug>")
def event_page_data(creator_id, slug):
    with store().connection() as connection:
        return jsonify(visible_page(connection, creator_id, slug))


@checkin.put("/api/checkin/events/<event_id>/visibility")
def update_event_page(event_id):
    user = actor()
    if not user:
        abort(401, description="请先登录账户中心。")
    data = validate_page_content(payload())
    with store().connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM checkin_events WHERE id=?", (event_id,)).fetchone()
        if not row or not can_edit_page(connection, dict(row), user):
            abort(404, description="签到不存在或无权修改。")
        connection.execute("UPDATE checkin_pages SET description=?,visibility=? WHERE event_id=?",
                           (data["description"], data["visibility"], event_id))
        creator = connection.execute("SELECT id,email FROM registrations WHERE id=?", (row["creator_id"],)).fetchone()
        if creator:
            notify(connection, creator, "checkin.page.updated", "BETA · 签到介绍设置已更新",
                   f"「{row['title']}」的活动介绍或查看权限已更新，当前权限：{data['visibility']}。\n"
                   "此更改不公开签到者资料。若非本人操作，请联系管理员。\n")
        audit(connection, user["id"], "checkin.page.update", [event_id, data["visibility"]])
    return jsonify(ok=True)


@checkin.get("/checkin.js")
def checkin_script():
    return asset("checkin.js")


FRONTEND_MODULES = frozenset((
    "approvals", "attendance", "bootstrap", "context", "deletion", "events",
    "factor", "pages", "records", "sensitive", "ui",
))


@checkin.get("/checkin-assets/<name>.js")
def checkin_module(name):
    if name not in FRONTEND_MODULES:
        abort(404)
    return asset(f"checkin/{name}.js")


@checkin.get("/checkin.css")
def checkin_styles():
    return asset("checkin.css")


@checkin.get("/api/checkin/session")
def checkin_session():
    user = actor()
    with store().connection() as connection:
        manager = bool(user and is_admin(connection, user["id"]))
        allowed = manager or member(connection, user)
    return jsonify(user={"name": user["nickname"] or user["real_name"] or user["username"]} if user else None,
                   can_create=allowed, can_manage=manager, fields=FIELDS, documents=DOCUMENTS,
                   csrf_token=csrf_token(), policy=POLICY)


@access.get("/checkin/records/<event_id>")
def records_page(event_id):
    with store().connection() as connection:
        scoped_event(connection, event_id, account(connection))
    csrf_token()
    return asset("checkin.html")
