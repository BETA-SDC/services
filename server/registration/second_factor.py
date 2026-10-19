"""Fresh second-factor proof bound to an existing, revocable login session."""
import hashlib
import hmac
import secrets
import time

from flask import Blueprint, current_app, jsonify, request

from .private_data import actor, active_session, audit, session_hash

second_factor = Blueprint("second_factor", __name__)
LIFETIME = 300


def initialize_second_factor(connection):
    for statement in (
        """CREATE TABLE IF NOT EXISTS second_factor_proofs(
            session_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL,method TEXT NOT NULL,
            credential_id TEXT NOT NULL,verified_at INTEGER NOT NULL,expires_at INTEGER NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS second_factor_challenges(
            challenge_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL,session_hash TEXT NOT NULL,
            email TEXT NOT NULL,code_hash TEXT NOT NULL,created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,attempts INTEGER NOT NULL DEFAULT 0)""",
        """CREATE TABLE IF NOT EXISTS second_factor_outbox(
            challenge_hash TEXT PRIMARY KEY,recipient TEXT NOT NULL,code TEXT NOT NULL,
            created_at INTEGER NOT NULL,next_attempt INTEGER NOT NULL,attempts INTEGER NOT NULL DEFAULT 0)""",
    ):
        connection.execute(statement)


def code_digest(challenge, user_id, code):
    key = current_app.secret_key
    if isinstance(key, str):
        key = key.encode()
    return hmac.new(key, f"2fa:{challenge}:{user_id}:{code}".encode(), hashlib.sha256).hexdigest()


def proof(connection, user):
    if not active_session(connection, user):
        return None
    row = connection.execute(
        "SELECT method,credential_id,verified_at,expires_at FROM second_factor_proofs "
        "WHERE session_hash=? AND user_id=? AND expires_at>? AND verified_at<=?",
        (session_hash(), user["id"], int(time.time()), int(time.time())),
    ).fetchone()
    if not row:
        return None
    if row[0] == "passkey" and not connection.execute(
            "SELECT 1 FROM passkeys WHERE credential_id=? AND user_id=?", (row[1], user["id"])).fetchone():
        return None
    if row[0] not in ("passkey", "email"):
        return None
    return {"method": row[0], "verified_at": row[2], "expires_at": row[3]}


def mark_verified(connection, user, method, credential_id=""):
    now = int(time.time())
    connection.execute("DELETE FROM second_factor_proofs WHERE expires_at<=?", (now,))
    connection.execute(
        "INSERT INTO second_factor_proofs VALUES(?,?,?,?,?,?) ON CONFLICT(session_hash) DO UPDATE SET "
        "user_id=excluded.user_id,method=excluded.method,credential_id=excluded.credential_id,"
        "verified_at=excluded.verified_at,expires_at=excluded.expires_at",
        (session_hash(), user["id"], method, credential_id, now, now + LIFETIME),
    )
    audit(connection, user["id"], "2fa." + method, [])


@second_factor.get("/security")
def status():
    user = actor()
    if not user:
        return jsonify(error="请先登录。"), 401
    with current_app.extensions["registration_store"].connection() as connection:
        count = connection.execute("SELECT count(*) FROM passkeys WHERE user_id=?", (user["id"],)).fetchone()[0]
        verified = proof(connection, user)
    return jsonify(email=user["email"], email_verified=bool(user["email_verified_at"]),
                   passkey_count=count, verification=verified, private_2fa_required=True)


@second_factor.post("/security/email/send")
def send():
    user = actor()
    if not user:
        return jsonify(error="请先登录。"), 401
    store = current_app.extensions["registration_store"]
    if not store.allow_auth_attempt(request.remote_addr, user["username"], action="second-factor-send"):
        return jsonify(error="请求过于频繁，请稍后重试。"), 429
    challenge, now = secrets.token_urlsafe(32), int(time.time())
    code = f"{secrets.randbelow(1000000):06d}"
    hashed = store.token_hash(challenge)
    with store.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if not active_session(connection, user):
            return jsonify(error="登录已失效。"), 401
        if not user["email_verified_at"]:
            return jsonify(error="需要已验证的绑定邮箱。"), 409
        connection.execute("DELETE FROM second_factor_outbox WHERE challenge_hash IN "
                           "(SELECT challenge_hash FROM second_factor_challenges WHERE expires_at<=?)", (now,))
        connection.execute("DELETE FROM second_factor_challenges WHERE expires_at<=?", (now,))
        if connection.execute("SELECT 1 FROM second_factor_challenges WHERE user_id=? AND created_at>?",
                              (user["id"], now - 60)).fetchone():
            return jsonify(error="请等待 60 秒后重新发送。"), 429
        # Resending rotates this browser's challenge; other browsers retain separate proofs.
        connection.execute("DELETE FROM second_factor_outbox WHERE challenge_hash IN "
                           "(SELECT challenge_hash FROM second_factor_challenges WHERE session_hash=?)", (session_hash(),))
        connection.execute("DELETE FROM second_factor_challenges WHERE session_hash=?", (session_hash(),))
        connection.execute("INSERT INTO second_factor_challenges VALUES(?,?,?,?,?,?,?,0)",
                           (hashed, user["id"], session_hash(), user["email"],
                            code_digest(challenge, user["id"], code), now, now + LIFETIME))
        connection.execute("INSERT INTO second_factor_outbox VALUES(?,?,?,?,?,0)",
                           (hashed, user["email"], code, now, now))
    return jsonify(challenge=challenge, expires_in=LIFETIME, retry_after=60,
                   message="验证码已申请发送至绑定邮箱，5 分钟内有效。")


@second_factor.post("/security/email/verify")
def verify():
    user, payload = actor(), request.get_json()
    if not user:
        return jsonify(error="请先登录。"), 401
    challenge = payload.get("challenge") if isinstance(payload, dict) else None
    code = payload.get("code") if isinstance(payload, dict) else None
    if (not isinstance(challenge, str) or not 32 <= len(challenge) <= 128
            or not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit()):
        return jsonify(error="请填写 6 位邮箱验证码。"), 422
    store = current_app.extensions["registration_store"]
    if not store.allow_auth_attempt(request.remote_addr, user["username"], action="second-factor-check"):
        return jsonify(error="尝试次数过多，请稍后重试。"), 429
    with store.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if not active_session(connection, user):
            return jsonify(error="登录已失效。"), 401
        hashed = store.token_hash(challenge)
        row = connection.execute(
            "SELECT code_hash FROM second_factor_challenges WHERE challenge_hash=? AND user_id=? "
            "AND session_hash=? AND email=? AND expires_at>? AND attempts<5",
            (hashed, user["id"], session_hash(), user["email"], int(time.time())),
        ).fetchone()
        if not row:
            return jsonify(error="验证码无效、已过期或尝试次数过多，请重新发送。"), 422
        connection.execute("UPDATE second_factor_challenges SET attempts=attempts+1 WHERE challenge_hash=?", (hashed,))
        if not hmac.compare_digest(row[0], code_digest(challenge, user["id"], code)):
            return jsonify(error="验证码不正确。"), 422
        connection.execute("DELETE FROM second_factor_challenges WHERE challenge_hash=?", (hashed,))
        connection.execute("DELETE FROM second_factor_outbox WHERE challenge_hash=?", (hashed,))
        mark_verified(connection, user, "email")
    return jsonify(ok=True, method="email", expires_in=LIFETIME)
