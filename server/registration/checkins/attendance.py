"""Consented attendance submission and session-bound single-use profile prefill."""
import hashlib
import hmac
import json
import secrets
import time

from flask import abort, current_app, jsonify, request, session

from ..private_data import actor, audit, decrypt, encrypt, session_hash
from .domain import POLICY, status
from .http import checkin, payload
from .presentation import public_event
from .repository import find_event, receipt, store
from .validation import validate_answers


@checkin.route("/api/checkin/join/<token>", methods=("GET", "POST"))
def join(token):
    if request.method == "POST" and not store().allow_auth_attempt(
            request.remote_addr, request.remote_addr, action="checkin-submit", limits=(600, 600)):
        abort(429, description="当前网络提交过于频繁，请稍后再试。")
    with store().connection() as connection:
        if request.method == "POST":
            connection.execute("BEGIN IMMEDIATE")
        event = find_event(connection, token=token)
        user = actor()
        previous = receipt(connection, event, user)
        if request.method == "GET":
            return jsonify(event=public_event(connection, event), receipt=previous)
        data = payload()
        if set(data) != {"values", "consent", "policy"} or data["consent"] is not True or data["policy"] != POLICY:
            abort(422, description="请确认本活动的信息用途及保存期限，并单独同意本次提交。")
        if previous:
            return jsonify(receipt=previous, already_checked=True)
        if status(event) != "open":
            abort(409, description="签到尚未开始、已暂停或已结束。")
        fields = json.loads(event["fields_json"])
        values = validate_answers(data["values"], fields)
        if connection.execute("SELECT count(*) FROM checkin_entries WHERE event_id=?", (event["id"],)).fetchone()[0] >= 10000:
            abort(409, description="本次签到人数已达上限。")
        identities = [(field, value.casefold()) for field, value in values.items()
                      if field in ("email", "phone", "student_id")]
        if "document" in values:
            doc = values["document"]
            identities.append(("document", f"{doc['type']}:{doc['country']}:{doc['number']}"))
        key = current_app.secret_key
        if isinstance(key, str):
            key = key.encode()
        identities = [(field, hmac.new(key, f"checkin:{event['id']}:{field}:{value}".encode(),
                                      hashlib.sha256).hexdigest()) for field, value in identities]
        for kind, digest in identities:
            if connection.execute("SELECT 1 FROM checkin_identities WHERE event_id=? AND kind=? AND digest=?",
                                  (event["id"], kind, digest)).fetchone():
                abort(409, description="提交信息与已有签到重复；如需更正，请联系活动发起人。")
        entry_id, checked_at = secrets.token_hex(16), int(time.time())
        connection.execute("INSERT INTO checkin_entries VALUES(?,?,?,?,?,?)",
                           (entry_id, event["id"], user["id"] if user else None,
                            encrypt(event["id"], "checkin:entry:" + entry_id, values), checked_at, POLICY))
        connection.executemany("INSERT INTO checkin_identities VALUES(?,?,?,?)",
                               [(event["id"], kind, digest, entry_id) for kind, digest in identities])
        receipts = session.get("checkin_receipts", {})
        session["checkin_receipts"] = dict(list(receipts.items())[-19:] + [(event["id"], entry_id)])
        return jsonify(receipt={"id": entry_id, "checked_at": checked_at}, already_checked=False), 201


@checkin.post("/api/checkin/join/<token>/prefill/authorize")
def authorize_prefill(token):
    user = actor()
    if not user:
        abort(401, description="请先登录，或手动填写。")
    data = payload()
    if set(data) != {"consent", "policy"} or data["consent"] is not True or data["policy"] != POLICY:
        abort(422, description="请单独同意仅为本次签到读取已保存资料。")
    from ..second_factor import proof
    with store().connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        event = find_event(connection, token=token)
        if status(event) != "open":
            abort(409, description="当前活动未开放签到。")
        fields = json.loads(event["fields_json"])
        private_fields = set(fields) & {"phone", "document"}
        if private_fields and not proof(connection, user):
            return jsonify(error="使用隐私资料需要二次验证。", code="second_factor_required"), 401
        grant = secrets.token_urlsafe(32)
        connection.execute("INSERT INTO checkin_grants VALUES(?,?,?,?,?,NULL)",
                           (store().token_hash(grant), event["id"], user["id"], session_hash(), int(time.time()) + 60))
        audit(connection, user["id"], "checkin.prefill.consent", fields)
    return jsonify(token=grant, expires_in=60)


@checkin.post("/api/checkin/join/<token>/prefill")
def prefill(token):
    user = actor()
    if not user:
        abort(401, description="请先登录，或手动填写。")
    data = payload()
    if set(data) != {"token"} or not isinstance(data["token"], str) or len(data["token"]) > 128:
        abort(422, description="本次资料授权无效。")
    from ..second_factor import proof
    with store().connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        event = find_event(connection, token=token)
        if status(event) != "open":
            abort(409, description="当前活动未开放签到。")
        fields = json.loads(event["fields_json"])
        private_fields = set(fields) & {"phone", "document"}
        if private_fields and not proof(connection, user):
            return jsonify(error="使用隐私资料需要二次验证。", code="second_factor_required"), 401
        grant = connection.execute(
            "SELECT 1 FROM checkin_grants WHERE token_hash=? AND event_id=? AND user_id=? "
            "AND session_hash=? AND expires_at>? AND consumed_at IS NULL",
            (store().token_hash(data["token"]), event["id"], user["id"], session_hash(), int(time.time()))).fetchone()
        if not grant:
            abort(409, description="本次授权已使用或已失效，请重新确认。")
        connection.execute("UPDATE checkin_grants SET consumed_at=? WHERE token_hash=?",
                           (int(time.time()), store().token_hash(data["token"])))
        values = {field: user["real_name"] if field == "name" else user["email"]
                  for field in fields if field in ("name", "email")}
        for field, ciphertext in connection.execute("SELECT field,ciphertext FROM private_fields WHERE user_id=?",
                                                    (user["id"],)):
            if field in fields and field in ("student_id", "phone", "document"):
                values[field] = decrypt(user["id"], field, ciphertext)
        audit(connection, user["id"], "checkin.prefill", fields)
    return jsonify(values=values)
