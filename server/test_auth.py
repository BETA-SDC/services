from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

from werkzeug.security import check_password_hash

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server.registration import create_app
from server.registration.repository import RegistrationStore
from server.test_support import signup_payload

PASSWORD = "Testing-long-phrase-2026!"


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "auth.sqlite3"
        self.app = create_app(self.path)
        self.client = self.app.test_client()
        self.headers = {
            "Host": "account.betasdc.duckdns.org",
            "X-Forwarded-For": "10.17.131.26", "X-Forwarded-Proto": "https",
            "Origin": "https://account.betasdc.duckdns.org",
        }
        self.refresh_csrf()

    def tearDown(self):
        self.temp.cleanup()

    def refresh_csrf(self):
        response = self.client.get("/api/session", headers=self.headers)
        self.headers["X-CSRF-Token"] = response.get_json()["csrf_token"]
        return response

    def register(self, username="auth_user", email="auth@westlake.edu.cn", password=PASSWORD):
        payload = signup_payload(self.client, self.headers,
                                 self.app.extensions["registration_store"], {
            "username": username, "email": email, "password": password,
        })
        return self.client.post("/api/register", headers=self.headers, json=payload)

    def login(self, identifier="auth_user", password=PASSWORD):
        response = self.client.post("/api/login", headers=self.headers, json={
            "identifier": identifier, "password": password,
        })
        if response.status_code == 200:
            self.headers["X-CSRF-Token"] = response.get_json()["csrf_token"]
        return response

    def test_registration_login_and_revocable_logout(self):
        self.assertEqual(self.register().status_code, 201)
        account = self.app.extensions["registration_store"].find_account("AUTH_USER")
        self.assertNotEqual(account["password_hash"], PASSWORD)
        self.assertTrue(check_password_hash(account["password_hash"], PASSWORD))
        response = self.login("AUTH@WESTLAKE.EDU.CN")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["user"]["username"], "auth_user")
        cookie = response.headers["Set-Cookie"]
        for flag in ("Secure", "HttpOnly", "SameSite=Lax", "Path=/"):
            self.assertIn(flag, cookie)
        old_cookie = self.client.get_cookie(
            "__Host-betasdc", domain="account.betasdc.duckdns.org",
        ).value
        self.assertEqual(self.refresh_csrf().get_json()["user"]["username"], "auth_user")
        self.assertEqual(self.client.post(
            "/api/logout", json={}, headers=self.headers,
        ).status_code, 200)
        self.client.set_cookie(
            "__Host-betasdc", old_cookie, domain="account.betasdc.duckdns.org",
        )
        self.assertIsNone(self.refresh_csrf().get_json()["user"])

    def test_csrf_wrong_password_and_unknown_accounts(self):
        self.assertEqual(self.register().status_code, 201)
        for value in ("", "incorrect", "错误"):
            headers = dict(self.headers, **{"X-CSRF-Token": value})
            self.assertEqual(self.client.post("/api/login", headers=headers, json={
                "identifier": "auth_user", "password": PASSWORD,
            }).status_code, 403)
        wrong = self.login(password="incorrect-password")
        unknown = self.login("does_not_exist")
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(unknown.get_json(), wrong.get_json())
        self.assertEqual(unknown.status_code, 401)

    def test_password_rules_and_no_password_in_responses(self):
        for value in (None, "", "short", "x" * 129):
            self.assertEqual(self.register(password=value).status_code, 422)
        response = self.register()
        self.assertNotIn("password", response.get_data(as_text=True))
        self.assertEqual(self.path.with_suffix(".session-key").stat().st_mode & 0o777, 0o600)

    def test_invalid_login_json(self):
        for payload in ("{", "null", "[" * 1100 + "]" * 1100):
            response = self.client.post(
                "/api/login", headers=self.headers, data=payload,
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 400)

    def test_old_records_are_reserved_not_claimable_or_loginable(self):
        self.app.extensions["registration_store"].add("old_user", "old@westlake.edu.cn")
        self.assertEqual(self.register("new_user", "old@westlake.edu.cn").status_code, 409)
        self.assertEqual(self.login("old_user").status_code, 401)

    def test_non_campus_email_rejected_but_existing_account_can_login(self):
        for email in ("new@example.com", "new@students.westlake.edu.cn",
                      "new@westlake.edu.cn.attacker.example"):
            self.assertEqual(self.register(email=email).status_code, 422)
        store = self.app.extensions["registration_store"]
        with store.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM registrations").fetchone()[0], 0)
        from werkzeug.security import generate_password_hash
        store.add("existing_user", "existing@example.com", generate_password_hash(PASSWORD))
        with store.connection() as connection:
            connection.execute("UPDATE registrations SET email_verified_at=1 WHERE username='existing_user'")
        self.assertEqual(self.login("existing@example.com").status_code, 200)

    def test_expiry_and_login_rate_limit_survive_application_restart(self):
        self.assertEqual(self.register().status_code, 201)
        self.assertEqual(self.login().status_code, 200)
        with self.app.extensions["registration_store"].connection() as connection:
            connection.execute("UPDATE auth_sessions SET expires_at = 0")
        self.assertIsNone(self.refresh_csrf().get_json()["user"])
        for _ in range(9):
            self.assertEqual(self.login(password="incorrect").status_code, 401)
        self.assertEqual(self.login(password="incorrect").status_code, 429)
        another = create_app(self.path)
        another_client = another.test_client()
        headers = dict(self.headers)
        headers["X-CSRF-Token"] = another_client.get(
            "/api/session", headers=headers,
        ).get_json()["csrf_token"]
        self.assertEqual(another_client.post("/api/login", json={
            "identifier": "auth_user", "password": PASSWORD,
        }, headers=headers).status_code, 429)

    def test_migration_preserves_old_schema_and_is_idempotent(self):
        legacy = Path(self.temp.name) / "legacy.sqlite3"
        with closing(sqlite3.connect(legacy)) as connection, connection:
            connection.execute("""
                CREATE TABLE registrations (
                    id INTEGER PRIMARY KEY, username TEXT COLLATE NOCASE UNIQUE NOT NULL,
                    email TEXT COLLATE NOCASE UNIQUE NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            connection.execute("INSERT INTO registrations (username, email) VALUES (?, ?)",
                               ("legacy", "legacy@example.invalid"))
        store = RegistrationStore(legacy, 1000)
        store.initialize()
        store.initialize()
        user = store.find_account("legacy")
        self.assertEqual(user["email"], "legacy@example.invalid")
        self.assertIsNone(user["password_hash"])

    def test_new_session_rotates_csrf_and_does_not_share_state(self):
        self.assertEqual(self.register().status_code, 201)
        old_token = self.headers["X-CSRF-Token"]
        self.assertEqual(self.login().status_code, 200)
        self.assertNotEqual(old_token, self.headers["X-CSRF-Token"])
        other = self.app.test_client()
        self.assertIsNone(other.get("/api/session", headers=self.headers).get_json()["user"])
        for path in ("/login", "/register", "/account"):
            response = other.get(path, headers=self.headers)
            self.assertEqual(response.status_code, 200)
            response.close()
