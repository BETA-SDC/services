"""Account service webauthn: enrollment."""
import json
import secrets
import sqlite3
import time
from fido2.utils import websafe_encode
from fido2.webauthn import AuthenticationResponse, RegistrationResponse, PublicKeyCredentialUserEntity, ResidentKeyRequirement, UserVerificationRequirement
from flask import current_app, jsonify, request, session
from ..privacy.identity import actor, active_session, password_valid, session_hash
from ..privacy.consent import audit, consent_context, issue_consent
from ..validation import InvalidRegistration
from .domain import passkeys
from .protocol import credentials, server, user_handle


@passkeys.post("/passkeys/<kind>/options")
def options(kind):
    if kind not in ("register", "verify", "stepup"):
        return jsonify(error="操作不存在。"), 404
    user, payload = actor(), request.get_json()
    if not user:
        return jsonify(error="请先登录。"), 401
    if not isinstance(payload, dict):
        return jsonify(error="参数无效。"), 422
    context = {}
    if kind == "register":
        if payload.get("consent") is not True:
            return jsonify(error="请同意保存通行密钥公钥。"), 422
        if not password_valid(user, payload):
            return jsonify(error="密码不正确或尝试次数过多。"), 401
    elif kind == "verify":
        try:
            context = consent_context(payload)
        except InvalidRegistration as error:
            return jsonify(error=str(error)), 422
    elif payload.get("consent") is not True:
        return jsonify(error="请确认本次二次验证。"), 422
    if not current_app.extensions["registration_store"].allow_auth_attempt(
            request.remote_addr, user["username"], action="passkey"):
        return jsonify(error="尝试次数过多，请稍后重试。"), 429
    store = current_app.extensions["registration_store"]
    with store.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if not active_session(connection, user):
            return jsonify(error="登录已失效。"), 401
        known = credentials(connection, user)
        if kind == "register":
            from ..second_factor import proof
            if not proof(connection, user):
                return jsonify(error="添加通行密钥前，请先完成二次验证。",
                               code="second_factor_required"), 401
            if len(known) >= 5:
                return jsonify(error="最多保存 5 个通行密钥，请先移除旧密钥。"), 409
            handle = user_handle(user["id"])
            result, state = server().register_begin(
                PublicKeyCredentialUserEntity(id=handle, name=user["username"],
                                              display_name=user["nickname"] or "BETA account"),
                known, resident_key_requirement=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.REQUIRED)
        else:
            if not known:
                return jsonify(error="请先添加通行密钥，或使用邮箱验证码完成二次验证。"), 409
            result, state = server().authenticate_begin(known, user_verification=UserVerificationRequirement.REQUIRED)
        now, flow = int(time.time()), secrets.token_urlsafe(32)
        connection.execute("DELETE FROM passkey_flows WHERE expires_at<=? OR session_hash=?", (now, session_hash()))
        connection.execute("INSERT INTO passkey_flows VALUES(?,?,?,?,?,?,?)",
                           (store.token_hash(flow), user["id"], session_hash(), kind,
                            json.dumps(state), json.dumps(context), now + 180))
    return jsonify(flow=flow, options=dict(result))



@passkeys.post("/passkeys/<kind>/complete")
def complete(kind):
    if kind not in ("register", "verify", "stepup"):
        return jsonify(error="操作不存在。"), 404
    user, payload = actor(), request.get_json()
    if not user:
        return jsonify(error="请先登录。"), 401
    if not isinstance(payload, dict) or not isinstance(payload.get("flow"), str) or len(payload["flow"]) > 128:
        return jsonify(error="参数无效。"), 422
    store = current_app.extensions["registration_store"]
    with store.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if not active_session(connection, user):
            return jsonify(error="登录已失效。"), 401
        hashed = store.token_hash(payload["flow"])
        row = connection.execute(
            "SELECT state_json,context_json FROM passkey_flows WHERE token_hash=? AND user_id=? "
            "AND session_hash=? AND kind=? AND expires_at>?",
            (hashed, user["id"], session_hash(), kind, int(time.time())),
        ).fetchone()
        if not row:
            return jsonify(error="通行密钥验证已失效，请重试。"), 409
        connection.execute("DELETE FROM passkey_flows WHERE token_hash=?", (hashed,))
        try:
            state, context = json.loads(row[0]), json.loads(row[1])
            if kind == "register":
                from ..second_factor import proof
                if not proof(connection, user):
                    raise ValueError("Second factor expired")
                response = RegistrationResponse.from_dict(payload.get("credential"))
                if response.response.client_data.cross_origin:
                    raise ValueError("Cross-origin credential")
                auth_data = server().register_complete(state, response)
                data = auth_data.credential_data
                if connection.execute("SELECT count(*) FROM passkeys WHERE user_id=?",
                                      (user["id"],)).fetchone()[0] >= 5:
                    raise ValueError("Too many credentials")
                connection.execute("INSERT INTO passkeys VALUES(?,?,?,?,?)",
                                   (websafe_encode(data.credential_id), user["id"], websafe_encode(data),
                                    auth_data.counter, int(time.time())))
                audit(connection, user["id"], "passkey.register", [])
                return jsonify(ok=True)
            response = AuthenticationResponse.from_dict(payload.get("credential"))
            if response.response.client_data.cross_origin:
                raise ValueError("Cross-origin credential")
            data = server().authenticate_complete(state, credentials(connection, user), response)
            counter = response.response.authenticator_data.counter
            old = connection.execute("SELECT sign_count FROM passkeys WHERE credential_id=?",
                                     (websafe_encode(data.credential_id),)).fetchone()[0]
            if (old or counter) and counter <= old:
                raise ValueError("Authenticator counter did not increase")
            connection.execute("UPDATE passkeys SET sign_count=? WHERE credential_id=?",
                               (counter, websafe_encode(data.credential_id)))
            from ..second_factor import mark_verified
            mark_verified(connection, user, "passkey", websafe_encode(data.credential_id))
            if kind == "verify":
                token = issue_consent(connection, user, context)
                return jsonify(token=token, expires_in=60)
            return jsonify(ok=True, method="passkey", expires_in=300)
        except (ValueError, TypeError, KeyError, AttributeError, sqlite3.IntegrityError):
            return jsonify(error="通行密钥验证失败，请重试或使用邮箱验证码。"), 422



@passkeys.post("/passkeys/remove")
def remove():
    user, payload = actor(), request.get_json()
    if not user:
        return jsonify(error="请先登录。"), 401
    if not isinstance(payload, dict) or payload.get("consent") is not True or not password_valid(user, payload):
        return jsonify(error="请确认移除全部通行密钥并验证密码。"), 401
    with current_app.extensions["registration_store"].connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if not active_session(connection, user):
            return jsonify(error="登录已失效。"), 401
        from ..second_factor import proof
        if not proof(connection, user):
            return jsonify(error="移除通行密钥前，请完成二次验证。",
                           code="second_factor_required"), 401
        connection.execute("DELETE FROM passkeys WHERE user_id=?", (user["id"],))
        connection.execute("DELETE FROM passkey_flows WHERE user_id=?", (user["id"],))
        connection.execute("DELETE FROM private_consents WHERE user_id=?", (user["id"],))
        connection.execute("DELETE FROM second_factor_proofs WHERE user_id=?", (user["id"],))
        audit(connection, user["id"], "passkey.remove", [])
    session.pop("private_verified_at", None)
    return jsonify(ok=True)

