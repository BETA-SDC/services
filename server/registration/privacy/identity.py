"""Account service privacy: identity."""
import time
from flask import current_app, request, session
from werkzeug.security import check_password_hash
from ..auth import current_account


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

