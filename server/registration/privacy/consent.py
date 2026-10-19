"""Account service privacy: consent."""
import json
import secrets
import time
from flask import current_app
from ..private_validation import FIELDS
from ..validation import InvalidRegistration
from .domain import POLICY
from .identity import active_session, session_hash


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
    from ..second_factor import proof
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

