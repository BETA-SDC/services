"""Server-side roles, local groups, and expiring GitHub membership snapshots."""
from functools import wraps
import calendar
from datetime import datetime
import sqlite3
import time
from zoneinfo import ZoneInfo

from flask import Blueprint, current_app, jsonify, request, session

from .auth import current_account

admin = Blueprint("admin", __name__)
ORG = "BETA-SDC"
MEMBERSHIP_LIFETIME = 86400


def owner_expiry(verified_at):
    verified = datetime.fromtimestamp(verified_at, ZoneInfo("Asia/Shanghai"))
    month_index = verified.year * 12 + verified.month - 1 + 6
    year, month = divmod(month_index, 12)
    month += 1
    day = min(verified.day, calendar.monthrange(year, month)[1])
    return int(verified.replace(year=year, month=month, day=day).timestamp())


def initialize_permissions(connection):
    schema = """
        CREATE TABLE IF NOT EXISTS account_roles (
            user_id INTEGER PRIMARY KEY REFERENCES registrations(id),
            role TEXT NOT NULL CHECK(role='admin')
        );
        CREATE TABLE IF NOT EXISTS github_admin_grants (
            user_id INTEGER PRIMARY KEY REFERENCES registrations(id),
            valid_until INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS github_owner_verifications (
            user_id INTEGER PRIMARY KEY REFERENCES registrations(id),
            verified_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS account_groups (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL,
            source TEXT NOT NULL CHECK(source IN ('local','github')),
            external_key TEXT UNIQUE, parent_id INTEGER REFERENCES account_groups(id),
            synced_at INTEGER, archived INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS group_members (
            user_id INTEGER NOT NULL REFERENCES registrations(id),
            group_id INTEGER NOT NULL REFERENCES account_groups(id),
            source TEXT NOT NULL CHECK(source IN ('local','github')),
            valid_until INTEGER,
            PRIMARY KEY(user_id,group_id,source)
        );
        CREATE TABLE IF NOT EXISTS organization_sync (
            user_id INTEGER PRIMARY KEY REFERENCES registrations(id),
            state TEXT NOT NULL, synced_at INTEGER NOT NULL,
            structure_complete INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS admin_audit (
            id INTEGER PRIMARY KEY, actor_id INTEGER NOT NULL,
            action TEXT NOT NULL, target TEXT NOT NULL, created_at INTEGER NOT NULL
        );
    """
    for statement in schema.split(";"):
        if statement.strip():
            connection.execute(statement)
    # Existing active grants are extended from their original verification, not deployment.
    legacy = connection.execute(
        "SELECT a.user_id,a.valid_until FROM github_admin_grants a LEFT JOIN github_owner_verifications v "
        "ON v.user_id=a.user_id WHERE v.user_id IS NULL").fetchall()
    for user_id, until in legacy:
        verified_at = max(0, until - MEMBERSHIP_LIFETIME)
        new_until = owner_expiry(verified_at) if until > int(time.time()) else until
        connection.execute("INSERT INTO github_owner_verifications VALUES (?,?)", (user_id, verified_at))
        connection.execute("UPDATE github_admin_grants SET valid_until=? WHERE user_id=?", (new_until, user_id))


def is_admin(connection, user_id):
    return bool(connection.execute(
        "SELECT 1 FROM account_roles WHERE user_id=? UNION ALL "
        "SELECT 1 FROM github_admin_grants a JOIN github_accounts g ON g.user_id=a.user_id "
        "WHERE a.user_id=? AND a.valid_until>?", (user_id, user_id, int(time.time())),
    ).fetchone())


