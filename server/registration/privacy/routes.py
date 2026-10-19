"""Account service privacy: routes."""
import time
from flask import Blueprint, current_app, jsonify, request
from ..private_validation import DOCUMENTS, validate_private
from ..validation import InvalidRegistration
from .domain import POLICY
from .crypto import decrypt, encrypt
from .identity import active_session, actor
from .consent import audit, consent_context, consume, issue_consent


private = Blueprint("private", __name__)



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
    from ..second_factor import proof
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

