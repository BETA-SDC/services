"""Session-bound OAuth binding; remote exchange is delegated over a Unix socket."""
import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import time
from urllib.parse import urlencode

from flask import Blueprint, current_app, jsonify, redirect, request, session

from .auth import current_account, establish_session
from server.github_profile import public_profile
from .permissions import audit, sync_memberships

github = Blueprint("github", __name__)
# Keep the registered OAuth callback as a compatibility bridge to the new host.
CALLBACK = "https://register.betasdc.duckdns.org/api/github/callback"
BROKER_SOCKET = "/run/betasdc-github/broker.sock"


def configure_github(app):
    if "GITHUB_CLIENT_ID" not in app.config:
        directory = os.environ.get("CREDENTIALS_DIRECTORY")
        path = Path(directory) / "github-client-id" if directory else None
        app.config["GITHUB_CLIENT_ID"] = os.environ.get("BETASDC_GITHUB_CLIENT_ID", "") or (
            path.read_text().strip() if path and path.exists() else ""
        )
    if app.config["GITHUB_CLIENT_ID"] and not re.fullmatch(r"[A-Za-z0-9_.-]{10,100}",
                                                         app.config["GITHUB_CLIENT_ID"]):
        raise ValueError("Invalid GitHub client ID")


def github_status():
    account = current_account()
    linked = None
    if account:
        store = current_app.extensions["registration_store"]
        with store.connection() as connection:
            row = connection.execute(
                "SELECT g.github_id,g.login,g.profile_json,g.synced_at FROM github_accounts g "
                "JOIN registrations r ON r.id=g.user_id "
                "WHERE r.username=?", (account["username"],),
            ).fetchone()
            if row:
                try:
                    details = json.loads(row[2])
                    if not isinstance(details, dict):
                        details = {}
                except (TypeError, ValueError):
                    details = {}
                stored = public_profile({**details, "id": row[0], "login": row[1]})
                linked = {key: stored[key] for key in ("id", "login", "name", "organizations_complete")}
                linked["organizations"] = [
                    {"id": org["id"], "login": org["login"],
                     "html_url": "https://github.com/" + org["login"]}
                    for org in stored["organizations"]
                ]
                linked.update(
                    avatar_url=f"https://avatars.githubusercontent.com/u/{row[0]}?s=160",
                    html_url="https://github.com/" + row[1], synced_at=row[3],
                )
    return {"github_enabled": bool(current_app.config["GITHUB_CLIENT_ID"]), "github": linked}


def recent_account():
    account = current_account()
    if not account:
        return None, (jsonify(error="请先登录。"), 401)
    if int(time.time()) - session.get("auth_time", 0) > 600:
        return None, (jsonify(error="为保护账户，请退出并重新登录后再操作。"), 403)
    return current_app.extensions["registration_store"].find_account(account["username"]), None


@github.post("/github/link")
def link():
    if not current_app.config["GITHUB_CLIENT_ID"]:
        return jsonify(error="GitHub 绑定暂不可用。"), 503
    account, error = recent_account()
    if error:
        return error
    store = current_app.extensions["registration_store"]
    if not store.allow_auth_attempt(request.remote_addr, str(account["id"]), action="github-link"):
        return jsonify(error="请求过于频繁，请稍后重试。"), 429
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    now = int(time.time())
    with store.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM github_flows WHERE expires_at<=? OR user_id=?",
                           (now, account["id"]))
        connection.execute("INSERT INTO github_flows VALUES (?,?,?,?,?)", (
            store.token_hash(state), account["id"], store.token_hash(session["auth_token"]),
            verifier, now + 600,
        ))
    url = "https://github.com/login/oauth/authorize?" + urlencode({
        "client_id": current_app.config["GITHUB_CLIENT_ID"], "redirect_uri": CALLBACK,
        "state": state, "scope": "read:org", "code_challenge": challenge,
        "code_challenge_method": "S256", "prompt": "select_account",
    })
    return jsonify(url=url)


class UnixConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(BROKER_SOCKET)


def broker_request(path, payload):
    connection = UnixConnection("localhost", timeout=28)
    try:
        connection.request("POST", path, json.dumps(payload),
                           {"Content-Type": "application/json"})
        response = connection.getresponse()
        data = response.read(524289)
        if response.status != 200 or len(data) > 524288:
            raise ValueError("GitHub exchange unavailable")
        return public_profile(json.loads(data))
    finally:
        connection.close()


def exchange_identity(code, verifier):
    return broker_request("/exchange", {"code": code, "verifier": verifier})


def fetch_public_identity(github_id, login):
    identity = broker_request("/profile", {"id": github_id, "login": login})
    if identity["id"] != github_id:
        raise ValueError("GitHub identity changed")
    return identity


