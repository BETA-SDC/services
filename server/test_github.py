from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from werkzeug.security import generate_password_hash

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server.registration import create_app
from server import github_broker


class GithubTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.temp.name) / "github.sqlite3",
                              config={"GITHUB_CLIENT_ID": "synthetic-client-id"})
        self.store = self.app.extensions["registration_store"]
        self.client = self.app.test_client()
        self.headers = {
            "Host": "account.betasdc.duckdns.org", "X-Forwarded-For": "10.16.125.52",
            "X-Forwarded-Proto": "https",
        }
        self.password = "Github-test-long-password"
        self.store.add("github_user", "github@westlake.edu.cn", generate_password_hash(self.password))
        self.store.add("other_user", "other@westlake.edu.cn", generate_password_hash(self.password))
        with self.store.connection() as connection:
            connection.execute("UPDATE registrations SET email_verified_at=1")
        self.login("github_user")

    def tearDown(self):
        self.temp.cleanup()

    def login(self, identifier):
        self.headers["X-CSRF-Token"] = self.client.get("/api/session", headers=self.headers).json["csrf_token"]
        response = self.post("login", {"identifier": identifier, "password": self.password})
        self.assertEqual(response.status_code, 200)
        self.headers["X-CSRF-Token"] = response.json["csrf_token"]

    def post(self, path, payload=None):
        return self.client.post("/api/" + path, headers=self.headers, json=payload or {})

    def start(self):
        response = self.post("github/link")
        self.assertEqual(response.status_code, 200)
        params = parse_qs(urlsplit(response.json["url"]).query, keep_blank_values=True)
        self.assertEqual(params["scope"], ["read:org"])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertEqual(len(params["code_challenge"][0]), 43)
        self.assertEqual(params["redirect_uri"], [github_broker.CALLBACK])
        return params["state"][0]

    def callback(self, state, **params):
        headers = dict(self.headers, **{"Sec-Fetch-Site": "cross-site",
                                      "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"})
        return self.client.get("/api/github/callback", headers=headers,
                               query_string={"state": state, "code": "synthetic-code", **params})

    def test_binding_requires_same_session_and_state_is_single_use(self):
        state = self.start()
        with patch("server.registration.github.exchange_identity",
                   return_value={"id": 1234, "login": "test-github-user"}) as exchange:
            self.assertIn("invalid", self.callback("x" * 43).location)
            self.assertIn("linked", self.callback(state).location)
            self.assertIn("invalid", self.callback(state).location)
            exchange.assert_called_once()
        status = self.client.get("/api/session", headers=self.headers).json
        self.assertEqual(status["github"]["login"], "test-github-user")
        self.assertEqual(status["github"]["avatar_url"], "https://avatars.githubusercontent.com/u/1234?s=160")
        self.assertNotIn("access_token", str(status))
        with self.store.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM github_flows").fetchone()[0], 0)
        state = self.start()
        self.login("other_user")
        with patch("server.registration.github.exchange_identity") as exchange:
            self.assertIn("invalid", self.callback(state).location)
            exchange.assert_not_called()

    def test_expiry_denial_and_unavailable_provider_do_not_bind(self):
        state = self.start()
        with self.store.connection() as connection:
            connection.execute("UPDATE github_flows SET expires_at=0")
        self.assertIn("invalid", self.callback(state).location)
        state = self.start()
        self.assertIn("cancelled", self.callback(state, error="access_denied").location)
        state = self.start()
        with patch("server.registration.github.exchange_identity", side_effect=OSError("private-data")):
            response = self.callback(state)
            self.assertIn("unavailable", response.location)
            self.assertNotIn("private-data", response.get_data(as_text=True))
        self.assertIsNone(self.client.get("/api/session", headers=self.headers).json["github"])

    def test_github_id_unique_and_existing_binding_not_replaced(self):
        state = self.start()
        with patch("server.registration.github.exchange_identity",
                   return_value={"id": 1234, "login": "first"}):
            self.assertIn("linked", self.callback(state).location)
        state = self.start()
        with patch("server.registration.github.exchange_identity",
                   return_value={"id": 9876, "login": "second"}):
            self.assertIn("already", self.callback(state).location)
        self.login("other_user")
        state = self.start()
        with patch("server.registration.github.exchange_identity",
                   return_value={"id": 1234, "login": "first"}):
            self.assertIn("conflict", self.callback(state).location)

    def test_unlink_requires_recent_auth_and_cancels_pending_flows(self):
        state = self.start()
        with self.client.session_transaction(base_url="https://" + self.headers["Host"]) as session:
            session["auth_time"] = int(time.time()) - 601
        self.assertEqual(self.post("github/unlink").status_code, 403)
        self.login("github_user")
        state = self.start()
        response = self.post("github/unlink")
        self.assertEqual(response.status_code, 200)
        self.headers["X-CSRF-Token"] = response.json["csrf_token"]
        self.assertIn("invalid", self.callback(state).location)
        self.post("logout")
        self.assertEqual(self.post("github/link").status_code, 403)

    def test_callback_exception_does_not_allow_other_cross_site_requests(self):
        headers = dict(self.headers, **{"Sec-Fetch-Site": "cross-site",
                                      "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"})
        self.assertEqual(self.client.get("/api/session", headers=headers).status_code, 403)
        self.assertEqual(self.client.post("/api/github/link", headers=headers, json={}).status_code, 403)
        headers.pop("Sec-Fetch-Mode")
        self.assertEqual(self.client.get("/api/github/callback", headers=headers).status_code, 403)

    def test_successful_oauth_redirect_chain_allows_only_top_level_account_navigation(self):
        state = self.start()
        headers = dict(self.headers, **{"Sec-Fetch-Site": "cross-site",
                                      "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"})
        with patch("server.registration.github.exchange_identity",
                   return_value={"id": 1234, "login": "fixture-return"}):
            with self.client.get("/api/github/callback", headers=headers,
                                 query_string={"state": state, "code": "synthetic-code"},
                                 follow_redirects=True) as response:
                self.assertEqual(response.status_code, 200)
                self.assertIn('id="account-view"', response.get_data(as_text=True))
        self.assertEqual(self.client.get("/api/session", headers=headers).status_code, 403)
        self.assertEqual(self.client.post("/api/profile", headers=headers,
                                         json={"real_name": "测试姓名"}).status_code, 403)
        iframe = dict(headers, **{"Sec-Fetch-Dest": "iframe"})
        self.assertEqual(self.client.get("/account", headers=iframe).status_code, 403)

    def test_logout_during_provider_exchange_prevents_binding(self):
        state = self.start()

        def exchange(*args):
            with self.store.connection() as connection:
                connection.execute("DELETE FROM auth_sessions")
            return {"id": 1234, "login": "public-user"}

        with patch("server.registration.github.exchange_identity", side_effect=exchange):
            self.assertIn("invalid", self.callback(state).location)
        with self.store.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM github_accounts").fetchone()[0], 0)

    def test_owner_auto_admin_is_not_a_local_role_and_unlink_revokes_it(self):
        state = self.start()
        identity = {"id": 1234, "login": "fixture-owner", "organization_access": {
            "state": "member", "role": "owner", "teams": [], "member_team_ids": [],
            "structure_complete": True}}
        with patch("server.registration.github.exchange_identity", return_value=identity):
            self.assertIn("linked", self.callback(state).location)
        status = self.client.get("/api/session", headers=self.headers).json
        self.assertTrue(status["access"]["is_admin"])
        self.assertEqual(self.client.get("/api/admin/overview", headers=self.headers).status_code, 200)
        with patch("server.registration.github.fetch_public_identity",
                   return_value={"id": 1234, "login": "fixture-owner"}):
            self.assertEqual(self.post("github/refresh").status_code, 200)
        self.assertTrue(self.client.get("/api/session", headers=self.headers).json["access"]["is_admin"])
        self.assertEqual(self.post("github/unlink").status_code, 200)
        self.assertFalse(self.client.get("/api/session", headers=self.headers).json["access"]["is_admin"])
        self.assertEqual(self.client.get("/api/admin/overview", headers=self.headers).status_code, 403)

    def test_broker_only_returns_identity_and_uses_fixed_provider_endpoints(self):
        with patch("server.github_broker.remote_json", side_effect=[
            {"access_token": "synthetic_token_123456"}, {"id": 1234, "login": "public-user",
                                                       "email": "private@example.test"},
            {"id": 1234, "login": "public-user", "name": "Public Name", "public_repos": 42},
            [{"id": 5678, "login": "public-org", "description": "Public organization"}],
        ]) as remote:
            result = github_broker.exchange("code", "verifier", "client", "private-secret")
            self.assertEqual(result["id"], 1234)
            self.assertEqual(result["public_profile"]["public_repos"], 42)
            self.assertEqual(result["organizations"][0]["description"], "Public organization")
            self.assertTrue(result["organizations_complete"])
            self.assertNotIn("private@example.test", str(result))
            self.assertNotIn("synthetic_token", str(result))
            self.assertEqual(remote.call_args_list[0].args[0], "https://github.com/login/oauth/access_token")
            self.assertEqual(remote.call_args_list[1].args[0], "https://api.github.com/user")
            self.assertIn(b"code_verifier=verifier", remote.call_args_list[0].kwargs["data"])
            for call in remote.call_args_list[2:]:
                header = call.kwargs["headers"]["Authorization"]
                self.assertTrue(header.startswith("Basic "))
                self.assertNotIn("synthetic_token", header)
        with patch("server.github_broker.remote_json", return_value={"error": "bad_credentials"}):
            with self.assertRaises(ValueError):
                github_broker.exchange("code", "verifier", "client", "private-secret")

    def test_profile_snapshot_saved_but_session_only_displays_requested_fields(self):
        identity = {
            "id": 1234, "login": "public-user", "name": "Public Name",
            "public_profile": {"id": 1234, "login": "public-user", "public_repos": 42,
                               "bio": "Full public biography", "hireable": True},
            "organizations": [{"id": 5678, "login": "public-org", "description": "Organization"}],
            "organizations_complete": True,
        }
        state = self.start()
        with patch("server.registration.github.exchange_identity", return_value=identity):
            self.assertIn("linked", self.callback(state).location)
        profile = self.client.get("/api/session", headers=self.headers).json["github"]
        self.assertEqual(profile["name"], "Public Name")
        self.assertEqual(profile["organizations"][0]["html_url"], "https://github.com/public-org")
        self.assertNotIn("public_profile", profile)
        self.assertNotIn("description", profile["organizations"][0])
        with self.store.connection() as connection:
            import json
            row = connection.execute("SELECT profile_json,synced_at FROM github_accounts").fetchone()
            self.assertEqual(json.loads(row[0])["public_profile"]["public_repos"], 42)
            self.assertGreater(row[1], 0)
        # Reauthorization updates profile fields without changing the linked numeric ID.
        identity["name"] = "Changed Name"
        state = self.start()
        with patch("server.registration.github.exchange_identity", return_value=identity):
            self.assertIn("linked", self.callback(state).location)
        self.assertEqual(self.client.get("/api/session", headers=self.headers).json["github"]["name"], "Changed Name")

    def test_public_org_pagination_and_incomplete_snapshot_are_explicit(self):
        orgs = [{"id": 1000 + index, "login": f"org-{index}"} for index in range(100)]
        responses = [{"access_token": "synthetic_token_123456"}, {"id": 1234, "login": "public-user"},
                     {"id": 1234, "login": "public-user"}, orgs, []]
        with patch("server.github_broker.remote_json", side_effect=responses) as remote:
            result = github_broker.exchange("code", "verifier", "client", "private-secret")
            self.assertTrue(result["organizations_complete"])
            self.assertEqual(len(result["organizations"]), 100)
            self.assertIn("page=2", remote.call_args.args[0])
        responses[-1] = OSError("private-provider-error")
        with patch("server.github_broker.remote_json", side_effect=responses):
            result = github_broker.exchange("code", "verifier", "client", "private-secret")
            self.assertFalse(result["organizations_complete"])
            self.assertEqual(len(result["organizations"]), 100)

    def test_legacy_binding_migrates_without_losing_identity(self):
        import sqlite3
        from server.registration.repository import RegistrationStore
        path = Path(self.temp.name) / "legacy.sqlite3"
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE github_accounts (user_id INTEGER PRIMARY KEY,github_id INTEGER UNIQUE NOT NULL,login TEXT NOT NULL)")
            connection.execute("INSERT INTO github_accounts VALUES (1,1234,'original')")
        store = RegistrationStore(path, 1000)
        store.initialize()
        store.initialize()
        with store.connection() as connection:
            self.assertEqual(connection.execute("SELECT github_id,login,profile_json,synced_at FROM github_accounts").fetchone(),
                             (1234, "original", "{}", None))

    def test_refresh_preserves_binding_and_rejects_changed_identity_or_concurrent_unlink(self):
        state = self.start()
        with patch("server.registration.github.exchange_identity", return_value={"id": 1234, "login": "original"}):
            self.assertIn("linked", self.callback(state).location)
        with patch("server.registration.github.fetch_public_identity",
                   return_value={"id": 1234, "login": "renamed", "name": "New Nickname"}):
            response = self.post("github/refresh")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["github"]["name"], "New Nickname")
        with patch("server.registration.github.fetch_public_identity",
                   return_value={"id": 9999, "login": "wrong"}):
            self.assertEqual(self.post("github/refresh").status_code, 502)
        self.assertEqual(self.client.get("/api/session", headers=self.headers).json["github"]["login"], "renamed")

        def unlink(*args):
            with self.store.connection() as connection:
                connection.execute("DELETE FROM github_accounts")
            return {"id": 1234, "login": "renamed"}

        with patch("server.registration.github.fetch_public_identity", side_effect=unlink):
            self.assertEqual(self.post("github/refresh").status_code, 409)
        self.assertIsNone(self.client.get("/api/session", headers=self.headers).json["github"])