def access_status():
    current = current_account()
    if not current:
        return {"is_admin": False, "groups": []}
    store = current_app.extensions["registration_store"]
    user = store.find_account(current["username"])
    with store.connection() as connection:
        groups = connection.execute(
            "SELECT DISTINCT g.id,g.name,g.parent_id,g.source FROM group_members m "
            "JOIN account_groups g ON g.id=m.group_id WHERE m.user_id=? AND g.archived=0 "
            "AND (m.valid_until IS NULL OR m.valid_until>?) ORDER BY g.id",
            (user["id"], int(time.time())),
        ).fetchall()
        snapshot = connection.execute(
            "SELECT state,synced_at,structure_complete FROM organization_sync WHERE user_id=?", (user["id"],)
        ).fetchone()
        owner = connection.execute(
            "SELECT v.verified_at,a.valid_until FROM github_admin_grants a "
            "JOIN github_owner_verifications v ON v.user_id=a.user_id "
            "JOIN github_accounts g ON g.user_id=a.user_id WHERE a.user_id=?", (user["id"],)
        ).fetchone()
        return {"is_admin": is_admin(connection, user["id"]),
                "groups": [dict(zip(("id", "name", "parent_id", "source"), row)) for row in groups],
                "organization_sync": dict(zip(("state", "synced_at", "structure_complete"), snapshot))
                if snapshot else None,
                "github_owner": {"verified_at": owner[0], "valid_until": owner[1],
                                 "expired": owner[1] <= int(time.time())} if owner else None}


def ensure_github_group(connection, key, name, parent_id, now):
    connection.execute(
        "INSERT INTO account_groups(name,source,external_key,parent_id,synced_at) VALUES (?,'github',?,?,?) "
        "ON CONFLICT(external_key) DO UPDATE SET name=excluded.name,parent_id=excluded.parent_id,"
        "synced_at=excluded.synced_at,archived=0", (name, key, parent_id, now),
    )
    return connection.execute("SELECT id FROM account_groups WHERE external_key=?", (key,)).fetchone()[0]


def sync_memberships(connection, user_id, identity):
    now = int(time.time())
    org = ensure_github_group(connection, "github:org:BETA-SDC", ORG, None, now)
    snapshot = identity.get("organization_access")
    public_member = any(item["login"].casefold() == ORG.casefold()
                        for item in identity.get("organizations", []))
    if snapshot is None:
        # A missing public membership is not proof of absence: membership can be private.
        if public_member:
            connection.execute(
                "INSERT INTO group_members VALUES (?,?,'github',?) ON CONFLICT(user_id,group_id,source) "
                "DO UPDATE SET valid_until=excluded.valid_until", (user_id, org, now + MEMBERSHIP_LIFETIME),
            )
            connection.execute(
                "UPDATE organization_sync SET state='public_member' WHERE user_id=? AND state='not_member'",
                (user_id,),
            )
        return
    state = snapshot["state"]
    if state == "not_member" and public_member:
        state = "permission_required"
    stored_state = "public_member" if public_member and state in ("permission_required", "unavailable") else state
    connection.execute(
        "INSERT INTO organization_sync VALUES (?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
        "state=excluded.state,synced_at=excluded.synced_at,structure_complete=excluded.structure_complete",
        (user_id, stored_state, now, int(snapshot["structure_complete"])),
    )
    if state not in ("member", "not_member"):
        # Failures must neither delete membership nor extend its validity.
        if public_member:
            sync_memberships(connection, user_id, {**identity, "organization_access": None})
        return
    was_owner = connection.execute("SELECT 1 FROM github_admin_grants WHERE user_id=?", (user_id,)).fetchone()
    connection.execute("DELETE FROM github_admin_grants WHERE user_id=?", (user_id,))
    connection.execute("DELETE FROM github_owner_verifications WHERE user_id=?", (user_id,))
    if state == "member" and snapshot.get("role") == "owner":
        connection.execute("INSERT INTO github_admin_grants(user_id,valid_until) VALUES (?,?)",
                           (user_id, owner_expiry(now)))
        connection.execute("INSERT INTO github_owner_verifications VALUES (?,?)", (user_id, now))
        if not was_owner:
            audit(connection, user_id, "role.github_owner.grant", user_id)
    elif was_owner:
        audit(connection, user_id, "role.github_owner.revoke", user_id)
    connection.execute("DELETE FROM group_members WHERE user_id=? AND source='github'", (user_id,))
    if state == "not_member":
        return
    connection.execute("INSERT INTO group_members VALUES (?,?,'github',?)",
                       (user_id, org, now + MEMBERSHIP_LIFETIME))
    teams = {team["id"]: team for team in snapshot["teams"]}
    ids = {}
    # Create all nodes first, then wire parents independent of API ordering.
    for team in teams.values():
        ids[team["id"]] = ensure_github_group(
            connection, f"github:team:{team['id']}", team["name"], org, now)
    for team in teams.values():
        connection.execute("UPDATE account_groups SET parent_id=? WHERE id=?",
                           (ids.get(team["parent_id"], org), ids[team["id"]]))
    member_ids = set(snapshot["member_team_ids"])
    for team_id in list(member_ids):
        seen = set()
        while team_id in teams and team_id not in seen:
            seen.add(team_id)
            member_ids.add(team_id)
            team_id = teams[team_id]["parent_id"]
    for team_id in member_ids:
        if team_id in ids:
            connection.execute("INSERT INTO group_members VALUES (?,?,'github',?)",
                               (user_id, ids[team_id], now + MEMBERSHIP_LIFETIME))
    if snapshot["structure_complete"]:
        for group_id, key in connection.execute(
            "SELECT id,external_key FROM account_groups WHERE external_key LIKE 'github:team:%'"
        ).fetchall():
            if group_id not in ids.values():
                connection.execute("UPDATE account_groups SET archived=1 WHERE id=?", (group_id,))
                connection.execute("DELETE FROM group_members WHERE group_id=? AND source='github'", (group_id,))


