from pathlib import Path
import sys
import struct
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from server.webapp import create_app
from server.test_support import signup_payload


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "test.sqlite3"
        self.app = create_app(self.db, max_records=2, networks=("10.16.112.0/20", "10.17.128.0/18"))
        self.client = self.app.test_client()
        self.headers = {
            "Host": "account.betasdc.duckdns.org", "X-Forwarded-For": "10.17.131.26",
            "X-Forwarded-Proto": "https",
        }
        self.headers["X-CSRF-Token"] = self.client.get(
            "/api/session", headers=self.headers,
        ).get_json()["csrf_token"]

    def tearDown(self):
        self.temp.cleanup()

    def register(self, payload, headers=None):
        headers = headers or self.headers
        payload = signup_payload(self.client, headers, self.app.extensions["registration_store"], payload)
        return self.client.post("/api/register", json=payload, headers=headers)

    def test_registration_duplicates_and_cap(self):
        first = {"username": "server_demo", "email": "server@westlake.edu.cn",
                 "password": "Test-password-long-1014"}
        self.assertEqual(self.register(first).status_code, 201)
        second = {"username": "server_demo2", "email": "server2@westlake.edu.cn",
                  "password": "Test-password-long-1014"}
        self.assertEqual(self.register(second).status_code, 201)
        third = {"username": "server_demo3", "email": "server3@westlake.edu.cn",
                 "password": "Test-password-long-1014"}
        self.assertEqual(self.register(third).status_code, 503)
        self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)

    def test_duplicate_registration_requires_email_ownership_and_preserves_account(self):
        payload = {"username": "existing_user", "email": "existing@westlake.edu.cn",
                   "password": "Test-password-long-1014"}
        self.assertEqual(self.register(payload).status_code, 201)
        self.assertEqual(self.register(payload).status_code, 409)

    def test_network_host_origin_and_proxy_restrictions(self):
        for key, value in (
            ("X-Forwarded-For", "8.8.8.8"), ("Host", "attacker.example"),
            ("Host", "betasdc.duckdns.org"), ("Origin", "https://betasdc.duckdns.org"),
            ("X-Forwarded-Proto", "http"), ("Origin", "https://attacker.example"),
            ("X-Forwarded-For", "10.17.131.26, 8.8.8.8"),
        ):
            headers = dict(self.headers, **{key: value})
            self.assertEqual(self.client.get("/api/health", headers=headers).status_code, 403)
        headers = dict(self.headers, **{"X-Forwarded-For": "10.16.125.52"})
        self.assertEqual(self.client.get("/api/health", headers=headers).status_code, 200)
        headers = dict(self.headers, Origin="https://account.betasdc.duckdns.org")
        self.assertEqual(self.register({
            "username": "origin_demo", "email": "origin@westlake.edu.cn",
            "password": "Test-password-long-1014",
        }, headers=headers).status_code, 201)
        self.assertEqual(self.client.get("/api/health").status_code, 403)

    def test_validation_body_limit_and_security_headers(self):
        for body in ({}, [], {"username": "<script>", "email": "a@example.com"}):
            self.assertIn(self.client.post("/api/register", json=body, headers=self.headers).status_code, (400, 422))
        self.assertEqual(self.client.post(
            "/api/register", data="x" * 4097, content_type="application/json",
            headers=self.headers,
        ).status_code, 413)
        self.assertEqual(self.client.post(
            "/api/register", data="[" * 1500 + "]" * 1500, content_type="application/json",
            headers=self.headers,
        ).status_code, 400)
        response = self.client.get("/api/health", headers=self.headers)
        self.assertIn("max-age", response.headers["Strict-Transport-Security"])
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        response = self.client.get("/", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        response.close()
        for path in ("/registrations.sqlite3", "/api/users", "/../account.conf"):
            self.assertEqual(self.client.get(path, headers=self.headers).status_code, 404)

    def test_default_avatar_is_local_and_access_controlled(self):
        with self.client.get("/default-avatar.png", headers=self.headers) as response:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "image/png")
            self.assertEqual(response.data, (Path(self.app.config["REGISTRATION_ASSETS"]) / "default-avatar.png").read_bytes())
            self.assertTrue(response.data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(self.client.get("/default-avatar.png").status_code, 403)
        with self.client.get("/account", headers=self.headers) as response:
            self.assertIn('src="/default-avatar.png"', response.get_data(as_text=True))

    def test_brand_logo_assets_and_access_policy(self):
        for path in ("/brand-logo.png", "/mark.png"):
            with self.client.get(path, headers=self.headers) as response:
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, "image/png")
                self.assertTrue(response.data.startswith(b"\x89PNG\r\n\x1a\n"))
                width, height = struct.unpack(">II", response.data[16:24])
                if path == "/brand-logo.png":
                    self.assertGreater(width, height)
                else:
                    self.assertEqual(width, height)
            self.assertEqual(self.client.get(path).status_code, 403)
        for path in ("/favicon.ico", "/beta-favicon-v2.ico"):
            with self.client.get(path, headers=self.headers) as response:
                self.assertEqual(response.status_code, 200)
                self.assertEqual(struct.unpack("<HHH", response.data[:6]), (0, 1, 6))
                sizes = {tuple(response.data[6 + index * 16:8 + index * 16]) for index in range(6)}
                self.assertIn((16, 16), sizes)
                self.assertIn((32, 32), sizes)
            self.assertEqual(self.client.get(path).status_code, 403)
        with self.client.get("/", headers=self.headers) as response:
            self.assertIn('href="/beta-favicon-v2.ico"', response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
