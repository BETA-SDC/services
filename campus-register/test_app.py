import http.client
import json
import socket
import sqlite3
import ssl
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from app import DOMAIN, RegistrationServer


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "test.sqlite3"
        self.server = RegistrationServer(("127.0.0.1", 0), self.db, ["127.0.0.1/32"])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, method, path, payload=None, headers=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        body = json.dumps(payload) if payload is not None else raw
        connection.request(
            method, path, body=body,
            headers=headers or {"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        status, result = response.status, response.read()
        connection.close()
        return status, result

    def test_persistent_registration_and_duplicates(self):
        payload = {"username": "demo_user", "email": "demo@example.com"}
        self.assertEqual(self.request("POST", "/api/register", payload)[0], 201)
        with sqlite3.connect(str(self.db)) as connection:
            self.assertEqual(
                connection.execute("SELECT username, email FROM registrations").fetchone(),
                ("demo_user", "demo@example.com"),
            )
        self.assertEqual(self.request(
            "POST", "/api/register", {"username": "DEMO_USER", "email": "other@example.com"}
        )[0], 409)
        self.assertEqual(self.request(
            "POST", "/api/register", {"username": "other_user", "email": "DEMO@example.com"}
        )[0], 409)

    def test_validation(self):
        for payload in [
            {"username": "<script>", "email": "demo@example.com"},
            {"username": "ok", "email": "bad"},
            {"username": "ok", "email": "a..b@example.com"},
            {"username": [], "email": "demo@example.com"},
            ["not", "an", "object"],
        ]:
            self.assertIn(self.request("POST", "/api/register", payload)[0], (400, 422))
        self.assertEqual(self.request("POST", "/api/register", raw="{")[0], 400)

    def test_access_and_origin(self):
        self.assertEqual(self.request("GET", "/api/health")[0], 200)
        self.assertEqual(self.request(
            "GET", "/", headers={"Host": "attacker.example"}
        )[0], 403)
        self.assertEqual(self.request(
            "POST", "/api/register", {"username": "test", "email": "t@example.com"},
            {"Content-Type": "application/json", "Origin": "http://attacker.example"},
        )[0], 403)
        self.server.allowed_networks = []
        self.assertEqual(self.request("GET", "/")[0], 403)

    def test_limits_and_private_data(self):
        self.assertEqual(self.request("GET", "/api/registrations")[0], 404)
        self.assertEqual(self.request("GET", "/../../app.py")[0], 404)
        self.assertEqual(self.request(
            "POST", "/api/register", raw="x" * 4097
        )[0], 413)
        for _ in range(9):
            self.request("POST", "/api/register", {})
        self.assertEqual(self.request("POST", "/api/register", {})[0], 429)
        self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)

    def test_assets(self):
        self.assertEqual(self.request("GET", "/")[0], 200)
        status, image = self.request("GET", "/mark.png")
        self.assertEqual(status, 200)
        self.assertTrue(image.startswith(b"\x89PNG"))
        self.assertEqual(self.request("GET", "/main.js")[0], 200)


class TLSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.cert = Path(cls.temp.name) / "certificate.pem"
        cls.key = Path(cls.temp.name) / "key.pem"
        subprocess.run([
            "openssl", "req", "-x509", "-nodes", "-newkey", "rsa:2048",
            "-days", "1", "-subj", "/CN=localhost",
            "-addext", "subjectAltName=IP:127.0.0.1",
            "-keyout", str(cls.key), "-out", str(cls.cert),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.database = tempfile.TemporaryDirectory()
        self.server = RegistrationServer(
            ("127.0.0.1", 0), Path(self.database.name) / "data.sqlite3",
            ["127.0.0.1/32"], str(self.cert), str(self.key),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.database.cleanup()

    def request(self, origin=None):
        context = ssl.create_default_context(cafile=str(self.cert))
        connection = http.client.HTTPSConnection(
            "127.0.0.1", self.server.server_port, context=context, timeout=3
        )
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        connection.request("POST", "/api/register", json.dumps({
            "username": "tls_demo", "email": "tls@example.com"
        }), headers)
        response = connection.getresponse()
        status, hsts = response.status, response.getheader("Strict-Transport-Security")
        response.read()
        connection.close()
        return status, hsts

    def test_trusted_tls_registration(self):
        origin = "https://127.0.0.1:{}".format(self.server.server_port)
        self.assertEqual(self.request(origin), (201, "max-age=86400"))
        self.assertEqual(self.server.tls_context().minimum_version, ssl.TLSVersion.TLSv1_2)

    def test_plaintext_origin_rejected(self):
        origin = "http://127.0.0.1:{}".format(self.server.server_port)
        self.assertEqual(self.request(origin)[0], 403)

    def test_certificate_reload(self):
        previous = self.server.tls_context()
        self.cert.touch()
        self.assertIsNot(self.server.tls_context(), previous)
        self.assertEqual(self.request()[0], 201)

    def test_standard_port_hostname(self):
        port = self.server.server_port
        self.server.server_port = 443
        connection = http.client.HTTPSConnection(
            "127.0.0.1", port,
            context=ssl.create_default_context(cafile=str(self.cert)), timeout=3,
        )
        connection.request("POST", "/api/register", json.dumps({
            "username": "standard_port", "email": "standard@example.com"
        }), {
            "Host": DOMAIN, "Origin": "https://" + DOMAIN,
            "Content-Type": "application/json",
        })
        response = connection.getresponse()
        self.assertEqual(response.status, 201)
        response.read()
        connection.close()

    def test_inherited_listener(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(16)
        server = RegistrationServer(
            listener.getsockname(), Path(self.database.name) / "activated.sqlite3",
            ["127.0.0.1/32"], str(self.cert), str(self.key), listener,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPSConnection(
                "127.0.0.1", server.server_port,
                context=ssl.create_default_context(cafile=str(self.cert)), timeout=3,
            )
            connection.request("GET", "/api/health")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read()), {"status": "ok"})
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertEqual(listener.fileno(), -1)


if __name__ == "__main__":
    unittest.main()