def require_admin(function):
    @wraps(function)
    def protected(*args, **kwargs):
        account = current_account()
        if not account:
            return jsonify(error="请先登录。"), 401
        store = current_app.extensions["registration_store"]
        user = store.find_account(account["username"])
        with store.connection() as connection:
            if not is_admin(connection, user["id"]):
                return jsonify(error="需要管理员权限。"), 403
        if request.method != "GET" and int(time.time()) - session.get("auth_time", 0) > 600:
            return jsonify(error="请重新登录后进行管理操作。"), 403
        return function(user, *args, **kwargs)
    return protected


def audit(connection, actor, action, target):
    connection.execute("INSERT INTO admin_audit(actor_id,action,target,created_at) VALUES (?,?,?,?)",
                       (actor, action, str(target), int(time.time())))


def begin_admin(connection, actor):
    connection.execute("BEGIN IMMEDIATE")
    return is_admin(connection, actor["id"])


@admin.get("/admin/overview")
@require_admin
def overview(actor):
    store = current_app.extensions["registration_store"]
    try:
        page = max(1, int(request.args.get("page", 1)))
        group_id = int(request.args["group"]) if request.args.get("group") else None
    except ValueError:
        return jsonify(error="分页参数无效。"), 400
    condition = (
        " WHERE EXISTS (SELECT 1 FROM group_members m JOIN account_groups f ON f.id=m.group_id "
        "WHERE m.user_id=r.id AND m.group_id=? AND f.archived=0 "
        "AND (m.valid_until IS NULL OR m.valid_until>?))"
    ) if group_id is not None else ""
    parameters = (group_id, int(time.time())) if group_id is not None else ()
    with store.connection() as connection:
        connection.row_factory = sqlite3.Row
        users = [dict(row) for row in connection.execute(
            "SELECT r.id,r.username,r.real_name,r.nickname,r.email,r.created_at,r.email_verified_at,"
            "CASE WHEN a.role='admin' THEN 'admin' ELSE 'member' END AS role,g.login AS github_login,"
            "s.state AS organization_state,s.synced_at AS organization_synced_at,"
            "s.structure_complete FROM registrations r LEFT JOIN account_roles a ON a.user_id=r.id "
            "LEFT JOIN github_accounts g ON g.user_id=r.id LEFT JOIN organization_sync s ON s.user_id=r.id "
            + condition + " ORDER BY r.id LIMIT 50 OFFSET ?", parameters + ((page - 1) * 50,),
        )]
        for user in users:
            owner = connection.execute(
                "SELECT valid_until FROM github_admin_grants a JOIN github_accounts g ON g.user_id=a.user_id "
                "WHERE a.user_id=? AND a.valid_until>?", (user["id"], int(time.time())),
            ).fetchone()
            user["role_source"] = "github_owner" if owner else "local"
            user["admin_valid_until"] = owner[0] if owner else None
            if owner:
                user["role"] = "admin"
            user["group_ids"] = [row[0] for row in connection.execute(
                "SELECT group_id FROM group_members WHERE user_id=? AND "
                "(valid_until IS NULL OR valid_until>?)", (user["id"], int(time.time())))]
        groups = [dict(row) for row in connection.execute(
            "SELECT g.*, (SELECT count(DISTINCT user_id) FROM group_members m WHERE m.group_id=g.id "
            "AND (m.valid_until IS NULL OR m.valid_until>?)) AS member_count "
            "FROM account_groups g WHERE archived=0 ORDER BY g.source,g.id", (int(time.time()),))]
        records = [dict(row) for row in connection.execute(
            "SELECT a.id,COALESCE(NULLIF(r.real_name,''),NULLIF(r.nickname,''),r.username) AS actor,"
            "a.action,a.target,a.created_at FROM admin_audit a "
            "LEFT JOIN registrations r ON r.id=a.actor_id ORDER BY a.id DESC LIMIT 30")]
        total = connection.execute("SELECT count(*) FROM registrations r" + condition, parameters).fetchone()[0]
    return jsonify(users=users, groups=groups, audit=records, total=total, page=page)


