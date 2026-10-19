"""Account-bound PAT issuance, authorization, rotation and revocation."""
from datetime import datetime
import re
import secrets
import sqlite3
import time

from flask import abort, jsonify, request

from ..access_control.policy import is_admin
from .domain import PAT_PATTERN, ZONE
from .notifications import notify
from .permissions import account, require_factor
from .presentation import scoped_event
from .repository import store


def issue_pat(connection, item, user, expires_at, allow_export):
    token = "beta_checkin_" + secrets.token_urlsafe(32)
    hashed, now = store().token_hash(token), int(time.time())
    connection.execute("INSERT INTO checkin_access_grants VALUES(?,?,?,?,?,?,?,NULL,?,NULL) "
                       "ON CONFLICT(request_id) DO UPDATE SET token_hash=excluded.token_hash,"
                       "issued_at=excluded.issued_at,mailed_at=NULL",
                       (item["id"], item["event_id"], user["id"], user["email"], hashed, now, expires_at, int(allow_export)))
    connection.execute("DELETE FROM system_mail_outbox WHERE grant_id=? AND kind='checkin.access.issued'", (item["id"],))
    expiry = datetime.fromtimestamp(expires_at, ZONE).strftime("%Y-%m-%d %H:%M:%S")
    link = f"https://account.betasdc.duckdns.org/checkin/records/{item['event_id']}"
    notify(connection, user, "checkin.access.issued", "BETA · 查看申请获批及限时 PAT",
           f"你对「{item['event_title']}」的查看申请已获批准。\n有效至：{expiry}（北京时间）\n"
           f"范围：仅本签到表{'，含导出' if allow_export else '，仅在线查看'}。\n\nPAT：\n{token}\n\n"
           f"请登录申请账号并完成二次验证，在以下页面粘贴 PAT：\n{link}\n\n"
           "PAT 不能用于他人账号。不要转发，不要写进网址。重新发送会使旧 PAT 立即失效。\n"
           "仅按申请用途使用资料，不得公开、转交或另作他用。邮件不包含签到者资料。\n",
           expires_at=expires_at, grant_id=item["id"], grant_hash=hashed)
    return hashed


def authorized_records(connection, event_id, export):
    user = account(connection)
    if not store().allow_auth_attempt(request.remote_addr, str(user["id"]), action="checkin-pat", limits=(600, 120)):
        abort(429, description="验证过于频繁，请稍后重试。")
    token = request.headers.get("X-Checkin-PAT", "")
    if not re.fullmatch(PAT_PATTERN, token):
        response = jsonify(error="请申请并填写邮件中的限时 PAT。", code="checkin_pat_required")
        response.status_code = 403
        abort(response)
    connection.row_factory = sqlite3.Row
    grant = connection.execute(
        "SELECT g.*,a.reviewer_id FROM checkin_access_grants g JOIN checkin_access_requests a ON a.id=g.request_id "
        "WHERE g.token_hash=? AND g.event_id=? AND g.user_id=? AND g.email=? "
        "AND a.status='approved' AND g.revoked_at IS NULL AND g.expires_at>?",
        (store().token_hash(token), event_id, user["id"], user["email"], int(time.time()))).fetchone()
    if not grant or not is_admin(connection, grant["reviewer_id"]):
        abort(403, description="PAT 无效、已到期或已撤销。")
    event, _ = scoped_event(connection, event_id, user)
    if export and not (grant["allow_export"] and is_admin(connection, user["id"])):
        abort(403, description="本 PAT 不允许导出。")
    require_factor(connection, user)
    return user, event, grant


def revoke_event_access(connection, event):
    now = int(time.time())
    connection.execute("UPDATE checkin_access_grants SET revoked_at=? WHERE event_id=? AND revoked_at IS NULL",
                       (now, event["id"]))
    connection.execute("DELETE FROM system_mail_outbox WHERE grant_id IN "
                       "(SELECT request_id FROM checkin_access_grants WHERE event_id=?)", (event["id"],))
    connection.row_factory = sqlite3.Row
    users = connection.execute(
        "SELECT DISTINCT r.id,r.email FROM registrations r JOIN checkin_access_requests a ON a.applicant_id=r.id "
        "WHERE a.event_id=?", (event["id"],)).fetchall()
    for user in users:
        notify(connection, user, "checkin.access.deleted", "BETA · 签到表已删除",
               f"「{event['title']}」已由管理员删除，相关查看授权已失效。\n")
    connection.execute("UPDATE checkin_access_requests SET status='rejected',reviewed_at=?,note='签到表已删除。' "
                       "WHERE event_id=? AND status='pending'", (now, event["id"]))
