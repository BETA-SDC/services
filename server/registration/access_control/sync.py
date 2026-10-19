"""Account service access_control: sync."""
import time
from .domain import MEMBERSHIP_LIFETIME, ORG, owner_expiry
from .policy import audit


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

