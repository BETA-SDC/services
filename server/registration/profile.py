"""Self-service display names, independent of immutable login identifiers."""
import time

from flask import Blueprint, current_app, jsonify, request, session

from .auth import current_account
from .validation import InvalidRegistration, validate_profile

profile = Blueprint("profile", __name__)


@profile.route("/profile/student-id", methods=["GET", "PUT"])
def student_id():
    from .private_data import actor, audit, decrypt, encrypt
    from .private_validation import validate_private
    user = actor()
    if not user:
        return jsonify(error="请先登录。"), 401
    if not user["email_verified_at"]:
        return jsonify(error="请先验证邮箱。"), 403
    store = current_app.extensions["registration_store"]
    value = None
    if request.method == "PUT":
        payload = request.get_json()
        if not isinstance(payload, dict) or set(payload) != {"student_id"}:
            return jsonify(error="参数无效。"), 422
        try:
            value = validate_private(payload)["student_id"]
        except InvalidRegistration as error:
            return jsonify(error=str(error)), 422
        if not store.allow_auth_attempt(request.remote_addr, user["username"], action="student-id"):
            return jsonify(error="修改过于频繁，请稍后重试。"), 429
    with store.connection() as connection:
        if request.method == "PUT":
            connection.execute("BEGIN IMMEDIATE")
            if value is None:
                connection.execute("DELETE FROM private_fields WHERE user_id=? AND field='student_id'", (user["id"],))
            else:
                connection.execute(
                    "INSERT INTO private_fields VALUES(?,?,?,?) ON CONFLICT(user_id,field) "
                    "DO UPDATE SET ciphertext=excluded.ciphertext,updated_at=excluded.updated_at",
                    (user["id"], "student_id", encrypt(user["id"], "student_id", value), int(time.time())))
            for table in ("private_consents", "checkin_grants"):
                connection.execute(f"UPDATE {table} SET consumed_at=? WHERE user_id=? AND consumed_at IS NULL",
                                   (int(time.time()), user["id"]))
            audit(connection, user["id"], "profile.student_id.edit", ["student_id"])
        else:
            row = connection.execute(
                "SELECT ciphertext FROM private_fields WHERE user_id=? AND field='student_id'", (user["id"],)).fetchone()
            value = decrypt(user["id"], "student_id", row[0]) if row else None
    return jsonify(student_id=value)


@profile.post("/profile")
def update_profile():
    account = current_account()
    if not account:
        return jsonify(error="请先登录。"), 401
    if int(time.time()) - session.get("auth_time", 0) > 600:
        return jsonify(error="请重新登录后修改账户资料。"), 403
    payload = request.get_json()
    if not isinstance(payload, dict):
        return jsonify(error="参数无效。"), 400
    try:
        name, nickname = validate_profile(payload.get("real_name"), payload.get("nickname", ""))
    except InvalidRegistration as error:
        return jsonify(error=str(error)), error.status
    store = current_app.extensions["registration_store"]
    if not store.allow_auth_attempt(request.remote_addr, account["username"], action="profile"):
        return jsonify(error="修改过于频繁，请稍后重试。"), 429
    with store.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        active = connection.execute(
            "SELECT s.user_id FROM auth_sessions s JOIN registrations r ON r.id=s.user_id "
            "WHERE s.token_hash=? AND s.expires_at>? AND r.username=? AND r.email_verified_at IS NOT NULL",
            (store.token_hash(session.get("auth_token", "")), int(time.time()), account["username"]),
        ).fetchone()
        if not active:
            return jsonify(error="登录已失效。"), 401
        connection.execute("UPDATE registrations SET real_name=?,nickname=? WHERE id=?",
                           (name, nickname, active[0]))
    return jsonify(user=current_account())
