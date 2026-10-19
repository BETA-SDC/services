"""SQLite operations sharing the owning store connection lifecycle."""
import hashlib
import secrets
import sqlite3
import time


class AccountSessions:
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

