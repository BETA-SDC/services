"""Encrypted, self-only optional data with session-bound single-use consent."""
import base64
import fcntl
import json
import os
from pathlib import Path
import secrets
import stat
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.security import check_password_hash

from .auth import current_account
from .private_validation import DOCUMENTS, FIELDS, validate_private
from .validation import InvalidRegistration

private = Blueprint("private", __name__)
POLICY = "2026-10-16"


def initialize_private(connection):
    statements = """
        CREATE TABLE IF NOT EXISTS private_fields(
            user_id INTEGER NOT NULL, field TEXT NOT NULL, ciphertext TEXT NOT NULL,
            updated_at INTEGER NOT NULL, PRIMARY KEY(user_id,field));
        CREATE TABLE IF NOT EXISTS private_consents(
            token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, session_hash TEXT NOT NULL,
            purpose TEXT NOT NULL, fields_json TEXT NOT NULL, policy TEXT NOT NULL,
            created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, consumed_at INTEGER);
        CREATE TABLE IF NOT EXISTS private_audit(
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, action TEXT NOT NULL,
            fields_json TEXT NOT NULL, policy TEXT NOT NULL, created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS passkeys(
            credential_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
            credential_data TEXT NOT NULL, sign_count INTEGER NOT NULL,
            created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS passkey_flows(
            token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, session_hash TEXT NOT NULL,
            kind TEXT NOT NULL, state_json TEXT NOT NULL, context_json TEXT NOT NULL,
            expires_at INTEGER NOT NULL);
    """
    for statement in statements.split(";"):
        if statement.strip():
            connection.execute(statement)


