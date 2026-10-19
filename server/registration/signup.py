"""Email ownership challenges consumed atomically with account creation."""
import hashlib
import hmac
import secrets
import time

from flask import Blueprint, current_app, jsonify, request, session

from .auth import current_account
from .repository import RegistrationAlreadyExists, RegistrationLimitReached
from .validation import InvalidRegistration, validate_registration
from .verification import json_body

signup = Blueprint("signup", __name__)
LIFETIME = 600


def code_digest(challenge, email, code):
    key = current_app.secret_key
    if isinstance(key, str):
        key = key.encode()
    return hmac.new(key, f"{challenge}:{email}:{code}".encode(), hashlib.sha256).hexdigest()


@signup.post("/signup/code")
def send_code():
    payload = json_body()
    try:
        email = validate_registration({
            "username": "email_check", "email": (payload or {}).get("email"),
        }).email
    except InvalidRegistration as error:
        return jsonify(error=str(error)), error.status
    if current_account():
        return jsonify(error="请先退出当前账户。"), 409
    store = current_app.extensions["registration_store"]
    if not store.allow_auth_attempt(request.remote_addr, email, action="signup-code"):
        return jsonify(error="请求过于频繁，请 15 分钟后重试。"), 429
    challenge = session.get("signup_challenge") or secrets.token_urlsafe(32)
    digest = store.token_hash(challenge)
    now = int(time.time())
    with store.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM signup_mail_outbox WHERE challenge_hash IN "
                           "(SELECT challenge_hash FROM signup_challenges WHERE expires_at<=?)", (now,))
        connection.execute("DELETE FROM signup_challenges WHERE expires_at<=?", (now,))
        # The cooldown applies across sessions, including multiple browsers.
        if connection.execute(
            "SELECT 1 FROM signup_challenges WHERE (email=? OR challenge_hash=?) AND created_at>?",
            (email, digest, now - 60),
        ).fetchone():
            return jsonify(error="请等待 60 秒后再发送验证码。", retry_after=60), 429
        code = f"{secrets.randbelow(1000000):06d}"
        connection.execute(
            "INSERT INTO signup_challenges VALUES (?, ?, ?, ?, ?, 0) "
            "ON CONFLICT(challenge_hash) DO UPDATE SET email=excluded.email, "
            "code_hash=excluded.code_hash, created_at=excluded.created_at, "
            "expires_at=excluded.expires_at, attempts=0",
            (digest, email, code_digest(challenge, email, code), now, now + LIFETIME),
        )
        connection.execute(
            "INSERT INTO signup_mail_outbox VALUES (?, ?, ?, ?, ?, 0) "
            "ON CONFLICT(challenge_hash) DO UPDATE SET recipient=excluded.recipient, "
            "code=excluded.code, created_at=excluded.created_at, "
            "next_attempt=excluded.next_attempt, attempts=0",
            (digest, email, code, now, now),
        )
    session["signup_challenge"] = challenge
    return jsonify(message="验证码已申请发送，请检查收件箱和垃圾邮件。10 分钟内有效。",
                   retry_after=60)


def create_verified_account(store, registration, password_hash, code):
    challenge = session.get("signup_challenge", "")
    digest = store.token_hash(challenge)
    valid = False
    now = int(time.time())
    with store.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT email, code_hash, expires_at, attempts FROM signup_challenges "
            "WHERE challenge_hash=?", (digest,),
        ).fetchone()
        if row and row[2] > now and row[3] < 5:
            # Commit failed guesses too; raising inside the transaction would undo them.
            connection.execute("UPDATE signup_challenges SET attempts=attempts+1 "
                               "WHERE challenge_hash=?", (digest,))
            valid = row[0] == registration.email and hmac.compare_digest(
                row[1], code_digest(challenge, registration.email, code),
            )
        if valid:
            if connection.execute("SELECT 1 FROM registrations WHERE username=? OR email=?",
                                  (registration.username, registration.email)).fetchone():
                raise RegistrationAlreadyExists()
            if connection.execute("SELECT count(*) FROM registrations").fetchone()[0] >= store.max_records:
                raise RegistrationLimitReached()
            connection.execute(
                "INSERT INTO registrations (username,email,password_hash,email_verified_at,real_name,nickname) "
                "VALUES (?,?,?,?,?,?)",
                (registration.username, registration.email, password_hash, now,
                 registration.real_name, registration.nickname),
            )
            from .sites import initialize_profile
            initialize_profile(connection, connection.execute("SELECT last_insert_rowid()").fetchone()[0],
                               registration.username)
            connection.execute("DELETE FROM signup_challenges WHERE challenge_hash=?", (digest,))
            connection.execute("DELETE FROM signup_mail_outbox WHERE challenge_hash=?", (digest,))
    if valid:
        session.pop("signup_challenge", None)
    return valid
