"""Shared site shell, public profiles and browser-bound, one-use SSO handoffs."""
import re
import secrets
import time
from urllib.parse import quote, urlencode, urlsplit

from flask import Blueprint, current_app, jsonify, redirect, request, session

from .auth import LIFETIME, csrf_token, current_account
from .routes import asset

sites = Blueprint("sites", __name__)
ACCOUNT_HOST = "account.betasdc.duckdns.org"
NAV_HOST = "nav.betasdc.duckdns.org"
PROFILE_HOST = "profile.betasdc.duckdns.org"
CALENDAR_HOST = "calendar.betasdc.duckdns.org"
SITE_HOSTS = (NAV_HOST, PROFILE_HOST, CALENDAR_HOST)
RESERVED = {"admin", "api", "sso", "login", "logout", "register", "account",
            "settings", "assets", "privacy", "profile", "navigation"}


def initialize_sites(connection):
    for statement in (
        """CREATE TABLE IF NOT EXISTS site_sessions(
            token_hash TEXT PRIMARY KEY,site_host TEXT NOT NULL,parent_hash TEXT NOT NULL DEFAULT '')""",
        """CREATE TABLE IF NOT EXISTS sso_flows(
            state_hash TEXT PRIMARY KEY,nonce_hash TEXT NOT NULL,target_host TEXT NOT NULL,
            next_path TEXT NOT NULL,required INTEGER NOT NULL,expires_at INTEGER NOT NULL,
            ticket_hash TEXT NOT NULL DEFAULT '',parent_hash TEXT NOT NULL DEFAULT '',
            user_id INTEGER,auth_time INTEGER NOT NULL DEFAULT 0)""",
        """CREATE TABLE IF NOT EXISTS public_profiles(
            user_id INTEGER PRIMARY KEY,slug TEXT UNIQUE COLLATE NOCASE NOT NULL,bio TEXT NOT NULL DEFAULT '')""",
        """CREATE TABLE IF NOT EXISTS profile_aliases(
            slug TEXT PRIMARY KEY COLLATE NOCASE,user_id INTEGER NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS profile_requests(
            id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,slug TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending',created_at INTEGER NOT NULL,reviewed_at INTEGER,reviewer_id INTEGER)""",
        """CREATE TABLE IF NOT EXISTS user_preferences(
            user_id INTEGER PRIMARY KEY,theme TEXT NOT NULL DEFAULT 'system')""",
    ):
        connection.execute(statement)
    for user_id, registration_number in connection.execute("SELECT id,username FROM registrations"):
        initialize_profile(connection, user_id, registration_number)


def initialize_profile(connection, user_id, registration_number):
    """Use the immutable system registration number as the default profile path."""
    connection.execute("INSERT INTO public_profiles(user_id,slug) VALUES(?,?) ON CONFLICT(user_id) DO NOTHING",
                       (user_id, registration_number))
    connection.execute("INSERT INTO profile_aliases(slug,user_id) VALUES(?,?) ON CONFLICT(slug) DO NOTHING",
                       (registration_number, user_id))


def own_profile(connection, user):
    row = connection.execute("SELECT slug,bio FROM public_profiles WHERE user_id=?", (user["id"],)).fetchone()
    if not row:
        initialize_profile(connection, user["id"], user["username"])
        row = (user["username"], "")
    latest = connection.execute("SELECT slug,state FROM profile_requests WHERE user_id=? ORDER BY id DESC LIMIT 1",
                                (user["id"],)).fetchone()
    return {"slug": row[0], "bio": row[1], "url": f"https://{PROFILE_HOST}/" + quote(row[0], safe=""),
            "request": {"slug": latest[0], "state": latest[1]} if latest else None}


def safe_next(value):
    if not isinstance(value, str) or len(value) > 1500 or "\\" in value or any(ord(c) < 32 for c in value):
        return None
    parts = urlsplit(value)
    if not value.startswith("/") or value.startswith("//") or parts.netloc or parts.scheme or parts.fragment:
        return None
    if parts.path.startswith(("/sso/", "/api/")):
        return None
    return value


