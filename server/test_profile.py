from pathlib import Path
import tempfile
import time
import unittest

from server.registration import create_app
from server.registration.validation import InvalidRegistration, validate_profile, validate_registration
from server.test_support import signup_payload


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.temp.name) / "profile.sqlite3")
        self.store = self.app.extensions["registration_store"]
        self.client = self.app.test_client()
        self.headers = {"Host": "account.betasdc.duckdns.org", "X-Forwarded-For": "10.16.125.52",
                        "X-Forwarded-Proto": "https"}
        self.headers["X-CSRF-Token"] = self.client.get("/api/session", headers=self.headers).json["csrf_token"]

    def tearDown(self):
        self.temp.cleanup()

    def register(self, email, name="杨轩", nickname=""):
        payload = signup_payload(self.client, self.headers, self.store, {
            "real_name": name, "nickname": nickname, "email": email,
            "password": "Synthetic-long-password-2026",
        })
        return self.client.post("/api/register", headers=self.headers, json=payload)

    def login(self, identifier):
        response = self.client.post("/api/login", headers=self.headers,
                                    json={"identifier": identifier, "password": "Synthetic-long-password-2026"})
        self.assertEqual(response.status_code, 200)
        self.headers["X-CSRF-Token"] = response.json["csrf_token"]
        return response

    def test_name_registration_duplicate_names_and_email_login(self):
        response = self.register("name-one@westlake.edu.cn", nickname="b杰特的恐龙")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["real_name"], "杨轩")
        self.assertEqual(response.json["nickname"], "b杰特的恐龙")
        # Names are not login identifiers and need not be unique.
        with self.store.connection() as connection:
            connection.execute("DELETE FROM signup_challenges")
        self.assertEqual(self.register("name-two@westlake.edu.cn").status_code, 201)
        user = self.login("name-one@westlake.edu.cn").json["user"]
        self.assertEqual(user["real_name"], "杨轩")
        self.assertEqual(user["nickname"], "b杰特的恐龙")
        with self.store.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(DISTINCT username) FROM registrations").fetchone()[0], 2)
        self.assertNotIn("password_hash", user)

    def test_profile_update_is_self_only_and_does_not_change_identity_or_roles(self):
        self.register("self-name@westlake.edu.cn")
        before = self.store.find_account("self-name@westlake.edu.cn")
        self.login("self-name@westlake.edu.cn")
        response = self.client.post("/api/profile", headers=self.headers, json={
            "real_name": "杨轩", "nickname": "小轩", "user_id": 999, "username": "other", "role": "admin"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["user"]["nickname"], "小轩")
        after = self.store.find_account("self-name@westlake.edu.cn")
        for field in ("id", "username", "email", "password_hash", "email_verified_at"):
            self.assertEqual(before[field], after[field])
        self.assertFalse(self.client.get("/api/session", headers=self.headers).json["access"]["is_admin"])
        self.assertEqual(self.client.post("/api/profile", headers=self.headers,
                                         json={"real_name": "杨轩", "nickname": ""}).status_code, 200)
        self.assertEqual(self.store.find_account("self-name@westlake.edu.cn")["nickname"], "")

    def test_profile_requires_login_csrf_and_recent_auth(self):
        self.assertEqual(self.client.post("/api/profile", headers=self.headers,
                                         json={"real_name": "杨轩"}).status_code, 401)
        self.register("secure-name@westlake.edu.cn")
        self.login("secure-name@westlake.edu.cn")
        headers = dict(self.headers, **{"X-CSRF-Token": "bad"})
        self.assertEqual(self.client.post("/api/profile", headers=headers,
                                         json={"real_name": "杨轩"}).status_code, 403)
        with self.client.session_transaction(base_url="https://" + self.headers["Host"]) as session:
            session["auth_time"] = int(time.time()) - 601
        self.assertEqual(self.client.post("/api/profile", headers=self.headers,
                                         json={"real_name": "杨轩"}).status_code, 403)

    def test_student_id_is_self_only_encrypted_profile_without_second_factor(self):
        from server.registration.private_data import encrypt
        endpoint = "/api/profile/student-id"
        self.assertEqual(self.client.get(endpoint, headers=self.headers).status_code, 401)
        self.register("student@westlake.edu.cn")
        self.login("student@westlake.edu.cn")
        user = self.store.find_account("student@westlake.edu.cn")
        with self.app.app_context(), self.store.connection() as connection:
            connection.execute("INSERT OR REPLACE INTO private_fields VALUES(?,?,?,?)",
                               (user["id"], "student_id", encrypt(user["id"], "student_id", "S20260001"), 1))
            connection.execute("INSERT INTO private_fields VALUES(?,?,?,?)",
                               (user["id"], "phone", encrypt(user["id"], "phone", "13800138000"), 1))
        response = self.client.get(endpoint, headers=self.headers)
        self.assertEqual(response.json, {"student_id": "S20260001"})
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        bad = dict(self.headers, **{"X-CSRF-Token": "bad"})
        self.assertEqual(self.client.put(endpoint, headers=bad, json={"student_id": "S20260002"}).status_code, 403)
        for data in ({"student_id": "!"}, {"student_id": "S20260002", "user_id": 99}, {"phone": "13800138000"}):
            self.assertEqual(self.client.put(endpoint, headers=self.headers, json=data).status_code, 422)
        response = self.client.put(endpoint, headers=self.headers, json={"student_id": "S20260002"})
        self.assertEqual(response.json, {"student_id": "S20260002"})
        with self.store.connection() as connection:
            encrypted = connection.execute("SELECT ciphertext FROM private_fields WHERE field='student_id'").fetchone()[0]
            self.assertNotIn("S20260002", encrypted)
            self.assertEqual(connection.execute("SELECT count(*) FROM second_factor_proofs").fetchone()[0], 0)
        for value in ("", None, "   "):
            self.assertEqual(self.client.put(endpoint, headers=self.headers,
                                            json={"student_id": value}).status_code, 422)
        self.assertEqual(self.client.get(endpoint, headers=self.headers).json, {"student_id": "S20260002"})
        with self.store.connection() as connection:
            self.assertEqual({row[0] for row in connection.execute("SELECT field FROM private_fields")},
                             {"phone", "student_id"})
        self.store.add("another-student", "another-student@westlake.edu.cn")
        with self.client.session_transaction(base_url="https://" + self.headers["Host"]) as session:
            session["auth_token"] = self.store.create_session(2, 3600)
        with self.store.connection() as connection:
            connection.execute("UPDATE registrations SET email_verified_at=1 WHERE id=2")
        self.assertEqual(self.client.get(endpoint, headers=self.headers).json, {"student_id": None})

    def test_name_validation_unicode_and_control_characters(self):
        for name in ("杨轩", "Jean-Luc Picard", "O'Connor", "张·三", "Élodie"):
            self.assertEqual(validate_profile(name)[0], name)
        for name in ("", "  ", "1234", "<script>", "A\nB", "杨\u202e轩", "X" * 81):
            with self.subTest(name=name), self.assertRaises(InvalidRegistration):
                validate_profile(name)
        for nickname in ("x" * 41, "a\nb", "a\u202eb"):
            with self.assertRaises(InvalidRegistration):
                validate_profile("杨轩", nickname)
        self.assertEqual(validate_profile(" 杨轩 ", "  个性昵称  "), ("杨轩", "个性昵称"))
        result = validate_registration({"real_name": "杨轩", "email": "name@westlake.edu.cn",
                                        "student_id": "S20260001"})
        self.assertTrue(result.username.startswith("u_"))

    def test_migration_preserves_old_login_and_does_not_guess_real_names(self):
        import sqlite3
        from server.registration.repository import RegistrationStore
        path = Path(self.temp.name) / "legacy-names.sqlite3"
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE registrations(id INTEGER PRIMARY KEY,username TEXT UNIQUE,email TEXT UNIQUE)")
            connection.execute("INSERT INTO registrations VALUES(10,'b杰特的恐龙','legacy-name@westlake.edu.cn')")
        store = RegistrationStore(path, 1000)
        store.initialize()
        store.initialize()
        account = store.find_account("b杰特的恐龙")
        self.assertEqual(account["id"], 10)
        self.assertEqual(account["real_name"], "")
        self.assertEqual(account["nickname"], "b杰特的恐龙")

    def test_registration_requires_explicit_real_name(self):
        response = self.client.post("/api/register", headers=self.headers, json={
            "username": "legacy_only", "email": "no-name@westlake.edu.cn",
            "password": "Synthetic-long-password-2026", "email_code": "123456"})
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
