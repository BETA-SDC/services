"""Access applications, administrator review and emailed PAT management."""
import sqlite3
import time

from flask import abort, jsonify, request

from ..access_control.policy import is_admin
from ..privacy.identity import actor
from ..privacy.consent import audit
from ..validation import InvalidRegistration
from .grants import issue_pat
from .http import access, page_number, payload
from .notifications import notify, notify_admins
from .permissions import account, administrator, require_factor
from .presentation import scoped_event
from .repository import access_rows, store
from .validation import text


@access.route("/api/checkin/events/<event_id>/access-requests", methods=("GET", "POST"))
def request_access(event_id):
    if request.method == "POST":
        applicant = actor()
        if not applicant:
            abort(401, description="请先登录账户中心。")
        if not store().allow_auth_attempt(request.remote_addr, str(applicant["id"]), action="checkin-access-request",
                                          limits=(120, 20)):
            abort(429, description="申请过于频繁，请稍后重试。")
    with store().connection() as connection:
        if request.method == "POST":
            connection.execute("BEGIN IMMEDIATE")
        user = account(connection)
        event, options = scoped_event(connection, event_id, user)
        if request.method == "GET":
            rows = connection.execute(
                "SELECT a.id,a.status,a.created_at,a.note,g.expires_at,g.revoked_at,g.mailed_at "
                "FROM checkin_access_requests a LEFT JOIN checkin_access_grants g ON g.request_id=a.id "
                "WHERE a.event_id=? AND a.applicant_id=? ORDER BY a.id DESC LIMIT 20",
                (event_id, user["id"])).fetchall()
            return jsonify(title=event["title"], intro_path=options["detail_path"], requests=[dict(r) for r in rows])
        data = payload()
        if set(data) != {"reason"}:
            raise InvalidRegistration("请填写查看用途。")
        reason = text(data["reason"], "查看用途", 300)
        if connection.execute("SELECT 1 FROM checkin_access_requests WHERE event_id=? AND applicant_id=? "
                              "AND status='pending'", (event_id, user["id"])).fetchone():
            abort(409, description="已有待审核申请，请等待管理员的系统邮件。")
        cursor = connection.execute(
            "INSERT INTO checkin_access_requests(event_id,event_title,applicant_id,reason,created_at) VALUES(?,?,?,?,?)",
            (event_id, event["title"], user["id"], reason, int(time.time())))
        link = "https://account.betasdc.duckdns.org/checkin/access-requests"
        notify_admins(connection, "checkin.access.requested", "BETA · 签到资料查看申请",
                      f"签到表：{event['title']}\n申请人：{user['real_name']}\n用途：{reason}\n\n请登录审批：{link}\n")
        notify(connection, user, "checkin.access.received", "BETA · 查看申请已提交",
               f"你对「{event['title']}」的查看申请已提交。审批结果和 PAT 将通过系统邮件送达。\n")
        audit(connection, user["id"], "checkin.access.request", [event_id])
        return jsonify(id=cursor.lastrowid, status="pending"), 201


@access.get("/api/checkin/access-requests")
def pending_access():
    page = page_number()
    with store().connection() as connection:
        administrator(connection)
        return jsonify(requests=access_rows(connection, page=page),
                       total=connection.execute("SELECT count(*) FROM checkin_access_requests").fetchone()[0],
                       page=page, page_size=50)


