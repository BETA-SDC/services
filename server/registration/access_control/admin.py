"""Account service access_control: admin."""
import sqlite3
import time
from flask import Blueprint, current_app, jsonify, request
from .policy import audit, begin_admin, require_admin
from .repository import populate_user_access


admin = Blueprint("admin", __name__)



@admin.get("/admin/overview")
@require_admin
def overview(actor):
    store = current_app.extensions["registration_store"]
    now = int(time.time())
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
    parameters = (group_id, now) if group_id is not None else ()
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
        populate_user_access(connection, users, now)
        groups = [dict(row) for row in connection.execute(
            "SELECT g.*, (SELECT count(DISTINCT user_id) FROM group_members m WHERE m.group_id=g.id "
            "AND (m.valid_until IS NULL OR m.valid_until>?)) AS member_count "
            "FROM account_groups g WHERE archived=0 ORDER BY g.source,g.id", (now,))]
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

