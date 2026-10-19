"""Bounded batch reads for the administrator's account directory."""


def populate_user_access(connection, users, now):
    if not users:
        return
    placeholders = ",".join("?" for _ in users)
    identifiers = tuple(user["id"] for user in users)
    owners = dict(connection.execute(
        "SELECT a.user_id,a.valid_until FROM github_admin_grants a "
        "JOIN github_accounts g ON g.user_id=a.user_id "
        f"WHERE a.user_id IN ({placeholders}) AND a.valid_until>?",
        identifiers + (now,),
    ))
    memberships = {user_id: [] for user_id in identifiers}
    for user_id, group_id in connection.execute(
        f"SELECT user_id,group_id FROM group_members WHERE user_id IN ({placeholders}) "
        "AND (valid_until IS NULL OR valid_until>?) ORDER BY user_id,group_id",
        identifiers + (now,),
    ):
        memberships[user_id].append(group_id)
    for user in users:
        expiry = owners.get(user["id"])
        user["role_source"] = "github_owner" if expiry is not None else "local"
        user["admin_valid_until"] = expiry
        if expiry is not None:
            user["role"] = "admin"
        user["group_ids"] = memberships[user["id"]]