@sites.get("/api/site/session")
def site_session():
    import ipaddress
    from .github import github_status
    user = current_account()
    profile = None
    avatar = "/default-avatar.png"
    if user:
        store = current_app.extensions["registration_store"]
        account = store.find_account(user["username"])
        with store.connection() as connection:
            profile = own_profile(connection, account)
        linked = github_status().get("github")
        if linked:
            avatar = linked.get("avatar_url") or avatar
    pending = session.get("pending_sso", "")
    continuation = None
    if user and request.host == ACCOUNT_HOST and pending:
        with current_app.extensions["registration_store"].connection() as connection:
            valid = connection.execute("SELECT 1 FROM sso_flows WHERE state_hash=? AND expires_at>?",
                                       (current_app.extensions["registration_store"].token_hash(pending),
                                        int(time.time()))).fetchone()
        if valid:
            continuation = "/sso/authorize?" + urlencode({"state": pending})
        else:
            session.pop("pending_sso", None)
    return jsonify(user={"name": user["nickname"] or user["real_name"] or user["username"],
                         "avatar": avatar, "profile": profile,
                         "preferences": _preferences(store, account["id"]) if user else None} if user else None,
                   csrf_token=session.get("csrf_token", ""), continue_url=continuation,
                   sso_available=any(ipaddress.ip_address(request.remote_addr) in ipaddress.ip_network(network)
                                     for network in current_app.config["SSO_NETWORKS"]),
                   sso_checked=int(time.time()) - session.get("sso_checked", 0) < 60)


def _preferences(store, user_id):
    with store.connection() as connection:
        row = connection.execute("SELECT theme FROM user_preferences WHERE user_id=?", (user_id,)).fetchone()
    return {"theme": row[0] if row and row[0] in ("system", "light", "dark") else "system"}


@sites.route("/api/preferences", methods=("GET", "PUT"))
def preferences():
    if request.host != ACCOUNT_HOST:
        return jsonify(error="请在账户中心修改偏好设置。"), 403
    user = current_account()
    if not user:
        return jsonify(error="请先登录。"), 401
    store = current_app.extensions["registration_store"]
    account = store.find_account(user["username"])
    if request.method == "GET":
        return jsonify(_preferences(store, account["id"]))
    payload = request.get_json(silent=True)
    theme = payload.get("theme") if isinstance(payload, dict) else None
    if theme not in ("system", "light", "dark"):
        return jsonify(error="颜色模式无效。"), 422
    with store.connection() as connection:
        connection.execute(
            "INSERT INTO user_preferences(user_id,theme) VALUES(?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET theme=excluded.theme",
            (account["id"], theme),
        )
    return jsonify(theme=theme)


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


@sites.get("/beta-shell.js")
@sites.get("/beta-shell.css")
def shell_asset():
    return asset(request.path[1:])


@sites.get("/api/public-profile/<slug>")
def public_profile(slug):
    if request.host != PROFILE_HOST:
        return jsonify(error="页面不存在。"), 404
    with current_app.extensions["registration_store"].connection() as connection:
        row = connection.execute(
            "SELECT p.slug,p.bio,r.nickname,r.username,g.github_id,g.login FROM profile_aliases a "
            "JOIN public_profiles p ON p.user_id=a.user_id JOIN registrations r ON r.id=a.user_id "
            "LEFT JOIN github_accounts g ON g.user_id=r.id WHERE a.slug=? COLLATE NOCASE "
            "AND r.email_verified_at IS NOT NULL", (slug,)).fetchone()
    if not row:
        return jsonify(error="没有找到这个个人主页。"), 404
    return jsonify(slug=row[0], bio=row[1], name=row[2] or row[3],
                   avatar=f"https://avatars.githubusercontent.com/u/{row[4]}?s=320" if row[4] else "/default-avatar.png",
                   github=f"https://github.com/{quote(row[5], safe='')}" if row[5] else None)


@sites.post("/api/profile/public")
def edit_public_profile():
    from .private_data import actor
    user, payload = actor(), request.get_json()
    if not user:
        return jsonify(error="请先登录。"), 401
    bio = payload.get("bio") if isinstance(payload, dict) else None
    if not isinstance(bio, str) or len(bio) > 280 or any(ord(c) < 32 and c != "\n" for c in bio):
        return jsonify(error="个人简介最多 280 个字符。"), 422
    with current_app.extensions["registration_store"].connection() as connection:
        own_profile(connection, user)
        connection.execute("UPDATE public_profiles SET bio=? WHERE user_id=?", (bio.strip(), user["id"]))
    return jsonify(ok=True)


@sites.post("/api/profile/address-request")
def request_address():
    from .private_data import actor
    user, payload = actor(), request.get_json()
    if not user:
        return jsonify(error="请先登录。"), 401
    slug = payload.get("slug", "") if isinstance(payload, dict) else ""
    if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?", slug) or slug in RESERVED:
        return jsonify(error="地址需为 1–39 位小写字母、数字或中间连字符，不能使用保留名称。"), 422
    store = current_app.extensions["registration_store"]
    if not store.allow_auth_attempt(request.remote_addr, user["username"], action="profile-address"):
        return jsonify(error="申请过于频繁，请稍后再试。"), 429
    with store.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM profile_aliases WHERE slug=? COLLATE NOCASE", (slug,)).fetchone():
            return jsonify(error="此地址已被保留或使用。"), 409
        connection.execute("UPDATE profile_requests SET state='superseded' WHERE user_id=? AND state='pending'", (user["id"],))
        connection.execute("INSERT INTO profile_requests(user_id,slug,created_at) VALUES(?,?,?)", (user["id"], slug, int(time.time())))
    return jsonify(ok=True), 201


