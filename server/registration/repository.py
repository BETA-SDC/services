"""SQLite persistence; each operation owns its connection and transaction."""
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
import hashlib
import secrets
import time


class RegistrationLimitReached(Exception):
    pass


class RegistrationAlreadyExists(Exception):
    pass


class RegistrationStore:
    def __init__(self, database, max_records):
        self.path = Path(database)
        if not isinstance(max_records, int) or max_records < 1:
            raise ValueError("Registration record limit must be a positive integer")
        self.max_records = max_records

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.path, timeout=5)) as connection, connection:
            yield connection

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS registrations (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS registrations_email_unique_nocase "
                               "ON registrations(email COLLATE NOCASE)")
            # Existing password-less registrations remain reserved, not claimable.
            columns = {row[1] for row in connection.execute("PRAGMA table_info(registrations)")}
            if "password_hash" not in columns:
                connection.execute("ALTER TABLE registrations ADD COLUMN password_hash TEXT")
            if "email_verified_at" not in columns:
                connection.execute("ALTER TABLE registrations ADD COLUMN email_verified_at INTEGER")
            if "real_name" not in columns:
                connection.execute("ALTER TABLE registrations ADD COLUMN real_name TEXT NOT NULL DEFAULT ''")
            if "nickname" not in columns:
                connection.execute("ALTER TABLE registrations ADD COLUMN nickname TEXT NOT NULL DEFAULT ''")
                connection.execute("UPDATE registrations SET nickname=username")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS email_verifications (
                    user_id INTEGER PRIMARY KEY REFERENCES registrations(id),
                    token_hash TEXT UNIQUE NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS mail_outbox (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER UNIQUE NOT NULL REFERENCES registrations(id),
                    recipient TEXT NOT NULL,
                    token TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    next_attempt INTEGER NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES registrations(id),
                    expires_at INTEGER NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS login_attempts (
                    bucket TEXT PRIMARY KEY,
                    started_at INTEGER NOT NULL,
                    attempts INTEGER NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS signup_challenges (
                    challenge_hash TEXT PRIMARY KEY, email TEXT NOT NULL,
                    code_hash TEXT NOT NULL, created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS signup_mail_outbox (
                    challenge_hash TEXT PRIMARY KEY, recipient TEXT NOT NULL,
                    code TEXT NOT NULL, created_at INTEGER NOT NULL,
                    next_attempt INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS github_accounts (
                    user_id INTEGER PRIMARY KEY REFERENCES registrations(id),
                    github_id INTEGER UNIQUE NOT NULL, login TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS github_flows (
                    state_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
                    session_hash TEXT NOT NULL, verifier TEXT NOT NULL,
                    expires_at INTEGER NOT NULL
                )
            """)
            github_columns = {row[1] for row in connection.execute("PRAGMA table_info(github_accounts)")}
            if "profile_json" not in github_columns:
                connection.execute("ALTER TABLE github_accounts ADD COLUMN profile_json TEXT NOT NULL DEFAULT '{}'")
            if "synced_at" not in github_columns:
                connection.execute("ALTER TABLE github_accounts ADD COLUMN synced_at INTEGER")
            from .permissions import initialize_permissions
            initialize_permissions(connection)
            from .private_data import initialize_private
            initialize_private(connection)
            from .second_factor import initialize_second_factor
            initialize_second_factor(connection)
            from .sites import initialize_sites
            initialize_sites(connection)
            from .checkin import initialize_checkin
            initialize_checkin(connection)
            connection.execute("PRAGMA user_version = 11")
        self.path.chmod(0o600)

    def add(self, username, email, password_hash=None):
        try:
            with self.connection() as connection:
                # Serialize the count and insert across Gunicorn workers.
                connection.execute("BEGIN IMMEDIATE")
                count = connection.execute("SELECT COUNT(*) FROM registrations").fetchone()[0]
                if count >= self.max_records:
                    raise RegistrationLimitReached()
                connection.execute(
                    "INSERT INTO registrations (username, email, password_hash,nickname) VALUES (?, ?, ?,?)",
                    (username, email, password_hash, username),
                )
                user_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
                from .sites import initialize_profile
                initialize_profile(connection, user_id, username)
                if password_hash:
                    # Persist the account and its verification email atomically.
                    from .verification import enqueue_verification
                    enqueue_verification(connection, user_id, email)
        except sqlite3.IntegrityError as error:
            raise RegistrationAlreadyExists() from error

    def find_account(self, identifier):
        with self.connection() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT id, username, email, password_hash, email_verified_at,real_name,nickname FROM registrations "
                "WHERE username = ? COLLATE NOCASE OR email = ? COLLATE NOCASE LIMIT 1",
                (identifier, identifier),
            ).fetchone()
            return dict(row) if row else None

    @staticmethod
    def token_hash(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def create_session(self, user_id, lifetime):
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        with self.connection() as connection:
            connection.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))
            connection.execute("DELETE FROM site_sessions WHERE token_hash NOT IN (SELECT token_hash FROM auth_sessions)")
            connection.execute(
                "INSERT INTO auth_sessions VALUES (?, ?, ?)",
                (self.token_hash(token), user_id, now + lifetime),
            )
            connection.execute("INSERT INTO site_sessions VALUES(?,?,'')", (self.token_hash(token), self.session_host()))
        return token

    def session_account(self, token):
        if not isinstance(token, str) or len(token) > 128:
            return None
        with self.connection() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT r.username, r.email, r.email_verified_at,r.real_name,r.nickname FROM auth_sessions s "
                "JOIN registrations r ON r.id = s.user_id LEFT JOIN site_sessions h ON h.token_hash=s.token_hash "
                "WHERE s.token_hash = ? AND s.expires_at > ? AND r.email_verified_at IS NOT NULL "
                "AND COALESCE(h.site_host,'account.betasdc.duckdns.org')=? "
                "AND (COALESCE(h.parent_hash,'')='' OR EXISTS(SELECT 1 FROM auth_sessions p "
                "LEFT JOIN site_sessions ph ON ph.token_hash=p.token_hash "
                "WHERE p.token_hash=h.parent_hash AND p.user_id=s.user_id AND p.expires_at>? "
                "AND COALESCE(ph.parent_hash,'')='' AND COALESCE(ph.site_host,'account.betasdc.duckdns.org')="
                "'account.betasdc.duckdns.org'))",
                (self.token_hash(token), int(time.time()), self.session_host(), int(time.time())),
            ).fetchone()
            return dict(row) if row else None

    def revoke_session(self, token):
        if isinstance(token, str):
            with self.connection() as connection:
                hashed = self.token_hash(token)
                row = connection.execute("SELECT parent_hash FROM site_sessions WHERE token_hash=?", (hashed,)).fetchone()
                root = row[0] if row and row[0] else hashed
                connection.execute("DELETE FROM auth_sessions WHERE token_hash=? OR token_hash IN "
                                   "(SELECT token_hash FROM site_sessions WHERE parent_hash=?)",
                                   (root, root))

    @staticmethod
    def session_host():
        from flask import has_request_context, request
        return request.host if has_request_context() else "account.betasdc.duckdns.org"

    def allow_auth_attempt(self, source, identifier, action="login", *, limits=(30, 10)):
        now = int(time.time())
        buckets = [
            (self.token_hash(action + ":ip:" + source), limits[0]),
            (self.token_hash(action + ":account:" + identifier.casefold()), limits[1]),
        ]
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM login_attempts WHERE started_at <= ?", (now - 900,))
            for bucket, limit in buckets:
                row = connection.execute(
                    "SELECT attempts FROM login_attempts WHERE bucket = ?", (bucket,)
                ).fetchone()
                if row and row[0] >= limit:
                    return False
            for bucket, _ in buckets:
                connection.execute(
                    "INSERT INTO login_attempts VALUES (?, ?, 1) "
                    "ON CONFLICT(bucket) DO UPDATE SET attempts = attempts + 1",
                    (bucket, now),
                )
        return True
