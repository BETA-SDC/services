"""Transactional, idempotent account schema initialization."""


def initialize_database(store):
    store.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with store.connection() as connection:
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
        from ..access_control.schema import initialize_permissions
        initialize_permissions(connection)
        from ..privacy.schema import initialize_private
        initialize_private(connection)
        from ..second_factor import initialize_second_factor
        initialize_second_factor(connection)
        from ..site_accounts.schema import initialize_sites
        initialize_sites(connection)
        from ..checkins.schema import initialize_checkin
        initialize_checkin(connection)
        connection.execute("PRAGMA user_version = 11")
    store.path.chmod(0o600)
