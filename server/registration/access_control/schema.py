"""Account service access_control: schema."""
import time
from .domain import MEMBERSHIP_LIFETIME, owner_expiry


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

