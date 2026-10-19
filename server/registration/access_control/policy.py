"""Account service access_control: policy."""
from functools import wraps
import time
from flask import current_app, jsonify, request, session
from ..auth import current_account

_CURRENT_ACCOUNT = object()


def is_admin(connection, user_id):
    return bool(connection.execute(
        "SELECT 1 FROM account_roles WHERE user_id=? UNION ALL "
        "SELECT 1 FROM github_admin_grants a JOIN github_accounts g ON g.user_id=a.user_id "
        "WHERE a.user_id=? AND a.valid_until>?", (user_id, user_id, int(time.time())),
    ).fetchone())



def access_status(*, current=_CURRENT_ACCOUNT):
    if current is _CURRENT_ACCOUNT:
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

