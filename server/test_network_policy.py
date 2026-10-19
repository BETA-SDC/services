from pathlib import Path
import tempfile
import unittest

from server.registration import create_app
from server.registration.sites import ACCOUNT_HOST, NAV_HOST, PROFILE_HOST


class NetworkPolicyTests(unittest.TestCase):
    def test_default_hosts_and_sso_accept_other_subnets(self):
        with tempfile.TemporaryDirectory() as temp:
            for index, host in enumerate((ACCOUNT_HOST, NAV_HOST, PROFILE_HOST)):
                app = create_app(Path(temp) / f"{index}.sqlite3", config={"REGISTRATION_DOMAIN": host})
                client = app.test_client()
                for address in ("10.17.64.1", "10.26.79.1", "192.168.2.3", "203.0.113.1", "2001:db8::1"):
                    headers = {"Host": host, "X-Forwarded-For": address, "X-Forwarded-Proto": "https"}
                    result = client.get("/api/site/session", headers=headers)
                    self.assertEqual(result.status_code, 200, (host, address, result.json))
                    self.assertTrue(result.json["sso_available"])
                    if host == ACCOUNT_HOST:
                        self.assertEqual(client.get("/api/checkin/events", headers=headers).status_code, 401)
                    else:
                        self.assertEqual(client.get("/sso/start", headers=headers).status_code, 303)
                    self.assertEqual(client.get("/api/site/session",
                                                headers=headers | {"Origin": "https://evil.invalid"}).status_code, 403)

    def test_nginx_sources_do_not_restore_fixed_client_subnets(self):
        root = Path(__file__).resolve().parent
        for name in ("nginx.conf", "sites.nginx.conf"):
            source = (root / name).read_text()
            self.assertIn("allow all;", source)
            self.assertNotIn("deny all;", source)
            self.assertNotIn("allow 10.", source)
        source = (root / "nginx.conf").read_text()
        self.assertIn("location ^~ /api/checkin/ {", source)
        self.assertIn("client_max_body_size 32k;", source)