@admin.post("/admin/groups")
@require_admin
def groups_change(actor):
    data = request.get_json()
    if not isinstance(data, dict):
        return jsonify(error="参数无效。"), 400
    name = data.get("name")
    parent = data.get("parent_id")
    if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 80
            or any(not char.isprintable() for char in name)
            or (parent is not None and type(parent) is not int)):
        return jsonify(error="请输入有效的分组名称。"), 422
    store = current_app.extensions["registration_store"]
    with store.connection() as connection:
        if not begin_admin(connection, actor):
            return jsonify(error="管理员权限已撤销。"), 403
        if parent is not None and not connection.execute(
            "SELECT 1 FROM account_groups WHERE id=? AND source='local' AND archived=0", (parent,)
        ).fetchone():
            return jsonify(error="只能在本地分组下创建子组。"), 422
        cursor = connection.execute("INSERT INTO account_groups(name,source,parent_id) VALUES (?,'local',?)",
                                    (name.strip(), parent))
        audit(connection, actor["id"], "group.create", cursor.lastrowid)
    return jsonify(ok=True)


@admin.post("/admin/membership")
@require_admin
def membership_change(actor):
    data = request.get_json()
    if (not isinstance(data, dict) or type(data.get("user_id")) is not int
            or type(data.get("group_id")) is not int or type(data.get("member")) is not bool):
        return jsonify(error="参数无效。"), 422
    with current_app.extensions["registration_store"].connection() as connection:
        if not begin_admin(connection, actor):
            return jsonify(error="管理员权限已撤销。"), 403
        if not connection.execute("SELECT 1 FROM registrations WHERE id=?", (data["user_id"],)).fetchone():
            return jsonify(error="账户不存在。"), 404
        if not connection.execute(
            "SELECT 1 FROM account_groups WHERE id=? AND source='local' AND archived=0", (data["group_id"],)
        ).fetchone():
            return jsonify(error="GitHub 分组只能通过 GitHub 同步。"), 422
        if data["member"]:
            connection.execute("INSERT OR IGNORE INTO group_members VALUES (?,?,'local',NULL)",
                               (data["user_id"], data["group_id"]))
        else:
            connection.execute("DELETE FROM group_members WHERE user_id=? AND group_id=? AND source='local'",
                               (data["user_id"], data["group_id"]))
        audit(connection, actor["id"], "membership.add" if data["member"] else "membership.remove",
              f"{data['user_id']}:{data['group_id']}")
    return jsonify(ok=True)


