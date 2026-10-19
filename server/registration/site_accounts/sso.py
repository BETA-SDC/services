"""Account service site_accounts: sso."""
import secrets
import time
from urllib.parse import urlencode
from flask import current_app, jsonify, redirect, request, session
from ..auth import LIFETIME, csrf_token, current_account
from .domain import ACCOUNT_HOST, SITE_HOSTS, safe_next, sites


@sites.get("/sso/start")
def sso_start():
    import ipaddress
    if request.host not in SITE_HOSTS:
        return jsonify(error="站点无效。"), 400
    if not any(ipaddress.ip_address(request.remote_addr) in ipaddress.ip_network(network)
               for network in current_app.config["SSO_NETWORKS"]):
        return jsonify(error="请连接校园网后登录账户中心。"), 403
    target = safe_next(request.args.get("next", "/"))
    if target is None:
        return jsonify(error="返回地址无效。"), 400
    store = current_app.extensions["registration_store"]
    if not store.allow_auth_attempt(request.remote_addr, request.remote_addr, action="sso-start", limits=(180, 120)):
        return jsonify(error="站点切换过于频繁，请稍后再试。"), 429
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    session["sso_nonce"] = nonce
    with store.connection() as connection:
        connection.execute("DELETE FROM sso_flows WHERE expires_at<=?", (int(time.time()),))
        connection.execute(
            "INSERT INTO sso_flows(state_hash,nonce_hash,target_host,next_path,required,expires_at) VALUES(?,?,?,?,?,?)",
            (store.token_hash(state), store.token_hash(nonce), request.host, target,
             int(request.args.get("login") == "1"), int(time.time()) + 600))
    return redirect("https://" + ACCOUNT_HOST + "/sso/authorize?" + urlencode({"state": state}), 303)



@sites.get("/sso/authorize")
def sso_authorize():
    if request.host != ACCOUNT_HOST:
        return jsonify(error="请使用账户中心。"), 403
    state = request.args.get("state", "")
    if not 32 <= len(state) <= 128:
        return jsonify(error="验证会话无效。"), 400
    store, now = current_app.extensions["registration_store"], int(time.time())
    user, ticket = current_account(), secrets.token_urlsafe(32)
    with store.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        flow = connection.execute("SELECT target_host,required FROM sso_flows WHERE state_hash=? AND expires_at>?",
                                  (store.token_hash(state), now)).fetchone()
        if not flow or flow[0] not in SITE_HOSTS:
            return jsonify(error="验证会话已失效。"), 409
        if not user and flow[1]:
            session["pending_sso"] = state
            return redirect("/login", 303)
        user_id = store.find_account(user["username"])["id"] if user else None
        parent = store.token_hash(session["auth_token"]) if user else ""
        connection.execute("UPDATE sso_flows SET ticket_hash=?,parent_hash=?,user_id=?,auth_time=?,expires_at=? "
                           "WHERE state_hash=?", (store.token_hash(ticket), parent, user_id,
                                                 session.get("auth_time", 0), now + 60, store.token_hash(state)))
    session.pop("pending_sso", None)
    return redirect(f"https://{flow[0]}/sso/callback?" + urlencode({"state": state, "ticket": ticket}), 303)



@sites.get("/sso/callback")
def sso_callback():
    store, now = current_app.extensions["registration_store"], int(time.time())
    state, ticket, nonce = request.args.get("state", ""), request.args.get("ticket", ""), session.get("sso_nonce", "")
    if request.host not in SITE_HOSTS or not nonce or not all(32 <= len(v) <= 128 for v in (state, ticket)):
        return jsonify(error="验证会话无效，请重新登录。"), 409
    with store.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT user_id,parent_hash,next_path,auth_time FROM sso_flows WHERE state_hash=? "
            "AND ticket_hash=? AND nonce_hash=? AND target_host=? AND expires_at>?",
            (store.token_hash(state), store.token_hash(ticket), store.token_hash(nonce), request.host, now)).fetchone()
        if not row:
            return jsonify(error="验证已过期或已使用。"), 409
        if row[0] is not None:
            parent = connection.execute("SELECT s.expires_at FROM auth_sessions s LEFT JOIN site_sessions h USING(token_hash) "
                                        "WHERE s.token_hash=? AND s.user_id=? "
                                        "AND COALESCE(h.site_host,'account.betasdc.duckdns.org')=? "
                                        "AND COALESCE(h.parent_hash,'')='' AND s.expires_at>?",
                                        (row[1], row[0], ACCOUNT_HOST, now)).fetchone()
            if not parent:
                return jsonify(error="账户登录已失效。"), 409
            token = secrets.token_urlsafe(32)
            connection.execute("DELETE FROM auth_sessions WHERE token_hash=? AND token_hash IN "
                               "(SELECT token_hash FROM site_sessions WHERE parent_hash<>'')",
                               (store.token_hash(session.get("auth_token", "")),))
            connection.execute("INSERT INTO auth_sessions VALUES(?,?,?)",
                               (store.token_hash(token), row[0], min(parent[0], now + LIFETIME)))
            connection.execute("INSERT INTO site_sessions VALUES(?,?,?)", (store.token_hash(token), request.host, row[1]))
        connection.execute("DELETE FROM sso_flows WHERE state_hash=?", (store.token_hash(state),))
    session.clear()
    session["sso_checked"] = now
    if row[0] is not None:
        session["auth_token"], session["auth_time"] = token, row[3]
        session.permanent = True
    csrf_token()
    return redirect(row[2], 303)

