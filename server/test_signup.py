from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server.registration import create_app
from server.mail_worker import deliver_once


class SignupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "signup.sqlite3"
        self.app = create_app(self.path)
        self.store = self.app.extensions["registration_store"]
        self.client = self.app.test_client()
        self.headers = {
            "Host": "account.betasdc.duckdns.org", "X-Forwarded-For": "10.16.125.52",
            "X-Forwarded-Proto": "https",
        }
        self.headers["X-CSRF-Token"] = self.client.get("/api/session", headers=self.headers).json["csrf_token"]
        self.payload = {"username": "new_signup", "real_name": "测试成员", "email": "signup@westlake.edu.cn",
                        "password": "Signup-test-long-phrase"}

    def tearDown(self):
        self.temp.cleanup()

    def post(self, path, payload):
        return self.client.post("/api/" + path, headers=self.headers, json=payload)

    def code(self):
        self.assertEqual(self.post("signup/code", {"email": self.payload["email"]}).status_code, 200)
        with self.store.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM registrations").fetchone()[0], 0)
            return connection.execute("SELECT code FROM signup_mail_outbox").fetchone()[0]

    def test_account_created_only_after_verified_email_and_code_cannot_be_reused(self):
        self.assertEqual(self.post("register", self.payload).status_code, 422)
        code = self.code()
        with self.store.connection() as connection:
            self.assertNotEqual(connection.execute("SELECT code_hash FROM signup_challenges").fetchone()[0], code)
        response = self.post("register", dict(self.payload, email_code=code))
        self.assertEqual(response.status_code, 201)
        self.assertFalse(response.json["verification_required"])
        self.assertIsNotNone(self.store.find_account("new_signup")["email_verified_at"])
        with self.store.connection() as connection:
            for table in ("signup_challenges", "signup_mail_outbox", "mail_outbox"):
                self.assertEqual(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)
        self.assertEqual(self.post("register", dict(self.payload, username="another", email_code=code)).status_code, 422)

    def test_wrong_guesses_persist_and_lock_correct_code(self):
        code = self.code()
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            self.assertEqual(self.post("register", dict(self.payload, email_code=wrong)).status_code, 422)
        self.assertEqual(self.post("register", dict(self.payload, email_code=code)).status_code, 422)
        with self.store.connection() as connection:
            self.assertEqual(connection.execute("SELECT attempts FROM signup_challenges").fetchone()[0], 5)
        self.assertIsNone(self.store.find_account("new_signup"))

    def test_expiry_email_mismatch_session_binding_and_cooldown(self):
        code = self.code()
        self.assertEqual(self.post("signup/code", {"email": self.payload["email"]}).status_code, 429)
        self.assertEqual(self.post("register", dict(self.payload, email="other@westlake.edu.cn",
                                                    email_code=code)).status_code, 422)
        other = self.app.test_client()
        headers = dict(self.headers)
        headers["X-CSRF-Token"] = other.get("/api/session", headers=headers).json["csrf_token"]
        self.assertEqual(other.post("/api/register", headers=headers,
                                    json=dict(self.payload, email_code=code)).status_code, 422)
        self.assertEqual(other.post("/api/signup/code", headers=headers,
                                    json={"email": self.payload["email"]}).status_code, 429)
        with self.store.connection() as connection:
            connection.execute("UPDATE signup_challenges SET expires_at=0")
        self.assertEqual(self.post("register", dict(self.payload, email_code=code)).status_code, 422)
        self.assertIsNone(self.store.find_account("new_signup"))

    def test_csrf_domain_and_transaction_failure(self):
        for email in ("a@example.com", "a@westlake.edu.cn.bad", "a..b@westlake.edu.cn"):
            self.assertEqual(self.post("signup/code", {"email": email}).status_code, 422)
        headers = dict(self.headers, **{"X-CSRF-Token": ""})
        self.assertEqual(self.client.post("/api/signup/code", headers=headers,
                                        json={"email": self.payload["email"]}).status_code, 403)
        with self.store.connection() as connection:
            connection.execute("DROP TABLE signup_mail_outbox")
        self.assertEqual(self.post("signup/code", {"email": self.payload["email"]}).status_code, 503)
        with self.store.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM signup_challenges").fetchone()[0], 0)

    def test_worker_sends_code_then_discards_plaintext(self):
        code = self.code()
        messages = []

        class SMTP:
            def __init__(inner, host, port, **kwargs):
                self.assertEqual((host, port), ("smtp.163.com", 465))
                self.assertTrue(kwargs["context"].check_hostname)
            def __enter__(inner):
                return inner
            def __exit__(inner, *args):
                return False
            def login(inner, *args):
                pass
            def send_message(inner, message, **kwargs):
                messages.append(message)

        self.assertEqual(deliver_once(self.path, "synthetic-credential", SMTP), (1, 0))
        self.assertIn(code, messages[0].get_content())
        self.assertNotIn(self.payload["password"], messages[0].as_string())
        with self.store.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM signup_mail_outbox").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM signup_challenges").fetchone()[0], 1)
        self.assertEqual(self.post("register", dict(self.payload, email_code=code)).status_code, 201)

    def test_concurrent_submission_consumes_code_once(self):
        code = self.code()
        cookie = self.client.get_cookie("__Host-betasdc", domain=self.headers["Host"]).value

        def submit(_):
            client = self.app.test_client()
            client.set_cookie("__Host-betasdc", cookie, domain=self.headers["Host"])
            return client.post("/api/register", headers=self.headers,
                               json=dict(self.payload, email_code=code)).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(sorted(executor.map(submit, range(2))), [201, 422])
        with self.store.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM registrations").fetchone()[0], 1)
