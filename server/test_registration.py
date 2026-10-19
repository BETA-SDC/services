from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server.registration import create_app
from server.registration.repository import (
    RegistrationAlreadyExists, RegistrationLimitReached, RegistrationStore,
)
from server.registration.validation import InvalidRegistration, validate_registration
from server.test_support import signup_payload


class ValidationTests(unittest.TestCase):
    def test_normalization_and_existing_input_rules(self):
        registration = validate_registration({
            "username": " user_demo ", "email": " Demo@WESTLAKE.EDU.CN ",
        })
        self.assertEqual((registration.username, registration.email),
                         ("user_demo", "demo@westlake.edu.cn"))
        for payload, status in (
            ([], 400), ({}, 400),
            ({"username": "a", "email": "demo@example.com"}, 422),
            ({"username": "user", "email": "a..b@example.com"}, 422),
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(InvalidRegistration) as raised:
                    validate_registration(payload)
                self.assertEqual(raised.exception.status, status)

    def test_registration_email_domain_is_exact(self):
        for email in ("user@example.com", "user@students.westlake.edu.cn",
                      "user@westlake.edu.cn.attacker.example", ".user@westlake.edu.cn",
                      "user.@westlake.edu.cn", "u" * 65 + "@westlake.edu.cn"):
            with self.subTest(email=email), self.assertRaises(InvalidRegistration):
                validate_registration({"username": "valid_user", "email": email})


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "registrations.sqlite3"
        self.store = RegistrationStore(self.path, 2)
        self.store.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_existing_data_survives_initialization_and_duplicate_rolls_back(self):
        self.store.add("user_demo", "demo@example.com")
        self.store.initialize()
        with self.assertRaises(RegistrationAlreadyExists):
            self.store.add("USER_DEMO", "other@example.com")
        self.store.add("second", "second@example.com")
        with self.store.connection() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM registrations"
            ).fetchone()[0], 2)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_record_limit_is_atomic_across_concurrent_connections(self):
        def insert(index):
            try:
                self.store.add("user_" + str(index), str(index) + "@example.com")
                return True
            except RegistrationLimitReached:
                return False

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(insert, range(8)))
        self.assertEqual(sum(results), 2)
        with self.store.connection() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM registrations"
            ).fetchone()[0], 2)


class ConfigurationTests(unittest.TestCase):
    def test_custom_config_and_independent_application_instances(self):
        with tempfile.TemporaryDirectory() as folder:
            first = create_app(config={
                "REGISTRATION_DATABASE": Path(folder) / "first.sqlite3",
                "REGISTRATION_DOMAIN": "custom.example",
                "REGISTRATION_NETWORKS": ("192.168.1.0/24",),
                "REGISTRATION_MAX_RECORDS": 1,
            })
            second = create_app(Path(folder) / "second.sqlite3")
            headers = {
                "Host": "custom.example", "X-Forwarded-For": "192.168.1.2",
                "X-Forwarded-Proto": "https", "Origin": "https://custom.example",
            }
            client = first.test_client()
            headers["X-CSRF-Token"] = client.get(
                "/api/session", headers=headers,
            ).get_json()["csrf_token"]
            first_payload = signup_payload(client, headers, first.extensions["registration_store"], {
                "username": "first_user", "email": "first@westlake.edu.cn",
                "password": "Test-password-long-1014",
            })
            self.assertEqual(client.post("/api/register", json=first_payload, headers=headers).status_code, 201)
            second_payload = signup_payload(client, headers, first.extensions["registration_store"], {
                "username": "second_user", "email": "second@westlake.edu.cn",
                "password": "Test-password-long-1014",
            })
            self.assertEqual(client.post("/api/register", json=second_payload, headers=headers).status_code, 503)
            self.assertEqual(second.test_client().get(
                "/api/health", headers=headers,
            ).status_code, 403)
            with second.extensions["registration_store"].connection() as connection:
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM registrations"
                ).fetchone()[0], 0)

    def test_empty_allowlist_denies_access_and_error_responses_keep_headers(self):
        with tempfile.TemporaryDirectory() as folder:
            app = create_app(Path(folder) / "empty.sqlite3", networks=[])
            response = app.test_client().get("/api/health", headers={
                "Host": "account.betasdc.duckdns.org",
                "X-Forwarded-For": "10.17.131.26", "X-Forwarded-Proto": "https",
            })
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")

    def test_storage_failure_is_generic_and_does_not_leak_database_path(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "database.sqlite3"
            app = create_app(path)
            with app.extensions["registration_store"].connection() as connection:
                connection.execute("DROP TABLE registrations")
            client = app.test_client()
            headers = {
                "Host": "account.betasdc.duckdns.org",
                "X-Forwarded-For": "10.17.131.26", "X-Forwarded-Proto": "https",
            }
            headers["X-CSRF-Token"] = client.get(
                "/api/session", headers=headers,
            ).get_json()["csrf_token"]
            payload = signup_payload(client, headers, app.extensions["registration_store"], {
                "username": "test_user", "email": "test@westlake.edu.cn",
                "password": "Test-password-long-1014",
            })
            response = client.post("/api/register", json=payload, headers=headers)
            self.assertEqual(response.status_code, 503)
            self.assertNotIn(str(path), response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
