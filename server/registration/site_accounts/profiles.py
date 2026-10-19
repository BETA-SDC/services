"""Account service site_accounts: profiles."""
import re
import time
from urllib.parse import quote
from flask import current_app, jsonify, request
from .domain import PROFILE_HOST, RESERVED, sites
from .schema import own_profile


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
    from ..privacy.identity import actor
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
    from ..privacy.identity import actor
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
    from ..access_control.policy import require_admin, begin_admin

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

