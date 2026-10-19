"""Deletion requests, administrator review and transactional event removal."""
import sqlite3
import time

from flask import abort, jsonify

from ..privacy.consent import audit
from .grants import revoke_event_access
from .http import checkin, page_number, payload
from .notifications import notify, notify_admins
from .permissions import administrator, organizer, require_factor
from .repository import find_event, store
from .validation import text


def delete_event(connection, event, user):
    revoke_event_access(connection, event)
    for table in ("checkin_identities", "checkin_entries", "checkin_grants", "checkin_options", "checkin_pages"):
        connection.execute(f"DELETE FROM {table} WHERE event_id=?", (event["id"],))
    connection.execute("DELETE FROM checkin_events WHERE id=?", (event["id"],))
    connection.execute(
        "UPDATE checkin_deletion_requests SET status='approved',reviewer_id=?,reviewed_at=?,note=? "
        "WHERE event_id=? AND status='pending'",
        (user["id"], int(time.time()), "管理员已删除签到表。", event["id"]))
    creator = connection.execute("SELECT id,email FROM registrations WHERE id=?", (event["creator_id"],)).fetchone()
    if creator:
        notify(connection, creator, "checkin.deleted", "BETA · 签到表已删除",
               f"「{event['title']}」已由管理员删除，签到记录及查看授权已失效。\n")
    audit(connection, user["id"], "checkin.delete", [event["id"]])


@checkin.post("/api/checkin/events/<event_id>/deletion-request")
def request_deletion(event_id):
    data = payload()
    if set(data) != {"reason"}:
        abort(422, description="请填写删除原因。")
    reason = text(data["reason"], "删除原因", 300)
    with store().connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        event = find_event(connection, event_id=event_id)
        user = organizer(connection)
        if event["creator_id"] != user["id"]:
            abort(403, description="只能申请删除自己创建的签到表。")
        if connection.execute("SELECT 1 FROM checkin_deletion_requests WHERE event_id=? AND status='pending'",
                              (event_id,)).fetchone():
            abort(409, description="删除申请已提交，等待管理员审核。")
        cursor = connection.execute(
            "INSERT INTO checkin_deletion_requests(event_id,event_title,requester_id,reason,created_at) VALUES(?,?,?,?,?)",
            (event_id, event["title"], user["id"], reason, int(time.time())))
        notify_admins(connection, "checkin.deletion.requested", "BETA · 签到表删除申请",
                      f"签到表：{event['title']}\n申请人：{user['real_name']}\n原因：{reason}\n"
                      "请登录处理：https://account.betasdc.duckdns.org/checkin/requests\n")
        notify(connection, user, "checkin.deletion.received", "BETA · 删除申请已提交",
               f"你对「{event['title']}」的删除申请已提交，处理结果将通过系统邮件发送。\n")
        audit(connection, user["id"], "checkin.deletion.request", [event_id])
        return jsonify(id=cursor.lastrowid, status="pending"), 201


@checkin.get("/api/checkin/deletion-requests")
def deletion_requests():
    page = page_number()
    with store().connection() as connection:
        administrator(connection)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT d.*,COALESCE(NULLIF(r.nickname,''),r.username) AS requester FROM checkin_deletion_requests d "
            "LEFT JOIN registrations r ON r.id=d.requester_id "
            "ORDER BY CASE d.status WHEN 'pending' THEN 0 ELSE 1 END,d.id DESC LIMIT 50 OFFSET ?",
            ((page - 1) * 50,)).fetchall()
        total = connection.execute("SELECT count(*) FROM checkin_deletion_requests").fetchone()[0]
        return jsonify(requests=[dict(row) for row in rows], total=total, page=page, page_size=50)


@checkin.post("/api/checkin/deletion-requests/<int:request_id>/review")
def review_deletion(request_id):
    data = payload()
    if set(data) != {"decision", "note"} or data["decision"] not in ("approve", "reject"):
        abort(422, description="审核决定无效。")
    note = text(data["note"], "审核备注", 300, optional=data["decision"] == "approve")
    with store().connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        user = administrator(connection)
        require_factor(connection, user)
        connection.row_factory = sqlite3.Row
        item = connection.execute("SELECT * FROM checkin_deletion_requests WHERE id=?", (request_id,)).fetchone()
        if not item:
            abort(404, description="删除申请不存在。")
        if item["status"] != "pending":
            abort(409, description="此申请已经处理，请刷新。")
        state = "approved" if data["decision"] == "approve" else "rejected"
        connection.execute("UPDATE checkin_deletion_requests SET status=?,reviewer_id=?,reviewed_at=?,note=? WHERE id=?",
                           (state, user["id"], int(time.time()), note, request_id))
        if state == "approved":
            delete_event(connection, find_event(connection, event_id=item["event_id"]), user)
        applicant = connection.execute("SELECT id,email FROM registrations WHERE id=?", (item["requester_id"],)).fetchone()
        if applicant:
            notify(connection, applicant, "checkin.deletion." + state, "BETA · 删除申请处理结果",
                   f"「{item['event_title']}」的删除申请已{'批准' if state == 'approved' else '驳回'}。\n"
                   f"管理员备注：{note or '无'}\n")
        audit(connection, user["id"], "checkin.deletion." + state, [item["event_id"]])
        return jsonify(ok=True, status=state)
