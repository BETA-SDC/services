import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

from server.registration import create_app

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.exceptions import InvalidTag
from fido2 import cbor
from fido2.cose import ES256
from fido2.utils import websafe_encode
from fido2.webauthn import Aaguid, AttestedCredentialData, AuthenticatorData
from werkzeug.security import generate_password_hash

from server.registration.auth import HASH_METHOD
from server.registration.private_data import POLICY, decrypt, encrypt
from server.registration.private_validation import validate_document, validate_private
from server.registration.validation import InvalidRegistration


class PrivateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = "Synthetic-private-password-2026"
        cls.password_hash = generate_password_hash(cls.password, method=HASH_METHOD)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "private.sqlite3"
        self.app = create_app(self.path)
        self.store = self.app.extensions["registration_store"]
        self.store.add("private-test", "private@westlake.edu.cn", self.password_hash)
        with self.store.connection() as connection:
            connection.execute("UPDATE registrations SET email_verified_at=1")
        self.client = self.app.test_client()
        self.headers = {"Host": "account.betasdc.duckdns.org", "X-Forwarded-For": "10.16.125.52",
                        "X-Forwarded-Proto": "https"}
        self.headers["X-CSRF-Token"] = self.client.get("/api/session", headers=self.headers).json["csrf_token"]
        result = self.post("/api/login", {"identifier": "private@westlake.edu.cn", "password": self.password})
        self.headers["X-CSRF-Token"] = result.json["csrf_token"]

    def tearDown(self):
        self.temp.cleanup()

    def post(self, url, value):
        return self.client.post(url, headers=self.headers, json=value)

    def context(self, purpose="read", fields=None):
        return {"purpose": purpose, "fields": fields or ["student_id"], "consent": True, "policy": POLICY}

    def grant(self, purpose="read", fields=None):
        self.email_factor()
        response = self.post("/api/private/authorize",
                             self.context(purpose, fields))
        self.assertEqual(response.status_code, 200, response.json)
        return response.json["token"]

    def email_factor(self):
        if self.client.get("/api/security", headers=self.headers).json["verification"]:
            return
        start = self.post("/api/security/email/send", {})
        self.assertEqual(start.status_code, 200, start.json)
        with self.store.connection() as connection:
            code = connection.execute("SELECT code FROM second_factor_outbox WHERE challenge_hash=?",
                                      (self.store.token_hash(start.json["challenge"]),)).fetchone()[0]
        verified = self.post("/api/security/email/verify", {"challenge": start.json["challenge"], "code": code})
        self.assertEqual(verified.status_code, 200, verified.json)

    def save(self, values):
        return self.post("/api/private/edit", {"token": self.grant("edit", list(values)), "values": values})

    def test_encryption_and_no_plaintext_in_status_or_database(self):
        self.assertEqual(self.save({"student_id": "S202612345", "phone": "13800138000"}).status_code, 200)
        state = self.client.get("/api/private", headers=self.headers)
        self.assertTrue(state.json["fields"]["student_id"]["saved"])
        self.assertNotIn("S202612345", state.get_data(as_text=True))
        self.assertNotIn(b"S202612345", self.path.read_bytes())
        with self.store.connection() as connection:
            ciphertext = connection.execute("SELECT ciphertext FROM private_fields WHERE field='student_id'").fetchone()[0]
        with self.app.app_context():
            self.assertEqual(decrypt(1, "student_id", ciphertext), "S202612345")
            with self.assertRaises(InvalidTag):
                decrypt(2, "student_id", ciphertext)
            with self.assertRaises(InvalidTag):
                decrypt(1, "phone", ciphertext)
            self.assertNotEqual(encrypt(1, "student_id", "same"), encrypt(1, "student_id", "same"))
        self.assertEqual(self.path.with_suffix(".private-key").stat().st_mode & 0o777, 0o600)
        self.assertNotEqual(self.path.with_suffix(".private-key").read_bytes(),
                            self.path.with_suffix(".session-key").read_bytes())

    def test_token_single_use_field_scope_and_operation_binding(self):
        self.save({"student_id": "S123456", "phone": "13800138000"})
        token = self.grant()
        self.assertEqual(self.post("/api/private/delete", {"token": token}).status_code, 409)
        result = self.post("/api/private/read", {"token": token})
        self.assertEqual(result.json["values"], {"student_id": "S123456"})
        self.assertEqual(self.post("/api/private/read", {"token": token}).status_code, 409)
        token = self.grant("edit")
        self.assertEqual(self.post("/api/private/edit", {"token": token, "values": {"phone": "13900139000"}}).status_code, 422)
        self.assertEqual(self.post("/api/private/edit", {"token": token, "values": {"student_id": "S999999"}}).status_code, 409)

    def test_expiry_and_different_session(self):
        token = self.grant()
        with self.store.connection() as connection:
            connection.execute("UPDATE private_consents SET expires_at=1")
        self.assertEqual(self.post("/api/private/read", {"token": token}).status_code, 409)
        token = self.grant()
        with self.client.session_transaction(base_url="https://" + self.headers["Host"]) as session:
            old = session["auth_token"]
            session["auth_token"] = self.store.create_session(1, 3600)
        self.assertEqual(self.post("/api/private/read", {"token": token}).status_code, 409)
        self.store.revoke_session(old)

    def test_explicit_consent_policy_password_and_csrf(self):
        for extra in ({"consent": False}, {"policy": "old"}, {"fields": ["password_hash"]},
                      {"fields": ["phone", "phone"]}, {"purpose": "share"}, {"fields": []}):
            payload = dict(self.context(), password=self.password)
            payload.update(extra)
            self.assertEqual(self.post("/api/private/authorize", payload).status_code, 422)
        self.assertEqual(self.post("/api/private/authorize", dict(self.context(), password="wrong")).status_code, 401)
        response = self.client.post("/api/private/authorize",
                                    headers=dict(self.headers, **{"X-CSRF-Token": "bad"}),
                                    json=dict(self.context(), password=self.password))
        self.assertEqual(response.status_code, 403)

    def test_recent_private_verification_can_authorize_save_but_expires(self):
        self.assertEqual(self.post("/api/private/authorize", self.context("edit")).status_code, 401)
        self.grant("read")
        response = self.post("/api/private/authorize", self.context("edit"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.post("/api/private/edit", {"token": response.json["token"],
                                                       "values": {"student_id": "S12345"}}).status_code, 200)
        with self.store.connection() as connection:
            connection.execute("UPDATE second_factor_proofs SET expires_at=1")
        self.assertEqual(self.post("/api/private/authorize", self.context("edit")).status_code, 401)

    def test_delete_clears_field_and_revokes_unused_grants(self):
        self.save({"student_id": "S123456"})
        old = self.grant()
        token = self.grant("delete")
        self.assertEqual(self.post("/api/private/delete", {"token": token}).status_code, 200)
        self.assertEqual(self.post("/api/private/read", {"token": old}).status_code, 409)
        self.assertEqual(self.client.get("/api/private", headers=self.headers).json["fields"], {})
        self.assertEqual(self.save({"phone": "13800138000"}).status_code, 200)
        self.assertEqual(self.save({"phone": ""}).status_code, 200)
        self.assertEqual(self.client.get("/api/private", headers=self.headers).json["fields"], {})

    def test_key_missing_or_permissions_fail_closed(self):
        self.save({"student_id": "S123456"})
        key = self.path.with_suffix(".private-key")
        key.chmod(0o644)
        with self.assertRaises(PermissionError):
            create_app(self.path)
        key.unlink()
        with self.assertRaises(RuntimeError):
            create_app(self.path)
        self.assertFalse(key.exists())

    def test_unauthenticated_and_user_id_cannot_read_another_account(self):
        self.save({"student_id": "S123456"})
        self.store.add("another", "another@westlake.edu.cn", self.password_hash)
        token = self.grant()
        self.post("/api/logout", {})
        self.assertEqual(self.client.get("/api/private", headers=self.headers).status_code, 401)
        self.assertEqual(self.post("/api/private/read", {"token": token}).status_code, 403)  # cleared CSRF
        state = self.client.get("/api/session", headers=self.headers)
        self.headers["X-CSRF-Token"] = state.json["csrf_token"]
        with self.store.connection() as connection:
            connection.execute("UPDATE registrations SET email_verified_at=1")
        state = self.post("/api/login", {"identifier": "another@westlake.edu.cn", "password": self.password})
        self.headers["X-CSRF-Token"] = state.json["csrf_token"]
        self.assertEqual(self.post("/api/private/read", {"token": token, "user_id": 1}).status_code, 409)
        self.assertEqual(self.client.get("/api/private", headers=self.headers).json["fields"], {})

    def test_same_email_case_insensitive_constraint(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.connection() as connection:
                connection.execute("INSERT INTO registrations(username,email) VALUES('duplicate','PRIVATE@WESTLAKE.EDU.CN')")
        self.assertEqual(self.store.find_account("PRIVATE@WESTLAKE.EDU.CN")["username"], "private-test")

    def test_policy_has_honest_scope_and_is_public(self):
        result = self.client.get("/privacy", headers=self.headers)
        self.assertEqual(result.status_code, 200)
        text = result.get_data(as_text=True)
        for item in ("AES-256-GCM", "不是端到端加密", "betasdc@163.com", "60 秒", "单独同意"):
            self.assertIn(item, text)
        result.close()

    def test_concurrent_redemption_only_one_succeeds(self):
        from concurrent.futures import ThreadPoolExecutor
        token = self.grant()
        cookie = self.client.get_cookie("__Host-betasdc", domain=self.headers["Host"])
        def redeem(_):
            client = self.app.test_client()
            client.set_cookie("__Host-betasdc", cookie.value, domain=self.headers["Host"])
            return client.post("/api/private/read", headers=self.headers, json={"token": token}).status_code
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(sorted(executor.map(redeem, range(2))), [200, 409])

    def credential_setup(self, origin=None, verified=True):
        self.email_factor()
        start = self.post("/api/passkeys/register/options", {"password": self.password, "consent": True})
        self.assertEqual(start.status_code, 200, start.json)
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = b"synthetic-private-credential"
        data = AttestedCredentialData.create(Aaguid.NONE, self.credential_id,
                                             ES256.from_cryptography_key(self.key.public_key()))
        auth_data = AuthenticatorData.create(hashlib.sha256(self.headers["Host"].encode()).digest(),
                                            0x45 if verified else 0x41, 0, data)
        client_data = json.dumps({"type": "webauthn.create",
                                  "challenge": start.json["options"]["publicKey"]["challenge"],
                                  "origin": origin or "https://" + self.headers["Host"],
                                  "crossOrigin": False}).encode()
        response = {"id": websafe_encode(self.credential_id), "rawId": websafe_encode(self.credential_id),
                    "type": "public-key", "response": {
                        "clientDataJSON": websafe_encode(client_data),
                        "attestationObject": websafe_encode(cbor.encode(
                            {"fmt": "none", "authData": bytes(auth_data), "attStmt": {}}))}}
        return start.json["flow"], response

    def assertion(self, counter=1, origin=None, verified=True, login=False, handle=None, stepup=False):
        from server.registration.passkeys import user_handle
        start = self.post("/api/passkeys/login/options" if login else
                          "/api/passkeys/stepup/options" if stepup else "/api/passkeys/verify/options",
                          {} if login else {"consent": True} if stepup else self.context())
        self.assertEqual(start.status_code, 200, start.json)
        auth_data = AuthenticatorData.create(hashlib.sha256(self.headers["Host"].encode()).digest(),
                                            5 if verified else 1, counter)
        client_data = json.dumps({"type": "webauthn.get",
                                  "challenge": start.json["options"]["publicKey"]["challenge"],
                                  "origin": origin or "https://" + self.headers["Host"],
                                  "crossOrigin": False}).encode()
        signature = self.key.sign(bytes(auth_data) + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
        with self.app.app_context():
            login_handle = user_handle(1)
        response = {"id": websafe_encode(self.credential_id), "rawId": websafe_encode(self.credential_id),
                    "type": "public-key", "response": {"clientDataJSON": websafe_encode(client_data),
                        "authenticatorData": websafe_encode(auth_data), "signature": websafe_encode(signature),
                        "userHandle": websafe_encode(handle or login_handle) if login else None}}
        return start.json["flow"], response

    def register_key(self):
        flow, credential = self.credential_setup()
        response = self.post("/api/passkeys/register/complete", {"flow": flow, "credential": credential})
        self.assertEqual(response.status_code, 200, response.json)

    def test_real_cryptographic_passkey_signature_and_single_use(self):
        self.register_key()
        flow, credential = self.assertion()
        response = self.post("/api/passkeys/verify/complete", {"flow": flow, "credential": credential})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.post("/api/private/read", {"token": response.json["token"]}).status_code, 200)
        self.assertEqual(self.post("/api/passkeys/verify/complete", {"flow": flow, "credential": credential}).status_code, 409)

    def test_passkey_stepup_proof_rechecks_credential_and_expiry(self):
        self.register_key()
        with self.store.connection() as connection:
            connection.execute("DELETE FROM second_factor_proofs")
        flow, credential = self.assertion(stepup=True)
        result = self.post("/api/passkeys/stepup/complete", {"flow": flow, "credential": credential})
        self.assertEqual(result.status_code, 200, result.json)
        state = self.client.get("/api/security", headers=self.headers).json
        self.assertEqual(state["verification"]["method"], "passkey")
        self.assertEqual(self.post("/api/private/authorize", self.context()).status_code, 200)
        with self.store.connection() as connection:
            connection.execute("DELETE FROM passkeys")
        self.assertEqual(self.post("/api/private/authorize", self.context()).status_code, 401)

    def test_passkeys_reject_origin_unverified_user_invalid_signatures_and_counter_replay(self):
        for origin, uv in (("https://evil.example", True), (None, False)):
            flow, credential = self.credential_setup(origin=origin, verified=uv)
            self.assertEqual(self.post("/api/passkeys/register/complete",
                                      {"flow": flow, "credential": credential}).status_code, 422)
        self.register_key()
        for origin, uv in (("https://evil.example", True), (None, False)):
            flow, credential = self.assertion(origin=origin, verified=uv)
            self.assertEqual(self.post("/api/passkeys/verify/complete",
                                      {"flow": flow, "credential": credential}).status_code, 422)
        flow, credential = self.assertion()
        credential["response"]["signature"] = websafe_encode(b"bad-signature")
        self.assertEqual(self.post("/api/passkeys/verify/complete", {"flow": flow, "credential": credential}).status_code, 422)
        flow, credential = self.assertion(counter=2)
        self.assertEqual(self.post("/api/passkeys/verify/complete", {"flow": flow, "credential": credential}).status_code, 200)
        flow, credential = self.assertion(counter=2)
        self.assertEqual(self.post("/api/passkeys/verify/complete", {"flow": flow, "credential": credential}).status_code, 422)

    def test_passkey_options_require_password_or_consent_and_remove_requires_password(self):
        self.assertEqual(self.post("/api/passkeys/register/options", {"consent": True, "password": "bad"}).status_code, 401)
        self.assertEqual(self.post("/api/passkeys/register/options", {"password": self.password}).status_code, 422)
        self.assertEqual(self.post("/api/passkeys/verify/options", self.context()).status_code, 409)
        self.register_key()
        self.assertEqual(self.post("/api/passkeys/remove", {"consent": True, "password": "wrong"}).status_code, 401)
        self.assertEqual(self.post("/api/passkeys/remove", {"consent": True, "password": self.password}).status_code, 200)
        self.assertEqual(self.client.get("/api/private", headers=self.headers).json["passkey_count"], 0)

    def logout(self):
        result = self.post("/api/logout", {})
        self.headers["X-CSRF-Token"] = result.json["csrf_token"]

    def test_passkey_login_maps_credential_to_account_and_rejects_replay(self):
        self.register_key()
        self.logout()
        flow, credential = self.assertion(login=True)
        response = self.post("/api/passkeys/login/complete", {"flow": flow, "credential": credential})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["user"]["email"], "private@westlake.edu.cn")
        self.headers["X-CSRF-Token"] = response.json["csrf_token"]
        self.logout()
        self.assertEqual(self.post("/api/passkeys/login/complete",
                                  {"flow": flow, "credential": credential}).status_code, 409)

    def test_passkey_login_rejects_wrong_origin_handle_uv_and_cross_browser(self):
        self.register_key()
        self.logout()
        for values in ({"origin": "https://evil.example"}, {"verified": False}, {"handle": b"wrong-user"}):
            flow, credential = self.assertion(login=True, **values)
            response = self.post("/api/passkeys/login/complete", {"flow": flow, "credential": credential})
            self.assertEqual(response.status_code, 401, response.json)
        flow, credential = self.assertion(login=True)
        other = self.app.test_client()
        headers = dict(self.headers)
        headers["X-CSRF-Token"] = other.get("/api/session", headers=headers).json["csrf_token"]
        response = other.post("/api/passkeys/login/complete", headers=headers,
                              json={"flow": flow, "credential": credential})
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(other.get("/api/session", headers=headers).json["user"])

    def test_private_page_route_contains_independent_editor(self):
        response = self.client.get("/account/privacy", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="private-page"', response.get_data(as_text=True))
        response.close()


class PrivateValidationTests(unittest.TestCase):
    def document(self, kind, number):
        return validate_document({"type": kind, "number": number, "country": "CN"})

    def test_mainland_checksum_date_and_residence_prefix(self):
        self.assertEqual(self.document("cn_id", "11010519491231002X")["validation"], "checksum")
        for value in ("110105194912310021", "11010519491331002X", "11010529991231002X", "123"):
            with self.assertRaises(InvalidRegistration):
                self.document("cn_id", value)
        with self.assertRaises(InvalidRegistration):
            self.document("hmt_residence", "11010519491231002X")
        from server.registration.private_validation import CN_WEIGHTS
        prefix = "81000020000101001"
        value = prefix + "10X98765432"[sum(int(n) * w for n, w in zip(prefix, CN_WEIGHTS)) % 11]
        self.assertEqual(self.document("hmt_residence", value)["validation"], "checksum")

    def test_hong_kong_taiwan_checksums_and_other_formats(self):
        from server.registration.private_validation import TW_CODES
        self.assertEqual({key: TW_CODES[key] for key in ("I", "J", "O", "W", "X", "Z")},
                         {"I": 34, "J": 18, "O": 35, "W": 32, "X": 30, "Z": 33})
        for kind, value in (("hk_id", "A123456(3)"), ("tw_id", "A123456789")):
            self.assertEqual(self.document(kind, value)["validation"], "checksum")
        for kind, value in (("hk_id", "A123456(4)"), ("hk_id", "A123456(3"),
                            ("tw_id", "A123456788"), ("tw_id", "A323456789")):
            with self.assertRaises(InvalidRegistration):
                self.document(kind, value)
        for kind, value in (("hk_macao_pass", "H12345678"), ("macao_id", "12345678"),
                            ("tw_pass", "12345678"), ("passport", "AB1234567"),
                            ("foreign_pr", "912345678901234567")):
            self.assertEqual(self.document(kind, value)["validation"], "format_only")
        with self.assertRaises(InvalidRegistration):
            self.document("hk_macao_pass", "H1234")

    def test_phone_student_formats_and_null_deletion(self):
        result = validate_private({"phone": "13800138000", "student_id": "S20260001"})
        self.assertEqual(result["phone"], "13800138000")
        for value in ("+8613800138000", "+86 138-0013-8000", "138 0013 8000",
                      "12800138000", "1380013800", "138001380000", "+12025550123",
                      "１３８００１３８０００", "+8601234567890"):
            with self.assertRaises(InvalidRegistration):
                validate_private({"phone": value})
        for value in ("12", "<script>", "S\n123", "S" * 33):
            with self.assertRaises(InvalidRegistration):
                validate_private({"student_id": value})
        self.assertEqual(validate_private({"document": None, "student_id": ""}), {"document": None, "student_id": None})


if __name__ == "__main__":
    unittest.main()