@admin.post("/admin/role")
@require_admin
def role_change(actor):
    data = request.get_json()
    if (not isinstance(data, dict) or type(data.get("user_id")) is not int
            or data.get("role") not in ("admin", "member")):
        return jsonify(error="参数无效。"), 422
    with current_app.extensions["registration_store"].connection() as connection:
        if not begin_admin(connection, actor):
            return jsonify(error="管理员权限已撤销。"), 403
        target = data["user_id"]
        if not connection.execute(
            "SELECT 1 FROM registrations WHERE id=? AND email_verified_at IS NOT NULL", (target,)
        ).fetchone():
            return jsonify(error="账户不存在或邮箱未验证。"), 422
        if target == actor["id"]:
            return jsonify(error="不能修改自己的管理员来源或权限。"), 409
        if data["role"] == "member" and connection.execute(
            "SELECT 1 FROM github_admin_grants WHERE user_id=? AND valid_until>?",
            (target, int(time.time())),
        ).fetchone():
            return jsonify(error="Owner 管理员权限由 GitHub 同步，请在 GitHub 修改组织角色。"), 409
        if data["role"] == "admin":
            connection.execute("INSERT OR IGNORE INTO account_roles VALUES (?,'admin')", (target,))
        else:
            connection.execute("DELETE FROM account_roles WHERE user_id=?", (target,))
        audit(connection, actor["id"], "role." + data["role"], target)
    return jsonify(ok=True)


@admin.post("/admin/group/update")
@require_admin
def group_update(actor):
    data = request.get_json()
    if not isinstance(data, dict) or type(data.get("group_id")) is not int:
        return jsonify(error="参数无效。"), 422
    name = data.get("name")
    archived = data.get("archived") is True
    if not archived and (not isinstance(name, str) or not 1 <= len(name.strip()) <= 80
                         or any(not char.isprintable() for char in name)):
        return jsonify(error="分组名称无效。"), 422
    with current_app.extensions["registration_store"].connection() as connection:
        if not begin_admin(connection, actor):
            return jsonify(error="管理员权限已撤销。"), 403
        group_id = data["group_id"]
        if not connection.execute(
            "SELECT 1 FROM account_groups WHERE id=? AND source='local' AND archived=0", (group_id,)
        ).fetchone():
            return jsonify(error="分组不存在或由 GitHub 管理。"), 422
        if archived:
            if connection.execute("SELECT 1 FROM account_groups WHERE parent_id=? AND archived=0",
                                  (group_id,)).fetchone():
                return jsonify(error="请先归档所有子分组。"), 409
            connection.execute("UPDATE account_groups SET archived=1 WHERE id=?", (group_id,))
            connection.execute("DELETE FROM group_members WHERE group_id=?", (group_id,))
        else:
            connection.execute("UPDATE account_groups SET name=? WHERE id=?", (name.strip(), group_id))
        audit(connection, actor["id"], "group.archive" if archived else "group.rename", group_id)
    return jsonify(ok=True)


@admin.post("/admin/memberships")
@require_admin
def memberships_batch(actor):
    data = request.get_json()
    if (not isinstance(data, dict) or type(data.get("user_id")) is not int
            or not isinstance(data.get("changes"), list) or len(data["changes"]) > 80):
        return jsonify(error="参数无效。"), 422
    changes = data["changes"]
    if any(not isinstance(item, dict) or type(item.get("group_id")) is not int
           or type(item.get("member")) is not bool for item in changes):
        return jsonify(error="参数无效。"), 422
    with current_app.extensions["registration_store"].connection() as connection:
        if not begin_admin(connection, actor):
            return jsonify(error="管理员权限已撤销。"), 403
        if not connection.execute("SELECT 1 FROM registrations WHERE id=?", (data["user_id"],)).fetchone():
            return jsonify(error="账户不存在。"), 404
        # Validate the entire batch before changing anything.
        for item in changes:
            if not connection.execute(
                "SELECT 1 FROM account_groups WHERE id=? AND source='local' AND archived=0",
                (item["group_id"],),
            ).fetchone():
                return jsonify(error="GitHub 分组只能通过 GitHub 同步。"), 422
        for item in changes:
            if item["member"]:
                connection.execute("INSERT OR IGNORE INTO group_members VALUES (?,?,'local',NULL)",
                                   (data["user_id"], item["group_id"]))
            else:
                connection.execute("DELETE FROM group_members WHERE user_id=? AND group_id=? AND source='local'",
                                   (data["user_id"], item["group_id"]))
            audit(connection, actor["id"], "membership.add" if item["member"] else "membership.remove",
                  f"{data['user_id']}:{item['group_id']}")
    return jsonify(ok=True)
