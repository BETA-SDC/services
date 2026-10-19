from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import smtplib
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server.registration import create_app
from server.mail_worker import deliver_once

PASSWORD = "Testing-long-phrase-2026!"


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "verify.sqlite3"
        self.app = create_app(self.path)
        self.store = self.app.extensions["registration_store"]
        self.client = self.app.test_client()
        self.headers = {
            "Host": "account.betasdc.duckdns.org",
            "X-Forwarded-For": "10.16.125.52", "X-Forwarded-Proto": "https",
            "Origin": "https://account.betasdc.duckdns.org",
        }
        self.headers["X-CSRF-Token"] = self.client.get(
            "/api/session", headers=self.headers,
        ).get_json()["csrf_token"]

    def tearDown(self):
        self.temp.cleanup()

    def post(self, path, payload):
        return self.client.post("/api/" + path, headers=self.headers, json=payload)

    def register(self):
        # Existing pending accounts and their links remain usable after migration.
        from werkzeug.security import generate_password_hash
        self.store.add("verification_user", "verify@westlake.edu.cn", generate_password_hash(PASSWORD))
        with self.store.connection() as connection:
            return connection.execute("SELECT token FROM mail_outbox").fetchone()[0]

    def login(self):
        return self.post("login", {"identifier": "verify@westlake.edu.cn", "password": PASSWORD})

    def test_pending_account_requires_confirmation_and_token_is_single_use(self):
        token = self.register()
        self.assertEqual(self.login().get_json()["code"], "email_verification_required")
        with self.store.connection() as connection:
            digest = connection.execute("SELECT token_hash FROM email_verifications").fetchone()[0]
            self.assertNotEqual(digest, token)
            self.assertEqual(digest, self.store.token_hash(token))
        with self.client.get("/verify#token=" + token, headers=self.headers) as response:
            self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.store.find_account("verification_user")["email_verified_at"])
        response = self.post("verification/confirm", {"token": token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.post("verification/confirm", {"token": token}).status_code, 400)
        response = self.login()
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.get_json()["user"]["email_verified_at"])
        with self.store.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM mail_outbox").fetchone()[0], 0)

    def test_expiry_resend_cooldown_rotation_and_generic_responses(self):
        token = self.register()
        existing = self.post("verification/resend", {"email": "verify@westlake.edu.cn"})
        unknown = self.post("verification/resend", {"email": "unknown@westlake.edu.cn"})
        self.assertEqual(existing.get_json(), unknown.get_json())
        with self.store.connection() as connection:
            self.assertEqual(connection.execute("SELECT token FROM mail_outbox").fetchone()[0], token)
            connection.execute("UPDATE email_verifications SET expires_at=0,created_at=0")
        self.assertEqual(self.post("verification/confirm", {"token": token}).status_code, 400)
        self.assertEqual(self.post("verification/resend", {"email": "verify@westlake.edu.cn"}).status_code, 200)
        with self.store.connection() as connection:
            new = connection.execute("SELECT token FROM mail_outbox").fetchone()[0]
        self.assertNotEqual(new, token)
        self.assertEqual(self.post("verification/confirm", {"token": token}).status_code, 400)
        self.assertEqual(self.post("verification/confirm", {"token": new}).status_code, 200)
        self.assertEqual(self.post("verification/resend", {"email": "verify@westlake.edu.cn"}).get_json(),
                         unknown.get_json())

    def test_cross_site_email_navigation_and_csrf_are_distinct(self):
        token = self.register()
        headers = {key: value for key, value in self.headers.items() if key != "Origin"}
        headers.update({"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate",
                        "Sec-Fetch-Dest": "document"})
        with self.client.get("/verify", headers=headers) as response:
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/session", headers=headers).status_code, 403)
        self.assertEqual(self.client.post("/api/verification/confirm", headers=headers,
                                         json={"token": token}).status_code, 403)
        headers = dict(self.headers, **{"X-CSRF-Token": ""})
        self.assertEqual(self.client.post("/api/verification/confirm", headers=headers,
                                         json={"token": token}).status_code, 403)
        self.assertEqual(self.client.post("/api/verification/resend", headers=headers,
                                         json={"email": "verify@westlake.edu.cn"}).status_code, 403)

    def test_registration_and_mail_enqueue_rollback_together(self):
        import sqlite3
        from werkzeug.security import generate_password_hash
        with self.store.connection() as connection:
            connection.execute("DROP TABLE mail_outbox")
        with self.assertRaises(sqlite3.Error):
            self.store.add("rollback_user", "rollback@westlake.edu.cn", generate_password_hash(PASSWORD))
        with self.store.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM registrations").fetchone()[0], 0)

    def test_resend_rate_limit_persists_and_legacy_records_are_not_claimable(self):
        self.store.add("legacy_user", "legacy@westlake.edu.cn")
        for _ in range(10):
            self.assertEqual(self.post("verification/resend", {
                "email": "legacy@westlake.edu.cn",
            }).status_code, 200)
        self.assertEqual(self.post("verification/resend", {
            "email": "legacy@westlake.edu.cn",
        }).status_code, 429)
        with self.store.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM mail_outbox").fetchone()[0], 0)
        another = create_app(self.path).test_client()
        headers = dict(self.headers)
        headers["X-CSRF-Token"] = another.get("/api/session", headers=headers).get_json()["csrf_token"]
        self.assertEqual(another.post("/api/verification/resend", headers=headers,
                                     json={"email": "legacy@westlake.edu.cn"}).status_code, 429)

    def test_worker_delivery_retry_expiry_and_message_contents(self):
        token = self.register()
        messages = []

        class SMTP:
            def __init__(inner, host, port, timeout, context):
                self.assertEqual((host, port), ("smtp.163.com", 465))
                self.assertTrue(context.check_hostname)
            def __enter__(inner):
                return inner
            def __exit__(inner, *args):
                return False
            def login(inner, username, password):
                self.assertEqual((username, password), ("betasdc@163.com", "test-credential"))
            def send_message(inner, message, **kwargs):
                messages.append(message)

        class FailingSMTP(SMTP):
            def login(inner, username, password):
                raise smtplib.SMTPAuthenticationError(535, b"test-credential-private")

        with self.assertLogs("betasdc-mail", level="WARNING") as logs:
            self.assertEqual(deliver_once(self.path, "test-credential", FailingSMTP), (0, 1))
        self.assertNotIn("test-credential", "".join(logs.output))
        self.assertEqual(deliver_once(self.path, "test-credential", SMTP), (0, 0))
        with self.store.connection() as connection:
            connection.execute("UPDATE mail_outbox SET next_attempt=0")
        self.assertEqual(deliver_once(self.path, "test-credential", SMTP), (1, 0))
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["To"], "verify@westlake.edu.cn")
        self.assertIn("/verify#token=" + token, messages[0].get_content())
        self.assertNotIn(PASSWORD, messages[0].as_string())
        self.assertEqual(deliver_once(self.path, "test-credential", SMTP), (0, 0))
        with self.store.connection() as connection:
            connection.execute("UPDATE email_verifications SET created_at=0")
        self.post("verification/resend", {"email": "verify@westlake.edu.cn"})
        with self.store.connection() as connection:
            connection.execute("UPDATE email_verifications SET expires_at=0")
        self.assertEqual(deliver_once(self.path, "test-credential", SMTP), (0, 0))
        with self.store.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM mail_outbox").fetchone()[0], 0)

    def test_malformed_verification_requests_and_parallel_resend(self):
        self.register()
        for path in ("verification/resend", "verification/confirm"):
            for body in ([], {}, None):
                response = self.post(path, body)
                self.assertIn(response.status_code, (400, 415))
        from server.registration.verification import enqueue_verification
        with self.store.connection() as connection:
            connection.execute("UPDATE email_verifications SET created_at=0")
            user_id = connection.execute("SELECT id FROM registrations").fetchone()[0]

        def enqueue(_):
            with self.store.connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                return enqueue_verification(connection, user_id, "verify@westlake.edu.cn")

        with ThreadPoolExecutor(max_workers=4) as executor:
            self.assertEqual(sum(executor.map(enqueue, range(4))), 1)
