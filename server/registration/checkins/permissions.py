"""Shared account, membership, ownership, administrator and step-up checks."""
import time

from flask import abort, jsonify

from ..permissions import is_admin
from ..private_data import actor


def member(connection, user):
    if not user:
        return False
    now = int(time.time())
    return bool(connection.execute(
        "SELECT 1 FROM github_accounts a JOIN group_members m ON m.user_id=a.user_id "
        "JOIN account_groups g ON g.id=m.group_id WHERE a.user_id=? AND m.source='github' "
        "AND g.archived=0 AND g.external_key='github:org:BETA-SDC' "
        "AND (m.valid_until IS NULL OR m.valid_until>?) UNION ALL "
        "SELECT 1 FROM github_accounts a JOIN github_admin_grants g ON g.user_id=a.user_id "
        "WHERE a.user_id=? AND g.valid_until>?",
        (user["id"], now, user["id"], now),
    ).fetchone())


def organizer(connection):
    user = actor()
    if not user:
        abort(401, description="请先登录账户中心。")
    if not (member(connection, user) or is_admin(connection, user["id"])):
        abort(403, description="需要有效的 BETA-SDC 成员或管理员权限。")
    return user


def administrator(connection):
    user = organizer(connection)
    if not is_admin(connection, user["id"]):
        abort(403, description="仅管理员可以管理签到表或查看名单。")
    return user


def require_factor(connection, user):
    from ..second_factor import proof
    if not proof(connection, user):
        response = jsonify(error="请先完成二次验证。", code="second_factor_required")
        response.status_code = 401
        abort(response)


def can_edit_page(connection, event, user):
    return bool(user and (event["creator_id"] == user["id"] or is_admin(connection, user["id"])))


def account(connection):
    user = actor()
    if not user:
        abort(401, description="请先登录账户中心。")
    if not user["email_verified_at"]:
        abort(403, description="请先验证绑定邮箱。")
    return user


def eligible(connection, event, user):
    return bool(user and user["email_verified_at"]
                and (can_edit_page(connection, event, user) or member(connection, user)))
