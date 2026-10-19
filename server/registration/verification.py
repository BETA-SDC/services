"""One-use email verification and durable delivery requests."""
import secrets
import time

from flask import Blueprint, current_app, jsonify, request

from .repository import RegistrationStore

verification = Blueprint("verification", __name__)
TOKEN_LIFETIME = 30 * 60
RESEND_INTERVAL = 60
GENERIC_MESSAGE = "如果该邮箱有待验证的账户，验证邮件将发送至该邮箱。请检查收件箱和垃圾邮件。"


def enqueue_verification(connection, user_id, email):
    now = int(time.time())
    previous = connection.execute(
        "SELECT created_at FROM email_verifications WHERE user_id=?", (user_id,),
    ).fetchone()
    if previous and now - previous[0] < RESEND_INTERVAL:
        return False
    token = secrets.token_urlsafe(32)
    connection.execute(
        "INSERT INTO email_verifications VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET token_hash=excluded.token_hash, "
        "created_at=excluded.created_at, expires_at=excluded.expires_at",
        (user_id, RegistrationStore.token_hash(token), now, now + TOKEN_LIFETIME),
    )
    connection.execute(
        "INSERT INTO mail_outbox (user_id, recipient, token, created_at, next_attempt, attempts) "
        "VALUES (?, ?, ?, ?, ?, 0) ON CONFLICT(user_id) DO UPDATE SET "
        "recipient=excluded.recipient, token=excluded.token, created_at=excluded.created_at, "
        "next_attempt=excluded.next_attempt, attempts=0",
        (user_id, email, token, now, now),
    )
    return True


def json_body():
    if request.mimetype != "application/json":
        return None
    try:
        payload = request.get_json()
    except RecursionError:
        return None
    return payload if isinstance(payload, dict) else None


@verification.post("/verification/resend")
def resend():
    payload = json_body()
    email = payload.get("email") if payload else None
    if not isinstance(email, str) or not 1 <= len(email.strip()) <= 254:
        return jsonify(error="请填写完整邮箱地址。"), 400
    email = email.strip().lower()
    store = current_app.extensions["registration_store"]
    if not store.allow_auth_attempt(request.remote_addr, email, action="verification-resend"):
        return jsonify(error="请求过于频繁，请 15 分钟后重试。"), 429
    with store.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        account = connection.execute(
            "SELECT id, password_hash, email_verified_at FROM registrations "
            "WHERE email=? COLLATE NOCASE", (email,),
        ).fetchone()
        if account and account[1] and account[2] is None:
            enqueue_verification(connection, account[0], email)
    return jsonify(message=GENERIC_MESSAGE)


@verification.post("/verification/confirm")
def confirm():
    payload = json_body()
    token = payload.get("token") if payload else None
    if not isinstance(token, str) or not 32 <= len(token) <= 128:
        return jsonify(error="验证链接无效或已过期，请重新发送验证邮件。"), 400
    store = current_app.extensions["registration_store"]
    if not store.allow_auth_attempt(request.remote_addr, "confirm", action="verification-confirm"):
        return jsonify(error="请求过于频繁，请 15 分钟后重试。"), 429
    now = int(time.time())
    with store.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT user_id FROM email_verifications WHERE token_hash=? AND expires_at>?",
            (store.token_hash(token), now),
        ).fetchone()
        if not row:
            return jsonify(error="验证链接无效或已过期，请重新发送验证邮件。"), 400
        connection.execute(
            "UPDATE registrations SET email_verified_at=? WHERE id=?", (now, row[0]),
        )
        connection.execute("DELETE FROM email_verifications WHERE user_id=?", (row[0],))
        connection.execute("DELETE FROM mail_outbox WHERE user_id=?", (row[0],))
        connection.execute("DELETE FROM auth_sessions WHERE user_id=?", (row[0],))
    return jsonify(message="邮箱验证成功。现在可以登录了。")