@sites.route("/api/admin/profile-addresses", methods=["GET", "POST"])
def review_addresses():
    from .permissions import require_admin, begin_admin

    @require_admin
    def handle(actor):
        store = current_app.extensions["registration_store"]
        with store.connection() as connection:
            if request.method == "GET":
                rows = connection.execute("SELECT p.id,p.slug,p.created_at,r.nickname,r.username FROM profile_requests p "
                                          "JOIN registrations r ON r.id=p.user_id WHERE p.state='pending' ORDER BY p.id").fetchall()
                return jsonify(requests=[dict(zip(("id", "slug", "created_at", "nickname", "username"), row)) for row in rows])
            payload = request.get_json()
            if not isinstance(payload, dict) or type(payload.get("id")) is not int or payload.get("decision") not in ("approve", "reject"):
                return jsonify(error="审核参数无效。"), 422
            if not begin_admin(connection, actor):
                return jsonify(error="管理员权限已失效。"), 403
            row = connection.execute("SELECT user_id,slug FROM profile_requests WHERE id=? AND state='pending'", (payload["id"],)).fetchone()
            if not row:
                return jsonify(error="申请已处理。"), 409
            if payload["decision"] == "approve":
                if connection.execute("SELECT 1 FROM profile_aliases WHERE slug=? COLLATE NOCASE", (row[1],)).fetchone():
                    return jsonify(error="此地址已被占用，请驳回申请。"), 409
                connection.execute("INSERT INTO profile_aliases VALUES(?,?)", (row[1], row[0]))
                connection.execute("UPDATE public_profiles SET slug=? WHERE user_id=?", (row[1], row[0]))
            connection.execute("UPDATE profile_requests SET state=?,reviewed_at=?,reviewer_id=? WHERE id=?",
                               ("approved" if payload["decision"] == "approve" else "rejected",
                                int(time.time()), actor["id"], payload["id"]))
            connection.execute("INSERT INTO admin_audit(actor_id,action,target,created_at) VALUES(?,?,?,?)",
                               (actor["id"], "profile.address." + payload["decision"], str(payload["id"]), int(time.time())))
        return jsonify(ok=True)
    return handle()


def configure_site_pages(app):
    """Install before_request after the shared security checks."""
    @app.before_request
    def entry():
        if request.host not in (NAV_HOST, PROFILE_HOST):
            return None
        if request.method not in ("GET", "HEAD"):
            return None
        if request.path == "/api/public-profile/" or request.path.startswith("/api/public-profile/"):
            return public_profile(request.path.removeprefix("/api/public-profile/"))
        if request.host == NAV_HOST and request.path == "/":
            return redirect(f"https://{CALENDAR_HOST}/sso/start?next=%2Fcalendar%2F", 303)
        if request.path in ("/login", "/account"):
            return redirect("/sso/start?login=1&next=%2F", 303)
        if request.host == PROFILE_HOST and (request.path == "/" or re.fullmatch(r"/[^/.]+", request.path)):
            if request.path[1:] in RESERVED:
                return jsonify(error="页面不存在。"), 404
            return asset("public-profile.html")
        if request.path in ("/navigation.js", "/public-profile.js", "/sites.css"):
            return asset(request.path[1:])
        if request.path in ("/", "/register", "/verify", "/admin", "/privacy"):
            return jsonify(error="页面不存在。"), 404


def install_site_shell(app):
    @app.after_request
    def template(response):
        if response.status_code == 200 and response.mimetype == "text/html":
            response.direct_passthrough = False
            source = response.get_data(as_text=True)
            if "/beta-shell.js" not in source and "</head>" in source:
                icons = "" if 'src="/icons.js"' in source or "vendor/lucide.js" in source else '<script src="/icons.js" defer></script>'
                includes = ('<link rel="stylesheet" href="/beta-shell.css">' + icons
                            + '<script src="/beta-shell.js" defer></script>')
                # One shared shell is injected into account and calendar documents.
                response.set_data(source.replace("</head>", includes + "</head>"))
                response.headers.pop("ETag", None)
                response.headers.pop("Last-Modified", None)
        return response
