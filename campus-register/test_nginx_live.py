"""Opt-in end-to-end checks through a temporary loopback Nginx listener."""
import json
import os
import subprocess
import unittest
import uuid


@unittest.skipUnless(os.environ.get("BETASDC_TEST_NGINX_PORT"), "No live Nginx test listener")
class LiveNginxTests(unittest.TestCase):
    def request(self, path="/api/health", method="GET", body=None, extra=None,
                campus=True):
        port = os.environ["BETASDC_TEST_NGINX_PORT"]
        command = [
            "/usr/bin/curl", "-q", "--noproxy", "*", "--silent", "--show-error",
            "--connect-to", "register.betasdc.duckdns.org:8443:127.0.0.1:" + port,
            "--max-time", "5", "-X", method, "-w", "\n%{http_code}",
        ]
        if campus:
            command += ["--interface", "10.17.131.26"]
        if body is not None:
            command += ["-H", "Content-Type: application/json", "--data-binary", body]
        for header in extra or []:
            command += ["-H", header]
        command.append("https://register.betasdc.duckdns.org:8443" + path)
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        data, status = result.stdout.rsplit("\n", 1)
        return int(status), data

    def test_tls_proxy_and_spoofed_header_replacement(self):
        status, data = self.request(extra=["X-Forwarded-For: 8.8.8.8",
                                          "X-Forwarded-Proto: http"])
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data), {"status": "ok"})
        self.assertEqual(self.request("/")[0], 200)

    def test_campus_access_control(self):
        self.assertEqual(self.request(campus=False)[0], 403)

    def test_registration_and_origin(self):
        name = "nginx_" + uuid.uuid4().hex[:10]
        payload = json.dumps({"username": name, "email": name + "@example.com"})
        self.assertEqual(self.request(
            "/api/register", "POST", payload,
            ["Origin: https://register.betasdc.duckdns.org:8443"],
        )[0], 201)
        self.assertEqual(self.request(
            "/api/register", "POST", payload, ["Origin: https://attacker.example"],
        )[0], 403)
        self.assertEqual(self.request("/api/register", "POST", "x" * 4097)[0], 413)

    def test_request_rate_limit(self):
        statuses = [self.request("/api/register", "POST", "{}")[0] for _ in range(10)]
        self.assertIn(429, statuses)
