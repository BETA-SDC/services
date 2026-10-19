"""Account service privacy: crypto."""
import base64
import fcntl
import json
import os
from pathlib import Path
import secrets
import stat
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask import current_app


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

