"""Account service webauthn: login."""
import json
import secrets
import time
from fido2.utils import websafe_decode, websafe_encode
from fido2.webauthn import AttestedCredentialData, AuthenticationResponse, UserVerificationRequirement
from flask import current_app, jsonify, request, session
from ..auth import current_account, establish_session
from ..privacy.consent import audit
from .domain import passkeys
from .protocol import server, user_handle


@passkeys.post("/passkeys/login/options")
def login_options():
    if current_account():
        return jsonify(error="当前已经登录。"), 409
    store = current_app.extensions["registration_store"]
    if not store.allow_auth_attempt(request.remote_addr, request.remote_addr, action="passkey-login"):
        return jsonify(error="尝试次数过多，请稍后重试。"), 429
    nonce = session.get("passkey_login_nonce") or secrets.token_urlsafe(32)
    session["passkey_login_nonce"] = nonce
    result, state = server().authenticate_begin(user_verification=UserVerificationRequirement.REQUIRED)
    now, flow = int(time.time()), secrets.token_urlsafe(32)
    with store.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM passkey_flows WHERE expires_at<=? OR session_hash=?",
                           (now, store.token_hash(nonce)))
        connection.execute("INSERT INTO passkey_flows VALUES(?,?,?,?,?,?,?)",
                           (store.token_hash(flow), 0, store.token_hash(nonce), "login",
                            json.dumps(state), "{}", now + 180))
    return jsonify(flow=flow, options=dict(result))



@passkeys.post("/passkeys/login/complete")
def login_complete():
    if current_account():
        return jsonify(error="当前已经登录。"), 409
    payload = request.get_json()
    nonce = session.get("passkey_login_nonce")
    if (not isinstance(payload, dict) or not isinstance(payload.get("flow"), str)
            or len(payload["flow"]) > 128 or not nonce):
        return jsonify(error="通行密钥登录已失效，请重试。"), 409
    store = current_app.extensions["registration_store"]
    with store.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        hashed = store.token_hash(payload["flow"])
        row = connection.execute(
            "SELECT state_json FROM passkey_flows WHERE token_hash=? AND user_id=0 "
            "AND session_hash=? AND kind='login' AND expires_at>?",
            (hashed, store.token_hash(nonce), int(time.time())),
        ).fetchone()
        if not row:
            return jsonify(error="通行密钥登录已失效，请重试。"), 409
        connection.execute("DELETE FROM passkey_flows WHERE token_hash=?", (hashed,))
        try:
            response = AuthenticationResponse.from_dict(payload.get("credential"))
            if response.response.client_data.cross_origin:
                raise ValueError("Cross-origin credential")
            saved = connection.execute(
                "SELECT p.user_id,p.credential_data,p.sign_count FROM passkeys p "
                "JOIN registrations r ON r.id=p.user_id WHERE p.credential_id=? "
                "AND r.email_verified_at IS NOT NULL",
                (websafe_encode(response.raw_id),),
            ).fetchone()
            if not saved or response.response.user_handle != user_handle(saved[0]):
                raise ValueError("Credential not available for this account")
            data = AttestedCredentialData(websafe_decode(saved[1]))
            server().authenticate_complete(json.loads(row[0]), [data], response)
            counter = response.response.authenticator_data.counter
            if (saved[2] or counter) and counter <= saved[2]:
                raise ValueError("Authenticator counter did not increase")
            connection.execute("UPDATE passkeys SET sign_count=? WHERE credential_id=?",
                               (counter, websafe_encode(data.credential_id)))
            audit(connection, saved[0], "passkey.login", [])
            user_id = saved[0]
        except (ValueError, TypeError, KeyError, AttributeError):
            return jsonify(error="通行密钥登录失败，请重试或使用邮箱登录。"), 401
    csrf = establish_session(user_id)
    return jsonify(user=current_account(), csrf_token=csrf)

