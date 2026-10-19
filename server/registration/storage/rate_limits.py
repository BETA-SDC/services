"""SQLite operations sharing the owning store connection lifecycle."""
import time


class AuthAttemptLimits:
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

