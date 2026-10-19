from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlsplit

from server.registration import create_app
from server import test_private as private_tests
from server.registration.sites import ACCOUNT_HOST, NAV_HOST, PROFILE_HOST


class SitesTests(unittest.TestCase):
    setUpClass = classmethod(private_tests.PrivateTests.setUpClass.__func__)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        path = Path(self.temp.name) / "sites.sqlite3"
        self.apps = {host: create_app(path, config={"REGISTRATION_DOMAIN": host, "SECRET_KEY": str(index) * 64})
                     for index, host in enumerate((ACCOUNT_HOST, NAV_HOST, PROFILE_HOST), 1)}
        self.clients = {host: app.test_client() for host, app in self.apps.items()}
        self.store = self.apps[ACCOUNT_HOST].extensions["registration_store"]
        self.store.add("u_demo", "demo@westlake.edu.cn", self.password_hash)
        with self.store.connection() as connection:
            connection.execute("UPDATE registrations SET email_verified_at=1,real_name='Private real name',nickname='Demo'")
        self.csrf = {}
        for host in self.apps:
            self.csrf[host] = self.get(host, "/api/session").json["csrf_token"]
        result = self.post(ACCOUNT_HOST, "/api/login", {"identifier": "demo@westlake.edu.cn", "password": self.password})
        self.csrf[ACCOUNT_HOST] = result.json["csrf_token"]

    def tearDown(self):
        self.temp.cleanup()

    def headers(self, host):
        return {"Host": host, "X-Forwarded-For": "10.16.125.52", "X-Forwarded-Proto": "https",
                "X-CSRF-Token": self.csrf.get(host, "")}

    def get(self, host, path):
        return self.clients[host].get(path, headers=self.headers(host))

    def post(self, host, path, body):
        return self.clients[host].post(path, headers=self.headers(host), json=body)

    def put(self, host, path, body):
        return self.clients[host].put(path, headers=self.headers(host), json=body)

    def handoff(self, host=NAV_HOST):
        start = self.get(host, "/sso/start?next=%2F")
        self.assertEqual(start.status_code, 303)
        authorized = self.get(ACCOUNT_HOST, start.location)
        self.assertEqual(authorized.status_code, 303, authorized.json)
        return authorized.location

    def test_sso_sets_only_host_cookie_and_logout_revokes_all_children(self):
        result = self.get(NAV_HOST, self.handoff())
        self.assertEqual(result.status_code, 303)
        cookie = result.headers["Set-Cookie"]
        self.assertIn("__Host-betasdc=", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertNotIn("Domain=", cookie)
        self.assertEqual(self.get(NAV_HOST, "/api/site/session").json["user"]["name"], "Demo")
        self.get(PROFILE_HOST, self.handoff(PROFILE_HOST))
        self.post(ACCOUNT_HOST, "/api/logout", {})
        for host in (NAV_HOST, PROFILE_HOST):
            self.assertIsNone(self.get(host, "/api/site/session").json["user"])

    def test_replay_expiry_wrong_browser_and_wrong_host_rejected(self):
        callback = self.handoff()
        other = self.apps[NAV_HOST].test_client()
        self.assertEqual(other.get(callback, headers=self.headers(NAV_HOST)).status_code, 409)
        self.assertEqual(self.get(PROFILE_HOST, callback).status_code, 409)
        self.assertEqual(self.get(NAV_HOST, callback).status_code, 303)
        self.assertEqual(self.get(NAV_HOST, callback).status_code, 409)
        callback = self.handoff()
        with self.store.connection() as connection:
            connection.execute("UPDATE sso_flows SET expires_at=1")
        self.assertEqual(self.get(NAV_HOST, callback).status_code, 409)

    def test_revoked_account_cannot_redeem_outstanding_ticket(self):
        callback = self.handoff()
        self.post(ACCOUNT_HOST, "/api/logout", {})
        self.assertEqual(self.get(NAV_HOST, callback).status_code, 409)

    def test_return_url_validation_and_account_host_enforcement(self):
        for value in ("https://evil.invalid", "//evil.invalid", "/\\evil.invalid", "/api/logout", "/sso/start"):
            from urllib.parse import quote
            self.assertEqual(self.get(NAV_HOST, "/sso/start?next=" + quote(value, safe="")).status_code, 400)
        self.assertEqual(self.get(NAV_HOST, "/sso/authorize?state=" + "x" * 40).status_code, 403)
        self.assertEqual(self.get(ACCOUNT_HOST, "/sso/start").status_code, 400)

    def test_navigation_host_redirects_to_calendar_and_satellite_logout_revokes_session(self):
        response = self.get(NAV_HOST, "/")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.location, "https://calendar.betasdc.duckdns.org/sso/start?next=%2Fcalendar%2F")
        self.get(NAV_HOST, self.handoff())
        self.csrf[NAV_HOST] = self.get(NAV_HOST, "/api/site/session").json["csrf_token"]
        self.assertEqual(self.post(NAV_HOST, "/api/logout", {}).status_code, 200)
        self.assertIsNone(self.get(NAV_HOST, "/api/site/session").json["user"])

    def test_sensitive_endpoints_and_password_login_remain_account_only(self):
        self.get(NAV_HOST, self.handoff())
        self.csrf[NAV_HOST] = self.get(NAV_HOST, "/api/site/session").json["csrf_token"]
        for path in ("/api/private", "/api/security", "/api/admin/overview"):
            self.assertEqual(self.get(NAV_HOST, path).status_code, 403)
        self.assertEqual(self.post(NAV_HOST, "/api/login", {}).status_code, 403)

    def test_public_profile_allowlist_hides_private_fields(self):
        result = self.get(PROFILE_HOST, "/api/public-profile/u_demo")
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(set(result.json), {"slug", "bio", "name", "avatar", "github"})
        self.assertNotIn("westlake.edu.cn", result.get_data(as_text=True))
        self.assertNotIn("Private real name", result.get_data(as_text=True))
        self.assertEqual(self.get(PROFILE_HOST, "/api/public-profile/missing").status_code, 404)

    def test_default_profile_path_is_registration_number_not_display_name(self):
        self.assertEqual(
            self.post(ACCOUNT_HOST, "/api/profile",
                      {"real_name": "杨轩", "nickname": "小恐龙"}).status_code,
            200,
        )
        result = self.get(PROFILE_HOST, "/api/public-profile/u_demo")
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(result.json["slug"], "u_demo")
        self.assertEqual(result.json["name"], "小恐龙")
        self.assertEqual(self.get(PROFILE_HOST, "/api/public-profile/杨轩").status_code, 404)

    def test_global_color_preference_is_account_only_and_shared_in_site_session(self):
        self.assertEqual(self.get(ACCOUNT_HOST, "/api/preferences").json["theme"], "system")
        self.assertEqual(self.put(ACCOUNT_HOST, "/api/preferences", {"theme": "dark"}).status_code, 200)
        self.assertEqual(self.get(ACCOUNT_HOST, "/api/preferences").json["theme"], "dark")
        self.get(NAV_HOST, self.handoff())
        self.assertEqual(self.get(NAV_HOST, "/api/site/session").json["user"]["preferences"]["theme"], "dark")
        self.assertEqual(self.get(NAV_HOST, "/api/preferences").status_code, 403)
        self.assertEqual(self.put(ACCOUNT_HOST, "/api/preferences", {"theme": "invalid"}).status_code, 422)

    def test_slug_approval_preserves_old_alias_and_cannot_be_self_approved(self):
        self.assertEqual(self.post(ACCOUNT_HOST, "/api/profile/address-request", {"slug": "campus-demo"}).status_code, 201)
        self.assertEqual(self.post(ACCOUNT_HOST, "/api/admin/profile-addresses", {"id": 1, "decision": "approve"}).status_code, 403)
        with self.store.connection() as connection:
            connection.execute("INSERT INTO account_roles VALUES(1,'admin')")
        result = self.post(ACCOUNT_HOST, "/api/admin/profile-addresses", {"id": 1, "decision": "approve"})
        self.assertEqual(result.status_code, 200, result.json)
        for slug in ("u_demo", "campus-demo"):
            self.assertEqual(self.get(PROFILE_HOST, "/api/public-profile/" + slug).json["slug"], "campus-demo")
        self.assertEqual(self.post(ACCOUNT_HOST, "/api/profile/address-request", {"slug": "campus-demo"}).status_code, 409)
        self.assertEqual(self.post(ACCOUNT_HOST, "/api/admin/profile-addresses", {"id": 1, "decision": "approve"}).status_code, 409)

    def test_reserved_invalid_slug_and_bio_validation(self):
        for value in ("admin", "api", "settings", "John", "../x", "-bad", "bad-", "a" * 40):
            self.assertEqual(self.post(ACCOUNT_HOST, "/api/profile/address-request", {"slug": value}).status_code, 422)
        self.assertEqual(self.post(ACCOUNT_HOST, "/api/profile/public", {"bio": "a" * 281}).status_code, 422)
        self.assertEqual(self.post(ACCOUNT_HOST, "/api/profile/public", {"bio": "<script>not executed</script>"}).status_code, 200)
        self.assertEqual(self.get(PROFILE_HOST, "/api/public-profile/u_demo").json["bio"], "<script>not executed</script>")

    def test_silent_guest_handoff_has_no_login_loop_and_required_login_resumes(self):
        self.post(ACCOUNT_HOST, "/api/logout", {})
        self.get(NAV_HOST, self.handoff())
        state = self.get(NAV_HOST, "/api/site/session").json
        self.assertTrue(state["sso_checked"])
        self.assertIsNone(state["user"])
        start = self.get(NAV_HOST, "/sso/start?login=1")
        auth = self.get(ACCOUNT_HOST, start.location)
        self.assertEqual(urlsplit(auth.location).path, "/login")
        self.csrf[ACCOUNT_HOST] = self.get(ACCOUNT_HOST, "/api/session").json["csrf_token"]
        result = self.post(ACCOUNT_HOST, "/api/login", {"identifier": "demo@westlake.edu.cn", "password": self.password})
        self.assertEqual(result.status_code, 200)
        continuation = self.get(ACCOUNT_HOST, "/api/site/session").json["continue_url"]
        self.assertTrue(continuation.startswith("/sso/authorize?"))
        self.assertEqual(self.get(NAV_HOST, self.get(ACCOUNT_HOST, continuation).location).status_code, 303)

    def test_templates_and_assets_are_shared(self):
        for host, path in ((ACCOUNT_HOST, "/login"), (PROFILE_HOST, "/u_demo")):
            result = self.get(host, path)
            self.assertIn("/beta-shell.js", result.get_data(as_text=True))
            result.close()
        for host in (ACCOUNT_HOST, NAV_HOST, PROFILE_HOST):
            result = self.get(host, "/beta-shell.css")
            self.assertEqual(result.status_code, 200)
            result.close()

    def test_header_session_does_not_race_bootstrap_csrf_cookie(self):
        client = self.apps[ACCOUNT_HOST].test_client()
        response = client.get("/api/site/session", headers=self.headers(ACCOUNT_HOST))
        self.assertNotIn("Set-Cookie", response.headers)
        self.assertEqual(response.json["csrf_token"], "")

    def test_child_token_is_not_accepted_on_account_or_another_site(self):
        self.get(NAV_HOST, self.handoff())
        with self.clients[NAV_HOST].session_transaction(base_url="https://" + NAV_HOST) as session:
            token = session["auth_token"]
        with self.clients[PROFILE_HOST].session_transaction(base_url="https://" + PROFILE_HOST) as session:
            session["auth_token"] = token
        self.assertIsNone(self.get(PROFILE_HOST, "/api/site/session").json["user"])
        with self.clients[ACCOUNT_HOST].session_transaction(base_url="https://" + ACCOUNT_HOST) as session:
            session["auth_token"] = token
        self.assertIsNone(self.get(ACCOUNT_HOST, "/api/site/session").json["user"])


if __name__ == "__main__":
    unittest.main()