@access.post("/api/checkin/access-requests/<int:request_id>/review")
def review_access(request_id):
    data = payload()
    if (set(data) != {"decision", "note", "expires_at", "allow_export"}
            or data["decision"] not in ("approve", "reject") or type(data["allow_export"]) is not bool):
        raise InvalidRegistration("审核决定无效。")
    note = text(data["note"], "审核备注", 300, optional=data["decision"] == "approve")
    with store().connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        admin = administrator(connection)
        require_factor(connection, admin)
        connection.row_factory = sqlite3.Row
        item = connection.execute("SELECT * FROM checkin_access_requests WHERE id=?", (request_id,)).fetchone()
        if not item:
            abort(404, description="查看申请不存在。")
        if item["status"] != "pending":
            abort(409, description="此申请已经处理。")
        user_row = connection.execute("SELECT * FROM registrations WHERE id=?", (item["applicant_id"],)).fetchone()
        user = dict(user_row) if user_row else None
        if not user:
            abort(409, description="申请账号已不存在。")
        if data["decision"] == "approve":
            scoped_event(connection, item["event_id"], user)
            expiry, now = data["expires_at"], int(time.time())
            if type(expiry) is not int or not now + 60 <= expiry <= now + 7 * 86400:
                raise InvalidRegistration("PAT 到期时间应在 1 分钟至 7 天内。")
            if data["allow_export"] and not is_admin(connection, user["id"]):
                raise InvalidRegistration("导出仍仅限管理员，普通申请人只能在线查看。")
            connection.execute("UPDATE checkin_access_grants SET revoked_at=? WHERE event_id=? AND user_id=? "
                               "AND revoked_at IS NULL", (now, item["event_id"], user["id"]))
            issue_pat(connection, item, user, expiry, data["allow_export"])
            notify(connection, user, "checkin.access.expired", "BETA · 签到资料查看授权已到期",
                   f"「{item['event_title']}」的限时 PAT 已到期，不能继续查看；如仍需使用，请重新申请。\n",
                   not_before=expiry, expires_at=expiry + 7 * 86400, grant_id=item["id"])
        else:
            notify(connection, user, "checkin.access.rejected", "BETA · 查看申请未获批准",
                   f"你对「{item['event_title']}」的查看申请未获批准。\n管理员备注：{note}\n")
        state = "approved" if data["decision"] == "approve" else "rejected"
        connection.execute("UPDATE checkin_access_requests SET status=?,reviewer_id=?,reviewed_at=?,note=? WHERE id=?",
                           (state, admin["id"], int(time.time()), note, request_id))
        audit(connection, admin["id"], "checkin.access." + state, [item["event_id"]])
        return jsonify(ok=True, status=state)


@access.post("/api/checkin/access-requests/<int:request_id>/<action>")
def manage_pat(request_id, action):
    if action not in ("resend", "revoke"):
        abort(404)
    data = payload()
    if set(data) != {"note"}:
        raise InvalidRegistration("请填写操作备注。")
    note = text(data["note"], "操作备注", 300, optional=action == "resend")
    with store().connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        admin = administrator(connection)
        require_factor(connection, admin)
        connection.row_factory = sqlite3.Row
        item = connection.execute("SELECT * FROM checkin_access_requests WHERE id=? AND status='approved'",
                                 (request_id,)).fetchone()
        grant = connection.execute("SELECT * FROM checkin_access_grants WHERE request_id=?", (request_id,)).fetchone()
        if not item or not grant or grant["revoked_at"] is not None or grant["expires_at"] <= int(time.time()):
            abort(409, description="授权已失效，请重新申请。")
        row = connection.execute("SELECT * FROM registrations WHERE id=?", (item["applicant_id"],)).fetchone()
        if action == "resend" and not row:
            abort(409, description="申请账号已不存在，无法重发。")
        user = dict(row) if row else None
        if action == "resend":
            if user["email"] != grant["email"]:
                abort(409, description="绑定邮箱已变更，请重新申请。")
            scoped_event(connection, item["event_id"], user)
            issue_pat(connection, item, user, grant["expires_at"], bool(grant["allow_export"]))
        else:
            connection.execute("UPDATE checkin_access_grants SET revoked_at=? WHERE request_id=?",
                               (int(time.time()), request_id))
            connection.execute("DELETE FROM system_mail_outbox WHERE grant_id=?", (request_id,))
            if user:
                notify(connection, user, "checkin.access.revoked", "BETA · 查看授权已撤销",
                       f"「{item['event_title']}」的 PAT 已撤销。\n管理员备注：{note}\n")
        audit(connection, admin["id"], "checkin.access." + action, [item["event_id"]])
        return jsonify(ok=True)
