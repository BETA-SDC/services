"""SQLite persistence; each operation owns its connection and transaction."""
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3

from .storage.sessions import AccountSessions
from .storage.rate_limits import AuthAttemptLimits
from .storage.schema import initialize_database


class RegistrationLimitReached(Exception):
    pass


class RegistrationAlreadyExists(Exception):
    pass


class RegistrationStore(AccountSessions, AuthAttemptLimits):
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
        initialize_database(self)

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
                from .site_accounts.schema import initialize_profile
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
