import time
import unittest
from unittest.mock import MagicMock

from server.mail_worker import deliver_once
from server import test_private as private_tests


class SecondFactorTests(unittest.TestCase):
    setUpClass = classmethod(private_tests.PrivateTests.setUpClass.__func__)
    setUp = private_tests.PrivateTests.setUp
    tearDown = private_tests.PrivateTests.tearDown
    post = private_tests.PrivateTests.post
    context = private_tests.PrivateTests.context
    email_factor = private_tests.PrivateTests.email_factor
    credential_setup = private_tests.PrivateTests.credential_setup
    register_key = private_tests.PrivateTests.register_key

    def challenge(self):
        response = self.post("/api/security/email/send", {"email": "attacker@example.com"})
        self.assertEqual(response.status_code, 200, response.json)
        with self.store.connection() as connection:
            row = connection.execute("SELECT recipient,code FROM second_factor_outbox").fetchone()
        self.assertEqual(row[0], "private@westlake.edu.cn")
        return {"challenge": response.json["challenge"], "code": row[1]}

    def test_password_and_legacy_cookie_do_not_grant_second_factor(self):
        with self.client.session_transaction(base_url="https://" + self.headers["Host"]) as session:
            session["private_verified_at"] = int(time.time())
        response = self.post("/api/private/authorize", dict(self.context(), password=self.password))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json["code"], "second_factor_required")
        self.assertEqual(self.post("/api/passkeys/register/options",
                                  {"consent": True, "password": self.password}).status_code, 401)

    def test_otp_single_use_and_session_bound_proof(self):
        challenge = self.challenge()
        self.assertEqual(self.post("/api/security/email/verify", challenge).status_code, 200)
        self.assertEqual(self.post("/api/security/email/verify", challenge).status_code, 422)
        token = self.post("/api/private/authorize", self.context()).json["token"]
        with self.store.connection() as connection:
            connection.execute("UPDATE second_factor_proofs SET expires_at=1")
        self.assertEqual(self.post("/api/private/read", {"token": token}).status_code, 409)
        self.assertIsNone(self.client.get("/api/security", headers=self.headers).json["verification"])
        self.assertNotIn("history", self.client.get("/api/private", headers=self.headers).json)

    def test_otp_cannot_cross_browser_or_survive_email_change(self):
        challenge = self.challenge()
        other = self.app.test_client()
        headers = dict(self.headers)
        headers["X-CSRF-Token"] = other.get("/api/session", headers=headers).json["csrf_token"]
        login = other.post("/api/login", headers=headers,
                           json={"identifier": "private@westlake.edu.cn", "password": self.password})
        headers["X-CSRF-Token"] = login.json["csrf_token"]
        self.assertEqual(other.post("/api/security/email/verify", headers=headers,
                                    json=challenge).status_code, 422)
        with self.store.connection() as connection:
            connection.execute("UPDATE registrations SET email='changed@westlake.edu.cn'")
        self.assertEqual(self.post("/api/security/email/verify", challenge).status_code, 422)

    def test_otp_expiry_cooldown_and_guess_limit(self):
        challenge = self.challenge()
        self.assertEqual(self.post("/api/security/email/send", {}).status_code, 429)
        wrong = "000000" if challenge["code"] != "000000" else "111111"
        for _ in range(5):
            self.assertEqual(self.post("/api/security/email/verify",
                                      dict(challenge, code=wrong)).status_code, 422)
        self.assertEqual(self.post("/api/security/email/verify", challenge).status_code, 422)
        with self.store.connection() as connection:
            connection.execute("UPDATE second_factor_challenges SET expires_at=1")
        self.assertEqual(self.post("/api/security/email/verify", challenge).status_code, 422)
        self.assertIsNone(self.client.get("/api/security", headers=self.headers).json["verification"])

    def test_csrf_and_logout_invalidate_challenge(self):
        challenge = self.challenge()
        self.assertEqual(self.client.post("/api/security/email/verify",
                         headers=dict(self.headers, **{"X-CSRF-Token": "wrong"}),
                         json=challenge).status_code, 403)
        self.post("/api/logout", {})
        self.assertEqual(self.client.get("/api/security", headers=self.headers).status_code, 401)

    def test_enrollment_completion_and_removal_recheck_factor(self):
        flow, credential = self.credential_setup()
        with self.store.connection() as connection:
            connection.execute("UPDATE second_factor_proofs SET expires_at=1")
        self.assertEqual(self.post("/api/passkeys/register/complete",
                         {"flow": flow, "credential": credential}).status_code, 422)
        self.assertEqual(self.post("/api/passkeys/remove",
                         {"consent": True, "password": self.password}).status_code, 401)

    def test_legacy_phone_decrypt_normalizes_without_changing_ciphertext(self):
        from server.registration.private_data import decrypt, encrypt
        with self.app.app_context():
            ciphertext = encrypt(1, "phone", "+8613800138000")
            self.assertEqual(decrypt(1, "phone", ciphertext), "13800138000")

    def test_mail_delivery_does_not_include_private_fields(self):
        self.challenge()
        smtp = MagicMock()
        self.assertEqual(deliver_once(self.path, "synthetic", smtp), (1, 0))
        message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
        self.assertEqual(str(message["From"]), "BETA SDC Service <betasdc@163.com>")
        self.assertIn("账户二次验证", message["Subject"])
        self.assertIn("5 分钟", message.get_content())
        with self.store.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM second_factor_outbox").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM second_factor_challenges").fetchone()[0], 1)

    def test_expired_or_revoked_session_mail_is_not_sent(self):
        self.challenge()
        with self.store.connection() as connection:
            connection.execute("DELETE FROM auth_sessions")
        smtp = MagicMock()
        self.assertEqual(deliver_once(self.path, "synthetic", smtp), (0, 0))
        smtp.assert_not_called()

    def test_mail_retry_keeps_challenge_and_does_not_log_recipient(self):
        self.challenge()
        smtp = MagicMock(side_effect=OSError("synthetic sensitive recipient"))
        with self.assertLogs("betasdc-mail", level="WARNING") as logs:
            self.assertEqual(deliver_once(self.path, "synthetic", smtp), (0, 1))
        self.assertNotIn("synthetic sensitive recipient", str(logs.output))
        with self.store.connection() as connection:
            self.assertEqual(connection.execute("SELECT attempts FROM second_factor_outbox").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
