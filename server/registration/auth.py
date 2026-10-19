"""Password hashing and revocable sessions using Flask/Werkzeug primitives."""
from datetime import timedelta
import fcntl
import os
from pathlib import Path
import secrets
import stat
import time

from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

auth = Blueprint("auth", __name__)
HASH_METHOD = "pbkdf2:sha256:600000"
LIFETIME = 12 * 60 * 60


def configure_auth(app, database):
    if not app.config.get("SECRET_KEY"):
        path = Path(database).with_suffix(".session-key")
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "r+") as keyfile:
            fcntl.flock(keyfile.fileno(), fcntl.LOCK_EX)
            info = os.fstat(keyfile.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077):
                raise PermissionError("Session key must be a private file owned by the service")
            key = keyfile.read()
            if not key:
                key = secrets.token_hex(32)
                keyfile.write(key)
                keyfile.flush()
                os.fsync(keyfile.fileno())
            if len(key) != 64 or any(character not in "0123456789abcdef" for character in key):
                raise ValueError("Invalid session signing key")
            app.config["SECRET_KEY"] = key
    app.config.update(
        SESSION_COOKIE_NAME="__Host-betasdc",
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_PATH="/",
        SESSION_COOKIE_DOMAIN=None,
        PERMANENT_SESSION_LIFETIME=timedelta(seconds=LIFETIME),
        SESSION_REFRESH_EACH_REQUEST=False,
    )
    app.extensions["dummy_password_hash"] = generate_password_hash(
        secrets.token_urlsafe(32), method=HASH_METHOD,
    )


def current_account():
    return current_app.extensions["registration_store"].session_account(session.get("auth_token"))


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def establish_session(user_id):
    store = current_app.extensions["registration_store"]
    store.revoke_session(session.get("auth_token"))
    pending = session.get("pending_sso")
    session.clear()
    if pending:
        session["pending_sso"] = pending
    session["auth_token"] = store.create_session(user_id, LIFETIME)
    session["auth_time"] = int(time.time())
    session.permanent = True
    return csrf_token()


@auth.get("/session")
def session_status():
    from .github import github_status
    from .access_control.policy import access_status
    account = current_account()
    return jsonify(user=account, csrf_token=csrf_token(), **github_status(account=account),
                   access=access_status(current=account))


@auth.post("/login")
def login():
    if request.mimetype != "application/json":
        return jsonify(error="请求格式无效。"), 415
    try:
        payload = request.get_json()
    except RecursionError:
        return jsonify(error="请求格式无效。"), 400
    identifier = payload.get("identifier") if isinstance(payload, dict) else None
    password = payload.get("password") if isinstance(payload, dict) else None
    if (not isinstance(identifier, str) or not 1 <= len(identifier.strip()) <= 254
            or not isinstance(password, str) or not 1 <= len(password) <= 128):
        return jsonify(error="请填写用户名或邮箱及密码。"), 400
    store = current_app.extensions["registration_store"]
    identifier = identifier.strip()
    if not store.allow_auth_attempt(request.remote_addr, identifier):
        return jsonify(error="尝试次数过多，请 15 分钟后重试。"), 429
    account = store.find_account(identifier)
    password_hash = (account or {}).get("password_hash") or current_app.extensions["dummy_password_hash"]
    valid = check_password_hash(password_hash, password)
    if not account or not account["password_hash"] or not valid:
        return jsonify(error="用户名、邮箱或密码不正确。"), 401
    if account["email_verified_at"] is None:
        return jsonify(error="请先验证邮箱，再登录。", code="email_verification_required",
                       email=account["email"]), 409
    token = establish_session(account["id"])
    return jsonify(user=current_account(), csrf_token=token)


@auth.post("/logout")
def logout():
    current_app.extensions["registration_store"].revoke_session(session.get("auth_token"))
    session.clear()
    return jsonify(user=None, csrf_token=csrf_token())