@github.get("/github/callback")
def callback():
    # Always remove the authorization code from the address bar via a clean redirect.
    def result(value):
        return redirect("/account#github=" + value, 303)

    account = current_account()
    state = request.args.get("state", "")
    if not account or not 32 <= len(state) <= 128:
        return result("invalid")
    store = current_app.extensions["registration_store"]
    user = store.find_account(account["username"])
    now = int(time.time())
    with store.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT verifier FROM github_flows WHERE state_hash=? AND user_id=? "
            "AND session_hash=? AND expires_at>?",
            (store.token_hash(state), user["id"], store.token_hash(session["auth_token"]), now),
        ).fetchone()
        if not row:
            return result("invalid")
        connection.execute("DELETE FROM github_flows WHERE state_hash=?", (store.token_hash(state),))
    if request.args.get("error"):
        return result("cancelled")
    code = request.args.get("code", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,256}", code):
        return result("invalid")
    try:
        identity = public_profile(exchange_identity(code, row[0]))
    except (OSError, ValueError, http.client.HTTPException):
        return result("unavailable")
    try:
        with store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            # Logout or expiry during the exchange must prevent binding.
            if not connection.execute(
                "SELECT 1 FROM auth_sessions WHERE token_hash=? AND expires_at>?",
                (store.token_hash(session["auth_token"]), int(time.time())),
            ).fetchone():
                return result("invalid")
            previous = connection.execute("SELECT github_id FROM github_accounts WHERE user_id=?",
                                          (user["id"],)).fetchone()
            if previous and previous[0] != identity["id"]:
                return result("already")
            connection.execute(
                "INSERT INTO github_accounts (user_id,github_id,login,profile_json,synced_at) "
                "VALUES (?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                "login=excluded.login,profile_json=excluded.profile_json,synced_at=excluded.synced_at",
                (user["id"], identity["id"], identity["login"], json.dumps(identity), int(time.time())),
            )
            sync_memberships(connection, user["id"], identity)
    except sqlite3.IntegrityError:
        return result("conflict")
    return result("linked")


@github.post("/github/unlink")
def unlink():
    account, error = recent_account()
    if error:
        return error
    store = current_app.extensions["registration_store"]
    with store.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM github_accounts WHERE user_id=?", (account["id"],))
        connection.execute("DELETE FROM github_flows WHERE user_id=?", (account["id"],))
        connection.execute("DELETE FROM group_members WHERE user_id=? AND source='github'", (account["id"],))
        connection.execute("DELETE FROM organization_sync WHERE user_id=?", (account["id"],))
        removed = connection.execute("DELETE FROM github_admin_grants WHERE user_id=?", (account["id"],))
        connection.execute("DELETE FROM github_owner_verifications WHERE user_id=?", (account["id"],))
        if removed.rowcount:
            audit(connection, account["id"], "role.github_owner.revoke", account["id"])
    return jsonify(csrf_token=establish_session(account["id"]), **github_status())


@github.post("/github/refresh")
def refresh():
    current = current_account()
    if not current:
        return jsonify(error="请先登录。"), 401
    store = current_app.extensions["registration_store"]
    account = store.find_account(current["username"])
    if not store.allow_auth_attempt(request.remote_addr, str(account["id"]), action="github-refresh"):
        return jsonify(error="更新过于频繁，请稍后重试。"), 429
    with store.connection() as connection:
        row = connection.execute("SELECT github_id,login FROM github_accounts WHERE user_id=?",
                                 (account["id"],)).fetchone()
    if not row:
        return jsonify(error="尚未绑定 GitHub。"), 409
    try:
        identity = public_profile(fetch_public_identity(row[0], row[1]))
        if identity["id"] != row[0]:
            raise ValueError("GitHub identity changed")
    except (OSError, ValueError, http.client.HTTPException):
        return jsonify(error="暂时无法获取 GitHub 资料，请稍后重试。"), 502
    with store.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if not connection.execute("SELECT 1 FROM auth_sessions WHERE token_hash=? AND expires_at>?",
                                  (store.token_hash(session["auth_token"]), int(time.time()))).fetchone():
            return jsonify(error="登录已失效，请重新登录。"), 401
        cursor = connection.execute(
            "UPDATE github_accounts SET login=?,profile_json=?,synced_at=? WHERE user_id=? AND github_id=?",
            (identity["login"], json.dumps(identity), int(time.time()), account["id"], row[0]),
        )
        if cursor.rowcount != 1:
            return jsonify(error="绑定状态已改变，请刷新页面。"), 409
        sync_memberships(connection, account["id"], identity)
    return jsonify(**github_status())
