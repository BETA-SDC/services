import ipaddress
import test_app

from app import DOMAIN, RegistrationServer


class ProxyTests(test_app.RegistrationTests):
    def setUp(self):
        super().setUp()
        self.server.proxy_port = 8443
        self.server.scheme = "https"
        self.server.allowed_networks = [
            ipaddress.ip_network("10.17.128.0/18")
        ]

    def headers(self, address="10.17.131.26"):
        return {
            "Host": DOMAIN + ":8443", "Content-Type": "application/json",
            "Origin": "https://" + DOMAIN + ":8443",
            "X-Forwarded-For": address, "X-Forwarded-Proto": "https",
        }

    # Inherited direct-listener tests use different headers and policies.
    def test_access_and_origin(self):
        self.assertEqual(self.request("GET", "/api/health", headers=self.headers())[0], 200)
        for address in ("8.8.8.8", "127.0.0.1", "10.17.131.26, 8.8.8.8", "invalid"):
            self.assertEqual(
                self.request("GET", "/api/health", headers=self.headers(address))[0], 403
            )
        for key, value in [
            ("X-Forwarded-Proto", "http"), ("Host", "attacker.example"),
            ("Origin", "https://attacker.example"),
        ]:
            headers = self.headers()
            headers[key] = value
            self.assertEqual(self.request("POST", "/api/register", {
                "username": "proxy_demo", "email": "proxy@example.com",
            }, headers=headers)[0], 403)

    def test_persistent_registration_and_duplicates(self):
        payload = {"username": "proxy_demo", "email": "proxy@example.com"}
        self.assertEqual(self.request("POST", "/api/register", payload, self.headers())[0], 201)
        self.assertEqual(self.request("POST", "/api/register", payload, self.headers())[0], 409)

    def test_assets(self):
        self.assertEqual(self.request("GET", "/", headers=self.headers())[0], 200)

    def test_limits_and_private_data(self):
        self.assertEqual(self.request("GET", "/api/health")[0], 403)
        for address in ("10.17.131.26", "10.17.131.27"):
            for _ in range(10):
                self.assertEqual(self.request(
                    "POST", "/api/register", raw="{}", headers=self.headers(address)
                )[0], 400)
            self.assertEqual(self.request(
                "POST", "/api/register", raw="{}", headers=self.headers(address)
            )[0], 429)

    def test_validation(self):
        with self.assertRaises(ValueError):
            RegistrationServer(("0.0.0.0", 0), self.db, [], proxy_port=8443)
