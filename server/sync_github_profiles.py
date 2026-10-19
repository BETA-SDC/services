"""Backfill existing bindings through the isolated broker without revealing user data."""
import http.client
import json
import time

from server.registration.github import fetch_public_identity
from server.registration.repository import RegistrationStore
from server.registration.permissions import sync_memberships


def sync(store):
    updated = failed = 0
    with store.connection() as connection:
        accounts = connection.execute("SELECT user_id,github_id,login FROM github_accounts").fetchall()
    for user_id, github_id, login in accounts:
        try:
            profile = fetch_public_identity(github_id, login)
        except (OSError, ValueError, http.client.HTTPException):
            failed += 1
            continue
        with store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE github_accounts SET login=?,profile_json=?,synced_at=? "
                "WHERE user_id=? AND github_id=?",
                (profile["login"], json.dumps(profile), int(time.time()), user_id, github_id),
            )
            updated += cursor.rowcount
            if cursor.rowcount:
                sync_memberships(connection, user_id, profile)
    return updated, failed


if __name__ == "__main__":
    counts = sync(RegistrationStore("/var/lib/betasdc/registrations.sqlite3", 1000))
    print("GitHub public profile sync: updated=%d failed=%d" % counts)