def configure_private(app, store):
    path = Path(app.config.get("PRIVATE_DATA_KEY") or store.path.with_suffix(".private-key"))
    # Missing keys must never silently replace a key that protects existing data.
    with store.connection() as connection:
        exists = connection.execute("SELECT 1 FROM private_fields LIMIT 1").fetchone()
        exists = exists or connection.execute("SELECT 1 FROM checkin_entries LIMIT 1").fetchone()
        exists = exists or connection.execute("SELECT 1 FROM checkin_events LIMIT 1").fetchone()
        exists = exists or connection.execute("SELECT 1 FROM system_mail_outbox LIMIT 1").fetchone()
    if exists and not path.exists():
        raise RuntimeError("Private data encryption key is missing; restore it before starting")
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "r+b") as keyfile:
        fcntl.flock(keyfile.fileno(), fcntl.LOCK_EX)
        info = os.fstat(keyfile.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise PermissionError("Private encryption key must be private and service-owned")
        key = keyfile.read()
        if not key:
            if exists:
                raise RuntimeError("Private data encryption key is empty")
            key = secrets.token_bytes(32)
            keyfile.write(key)
            keyfile.flush()
            os.fsync(keyfile.fileno())
        if len(key) != 32:
            raise ValueError("Invalid private encryption key")
    app.extensions["private_cipher"] = AESGCM(key)


def actor():
    user = current_account()
    if not user:
        return None
    return current_app.extensions["registration_store"].find_account(user["username"])


def password_valid(user, payload):
    password = payload.get("password") if isinstance(payload, dict) else None
    store = current_app.extensions["registration_store"]
    if not store.allow_auth_attempt(request.remote_addr, user["username"], action="private-verify"):
        return False
    return (isinstance(password, str) and 1 <= len(password) <= 128
            and check_password_hash(user["password_hash"], password))


def session_hash():
    return current_app.extensions["registration_store"].token_hash(session.get("auth_token", ""))


def active_session(connection, user):
    return connection.execute(
        "SELECT 1 FROM auth_sessions WHERE token_hash=? AND user_id=? AND expires_at>?",
        (session_hash(), user["id"], int(time.time())),
    ).fetchone()


def consent_context(payload):
    if not isinstance(payload, dict) or payload.get("consent") is not True or payload.get("policy") != POLICY:
        raise InvalidRegistration("请阅读隐私保护政策，并单独同意本次操作。")
    purpose, fields = payload.get("purpose"), payload.get("fields")
    if purpose not in ("read", "edit", "delete") or not isinstance(fields, list) or not fields:
        raise InvalidRegistration("请明确本次操作目的和资料范围。")
    if any(field not in FIELDS for field in fields) or len(fields) != len(set(fields)):
        raise InvalidRegistration("资料范围无效。")
    return {"purpose": purpose, "fields": sorted(fields)}


def audit(connection, user_id, action, fields):
    now = int(time.time())
    connection.execute("DELETE FROM private_audit WHERE created_at<?", (now - 180 * 86400,))
    connection.execute("INSERT INTO private_audit(user_id,action,fields_json,policy,created_at) VALUES(?,?,?,?,?)",
                       (user_id, action, json.dumps(sorted(fields)), POLICY, now))


def issue_consent(connection, user, context):
    store = current_app.extensions["registration_store"]
    now = int(time.time())
    connection.execute("DELETE FROM private_consents WHERE expires_at<?", (now - 86400,))
    token = secrets.token_urlsafe(32)
    connection.execute("INSERT INTO private_consents VALUES(?,?,?,?,?,?,?,?,NULL)",
                       (store.token_hash(token), user["id"], session_hash(), context["purpose"],
                        json.dumps(context["fields"]), POLICY, now, now + 60))
    audit(connection, user["id"], "consent." + context["purpose"], context["fields"])
    return token


def consume(connection, user, token, purpose):
    from .second_factor import proof
    if not isinstance(token, str) or len(token) > 128 or not active_session(connection, user):
        return None
    if not proof(connection, user):
        return None
    hashed = current_app.extensions["registration_store"].token_hash(token)
    row = connection.execute(
        "SELECT fields_json FROM private_consents WHERE token_hash=? AND user_id=? "
        "AND session_hash=? AND purpose=? AND policy=? AND expires_at>? AND consumed_at IS NULL",
        (hashed, user["id"], session_hash(), purpose, POLICY, int(time.time())),
    ).fetchone()
    if not row:
        return None
    connection.execute("UPDATE private_consents SET consumed_at=? WHERE token_hash=?",
                       (int(time.time()), hashed))
    return json.loads(row[0])


def encrypt(user_id, field, value):
    nonce = secrets.token_bytes(12)
    aad = f"betasdc-private:v1:{user_id}:{field}".encode()
    encrypted = current_app.extensions["private_cipher"].encrypt(
        nonce, json.dumps(value, ensure_ascii=False).encode(), aad)
    return base64.urlsafe_b64encode(nonce + encrypted).decode()


def decrypt(user_id, field, value):
    raw = base64.urlsafe_b64decode(value)
    result = json.loads(current_app.extensions["private_cipher"].decrypt(
        raw[:12], raw[12:], f"betasdc-private:v1:{user_id}:{field}".encode()))
    if field == "phone" and isinstance(result, str) and result.startswith("+86"):
        from re import fullmatch
        if fullmatch(r"\+861[3-9][0-9]{9}", result):
            return result[3:]
    return result


@private.get("/private")
def private_status():
    user = actor()
    if not user:
        return jsonify(error="请先登录。"), 401
    store = current_app.extensions["registration_store"]
    with store.connection() as connection:
        fields = {row[0]: {"saved": True, "updated_at": row[1]} for row in connection.execute(
            "SELECT field,updated_at FROM private_fields WHERE user_id=?", (user["id"],))}
        keys = connection.execute("SELECT count(*) FROM passkeys WHERE user_id=?", (user["id"],)).fetchone()[0]
    return jsonify(fields=fields, passkey_count=keys, policy=POLICY, documents=DOCUMENTS)


@private.post("/private/authorize")
def authorize():
    user = actor()
    if not user:
        return jsonify(error="请先登录。"), 401
    payload = request.get_json()
    try:
        context = consent_context(payload)
    except InvalidRegistration as error:
        return jsonify(error=str(error)), 422
    from .second_factor import proof
    with current_app.extensions["registration_store"].connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if not active_session(connection, user):
            return jsonify(error="登录已失效。"), 401
        if not proof(connection, user):
            return jsonify(error="请先通过通行密钥或邮箱验证码完成二次验证。",
                           code="second_factor_required"), 401
        token = issue_consent(connection, user, context)
    return jsonify(token=token, expires_in=60)


@private.post("/private/<operation>")
def use_private(operation):
    if operation not in ("read", "edit", "delete"):
        return jsonify(error="操作不存在。"), 404
    user = actor()
    if not user:
        return jsonify(error="请先登录。"), 401
    payload = request.get_json()
    if not isinstance(payload, dict):
        return jsonify(error="参数无效。"), 422
    with current_app.extensions["registration_store"].connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        fields = consume(connection, user, payload.get("token"), operation)
        if not fields:
            return jsonify(error="本次授权已使用、已过期或不属于当前会话，请重新同意。"), 409
        result = {}
        if operation == "edit":
            try:
                values = validate_private(payload.get("values"))
                if set(values) != set(fields):
                    raise InvalidRegistration("提交的资料与本次同意的范围不一致。")
            except InvalidRegistration as error:
                return jsonify(error=str(error)), 422
            for field, value in values.items():
                if value is None:
                    connection.execute("DELETE FROM private_fields WHERE user_id=? AND field=?", (user["id"], field))
                else:
                    connection.execute(
                        "INSERT INTO private_fields VALUES(?,?,?,?) ON CONFLICT(user_id,field) "
                        "DO UPDATE SET ciphertext=excluded.ciphertext,updated_at=excluded.updated_at",
                        (user["id"], field, encrypt(user["id"], field, value), int(time.time())))
            result = values
        if operation == "read":
            for field, ciphertext in connection.execute(
                    "SELECT field,ciphertext FROM private_fields WHERE user_id=?", (user["id"],)):
                if field in fields:
                    result[field] = decrypt(user["id"], field, ciphertext)
        if operation == "delete":
            for field in fields:
                connection.execute("DELETE FROM private_fields WHERE user_id=? AND field=?", (user["id"], field))
        # Mutations invalidate any outstanding grants, including grants from other sessions.
        if operation != "read":
            connection.execute("UPDATE private_consents SET consumed_at=? WHERE user_id=? AND consumed_at IS NULL",
                               (int(time.time()), user["id"]))
            connection.execute("UPDATE checkin_grants SET consumed_at=? WHERE user_id=? AND consumed_at IS NULL",
                               (int(time.time()), user["id"]))
        audit(connection, user["id"], operation, fields)
    return jsonify(ok=True, values=result)
