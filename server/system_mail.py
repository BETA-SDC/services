"""Encrypted transactional notifications, delivered by the isolated mail worker."""
import base64
import json
import secrets
import time


def initialize_system_mail(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS system_mail_outbox(
        id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,recipient TEXT NOT NULL,kind TEXT NOT NULL,
        ciphertext TEXT NOT NULL,created_at INTEGER NOT NULL,next_attempt INTEGER NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,expires_at INTEGER NOT NULL,
        grant_id INTEGER,grant_hash TEXT)""")


def queue_message(connection, cipher, user, kind, subject, body, *, expires_at=None,
                  not_before=None, grant_id=None, grant_hash=None):
    job_id, now = secrets.token_hex(16), int(time.time())
    nonce = secrets.token_bytes(12)
    aad = f"betasdc-system-mail:v1:{job_id}:{user['id']}:{user['email']}".encode()
    value = json.dumps({"subject": subject, "body": body}, ensure_ascii=False).encode()
    ciphertext = base64.urlsafe_b64encode(nonce + cipher.encrypt(nonce, value, aad)).decode()
    connection.execute("INSERT INTO system_mail_outbox VALUES(?,?,?,?,?,?,?,0,?,?,?)",
                       (job_id, user["id"], user["email"], kind, ciphertext, now,
                        now if not_before is None else not_before,
                        now + 7 * 86400 if expires_at is None else expires_at, grant_id, grant_hash))
    return job_id


def read_message(cipher, job):
    raw = base64.urlsafe_b64decode(job["ciphertext"])
    aad = f"betasdc-system-mail:v1:{job['id']}:{job['user_id']}:{job['recipient']}".encode()
    return json.loads(cipher.decrypt(raw[:12], raw[12:], aad))
