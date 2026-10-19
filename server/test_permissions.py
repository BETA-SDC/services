from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from server.registration import create_app
from server.registration.permissions import sync_memberships, owner_expiry
from server.github_organization import organization_access
from server.github_broker import fetch_organization


class PermissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.temp.name) / "permissions.sqlite3")
        self.store = self.app.extensions["registration_store"]
        self.store.add("admin_fixture", "admin-fixture@westlake.edu.cn")
        self.store.add("member_fixture", "member-fixture@westlake.edu.cn")
        with self.store.connection() as connection:
            connection.execute("UPDATE registrations SET email_verified_at=1")
            connection.execute("INSERT INTO account_roles VALUES (1,'admin')")
        self.client = self.app.test_client()
        self.headers = {"Host": "account.betasdc.duckdns.org", "X-Forwarded-For": "10.16.125.52",
                        "X-Forwarded-Proto": "https", "X-CSRF-Token": "synthetic-csrf"}
        self.login(1)

    def tearDown(self):
        self.temp.cleanup()

    def login(self, user_id, age=0):
        token = self.store.create_session(user_id, 3600)
        with self.client.session_transaction(base_url="https://" + self.headers["Host"]) as session:
            session["auth_token"] = token
            session["csrf_token"] = self.headers["X-CSRF-Token"]
            session["auth_time"] = int(time.time()) - age

    def post(self, path, payload):
        return self.client.post("/api/admin/" + path, json=payload, headers=self.headers)

    def overview(self):
        return self.client.get("/api/admin/overview", headers=self.headers)

    def test_access_is_server_enforced_not_session_or_group_claim(self):
        with self.client.get("/admin", headers=self.headers) as response:
            self.assertEqual(response.status_code, 200)
        self.login(2)
        with self.client.session_transaction(base_url="https://" + self.headers["Host"]) as session:
            session["role"] = "admin"
        self.assertEqual(self.overview().status_code, 403)
        self.assertEqual(self.client.get("/admin", headers=self.headers).status_code, 403)
        self.assertEqual(self.post("role", {"user_id": 2, "role": "admin"}).status_code, 403)
        for path in ("groups", "group/update", "membership", "memberships"):
            self.assertEqual(self.post(path, {}).status_code, 403)
        with self.client.session_transaction(base_url="https://" + self.headers["Host"]) as session:
            session.clear()
        self.assertEqual(self.overview().status_code, 401)

    def test_role_change_recent_auth_csrf_self_protection_and_revocation(self):
        self.assertEqual(self.post("role", {"user_id": 1, "role": "member"}).status_code, 409)
        self.assertEqual(self.post("role", {"user_id": 1, "role": "admin"}).status_code, 409)
        self.login(1, age=601)
        self.assertEqual(self.post("role", {"user_id": 2, "role": "admin"}).status_code, 403)
        self.login(1)
        headers = dict(self.headers, **{"X-CSRF-Token": "wrong"})
        self.assertEqual(self.client.post("/api/admin/role", json={"user_id": 2, "role": "admin"},
                                         headers=headers).status_code, 403)
        self.assertEqual(self.post("role", {"user_id": 2, "role": "admin"}).status_code, 200)
        self.assertEqual(self.post("role", {"user_id": 2, "role": "member"}).status_code, 200)
        self.login(2)
        self.assertEqual(self.overview().status_code, 403)

    def test_groups_create_nested_rename_archive_and_audit(self):
        self.assertEqual(self.post("groups", {"name": "Research"}).status_code, 200)
        group = self.overview().json["groups"][0]
        self.assertEqual(self.post("groups", {"name": "Robotics", "parent_id": group["id"]}).status_code, 200)
        self.assertEqual(self.post("group/update", {"group_id": group["id"], "archived": True}).status_code, 409)
        self.assertEqual(self.post("group/update", {"group_id": group["id"], "name": "Research lab"}).status_code, 200)
        child = next(item for item in self.overview().json["groups"] if item["parent_id"])
        self.assertEqual(self.post("group/update", {"group_id": child["id"], "archived": True}).status_code, 200)
        self.assertEqual(self.post("group/update", {"group_id": group["id"], "archived": True}).status_code, 200)
        self.assertFalse(self.overview().json["groups"])
        self.assertEqual(len(self.overview().json["audit"]), 5)
        self.assertNotIn("password_hash", str(self.overview().json))

    def test_batch_is_atomic_and_github_groups_cannot_be_edited(self):
        self.post("groups", {"name": "Local"})
        local = self.overview().json["groups"][0]["id"]
        with self.store.connection() as connection:
            sync_memberships(connection, 2, {"organizations": [{"login": "BETA-SDC"}]})
        github = next(item["id"] for item in self.overview().json["groups"] if item["source"] == "github")
        response = self.post("memberships", {"user_id": 2, "changes": [
            {"group_id": local, "member": True}, {"group_id": github, "member": True}]})
        self.assertEqual(response.status_code, 422)
        with self.store.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM group_members WHERE source='local'").fetchone()[0], 0)
        self.assertEqual(self.post("memberships", {"user_id": 2, "changes": [{"group_id": local, "member": True}]}).status_code, 200)
        self.assertEqual(self.post("group/update", {"group_id": github, "name": "Forged"}).status_code, 422)
        self.login(2)
        access = self.client.get("/api/session", headers=self.headers).json["access"]
        self.assertFalse(access["is_admin"])
        self.assertEqual(len(access["groups"]), 2)

    def test_group_filter_applies_before_pagination(self):
        with self.store.connection() as connection:
            for index in range(3, 54):
                connection.execute("INSERT INTO registrations(id,username,email) VALUES (?,?,?)",
                                   (index, f"paging_{index}", f"paging-{index}@westlake.edu.cn"))
            connection.execute("INSERT INTO account_groups(id,name,source) VALUES (1,'Paged','local')")
            connection.execute("INSERT INTO group_members VALUES(53,1,'local',NULL)")
        response = self.client.get("/api/admin/overview?group=1", headers=self.headers)
        self.assertEqual(response.json["total"], 1)
        self.assertEqual(response.json["users"][0]["id"], 53)
        response = self.client.get("/api/admin/overview?page=2", headers=self.headers)
        self.assertEqual(len(response.json["users"]), 3)
        self.assertEqual(self.client.get("/api/admin/overview?group=invalid", headers=self.headers).status_code, 400)

    def snapshot(self, teams=None, members=None, state="member", complete=True):
        return {"organization_access": {
            "state": state, "teams": teams or [
                {"id": 20, "name": "Child", "slug": "child", "parent_id": 10},
                {"id": 10, "name": "Parent", "slug": "parent", "parent_id": None}],
            "member_team_ids": members if members is not None else [20],
            "structure_complete": complete}}

    def test_team_hierarchy_membership_expiry_and_authoritative_removal(self):
        with self.store.connection() as connection:
            sync_memberships(connection, 2, self.snapshot())
        groups = self.overview().json["groups"]
        parent = next(item for item in groups if item["name"] == "Parent")
        child = next(item for item in groups if item["name"] == "Child")
        self.assertEqual(child["parent_id"], parent["id"])
        self.login(2)
        state = self.client.get("/api/session", headers=self.headers).json["access"]
        self.assertEqual({item["name"] for item in state["groups"]}, {"BETA-SDC", "Child", "Parent"})
        self.assertFalse(state["is_admin"])
        with self.store.connection() as connection:
            connection.execute("UPDATE group_members SET valid_until=0")
        self.assertFalse(self.client.get("/api/session", headers=self.headers).json["access"]["groups"])
        with self.store.connection() as connection:
            sync_memberships(connection, 2, self.snapshot(state="not_member", members=[]))
            self.assertEqual(connection.execute("SELECT count(*) FROM group_members").fetchone()[0], 0)

    def test_partial_or_failed_sync_preserves_other_teams_and_does_not_extend_memberships(self):
        with self.store.connection() as connection:
            sync_memberships(connection, 2, self.snapshot())
            before = connection.execute("SELECT valid_until FROM group_members WHERE user_id=2").fetchall()
            sync_memberships(connection, 2, {"organization_access": {"state": "unavailable",
                "teams": [], "member_team_ids": [], "structure_complete": False}})
            self.assertEqual(before, connection.execute("SELECT valid_until FROM group_members WHERE user_id=2").fetchall())
            sync_memberships(connection, 1, self.snapshot(teams=[
                {"id": 30, "name": "Other", "slug": "other", "parent_id": None}], members=[30], complete=False))
            self.assertEqual(connection.execute("SELECT archived FROM account_groups WHERE external_key='github:team:20'").fetchone()[0], 0)
            sync_memberships(connection, 1, self.snapshot(teams=[
                {"id": 30, "name": "Other", "slug": "other", "parent_id": None}], members=[30]))
            self.assertEqual(connection.execute("SELECT archived FROM account_groups WHERE external_key='github:team:20'").fetchone()[0], 1)

    def test_team_normalization_rejects_cycles_unknown_members_and_missing_parents(self):
        snapshot = self.snapshot()["organization_access"]
        self.assertEqual(len(organization_access(snapshot)["teams"]), 2)
        snapshot["teams"][1]["parent_id"] = 20
        with self.assertRaises(ValueError):
            organization_access(snapshot)
        snapshot = self.snapshot()["organization_access"]
        snapshot["member_team_ids"] = [999]
        with self.assertRaises(ValueError):
            organization_access(snapshot)
        snapshot = self.snapshot()["organization_access"]
        snapshot["teams"][0]["parent_id"] = 999
        with self.assertRaises(ValueError):
            organization_access(snapshot)

    def test_only_verified_owner_is_automatic_admin_and_expiry_or_demotion_revokes(self):
        snapshot = self.snapshot()
        snapshot["organization_access"]["role"] = "owner"
        with self.store.connection() as connection:
            connection.execute("INSERT INTO github_accounts(user_id,github_id,login) VALUES (2,4321,'fixture-owner')")
            sync_memberships(connection, 2, snapshot)
        self.login(2)
        self.assertEqual(self.overview().status_code, 200)
        self.assertTrue(self.client.get("/api/session", headers=self.headers).json["access"]["is_admin"])
        with self.store.connection() as connection:
            connection.execute("UPDATE github_admin_grants SET valid_until=0")
        self.assertEqual(self.overview().status_code, 403)
        with self.store.connection() as connection:
            sync_memberships(connection, 2, snapshot)
            sync_memberships(connection, 2, self.snapshot())
        self.assertEqual(self.overview().status_code, 403)
        with self.store.connection() as connection:
            sync_memberships(connection, 2, snapshot)
            connection.execute("DELETE FROM github_accounts WHERE user_id=2")
        self.assertEqual(self.overview().status_code, 403)

    def test_owner_expiry_is_six_calendar_months_and_team_expiry_is_independent(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        zone = ZoneInfo("Asia/Shanghai")
        for start, end in (("2026-10-15T20:00:00", "2027-04-15T20:00:00"),
                           ("2023-08-31T20:00:00", "2024-02-29T20:00:00"),
                           ("2026-08-31T20:00:00", "2027-02-28T20:00:00")):
            timestamp = int(datetime.fromisoformat(start).replace(tzinfo=zone).timestamp())
            self.assertEqual(datetime.fromtimestamp(owner_expiry(timestamp), zone).isoformat()[:19], end)
        snapshot = self.snapshot()
        snapshot["organization_access"]["role"] = "owner"
        with self.store.connection() as connection:
            connection.execute("INSERT INTO github_accounts(user_id,github_id,login) VALUES(2,4321,'fixture-owner')")
            sync_memberships(connection, 2, snapshot)
            until = connection.execute("SELECT valid_until FROM github_admin_grants WHERE user_id=2").fetchone()[0]
            self.assertGreater(until - time.time(), 170 * 86400)
            connection.execute("UPDATE group_members SET valid_until=0")
        self.login(2)
        status = self.client.get("/api/session", headers=self.headers).json["access"]
        self.assertTrue(status["is_admin"])
        self.assertFalse(status["groups"])
        self.assertFalse(status["github_owner"]["expired"])
        self.assertEqual(status["github_owner"]["valid_until"], until)

    def test_legacy_active_owner_grants_extend_once_from_original_verification(self):
        now = int(time.time())
        with self.store.connection() as connection:
            connection.execute("INSERT INTO github_admin_grants VALUES(1,?)", (now + 86400,))
            connection.execute("INSERT INTO github_admin_grants VALUES(2,0)")
        self.store.initialize()
        self.store.initialize()
        with self.store.connection() as connection:
            self.assertEqual(connection.execute("SELECT valid_until FROM github_admin_grants WHERE user_id=1").fetchone()[0], owner_expiry(now))
            self.assertEqual(connection.execute("SELECT valid_until FROM github_admin_grants WHERE user_id=2").fetchone()[0], 0)

    def test_broker_org_scope_owner_hierarchy_and_token_never_returned(self):
        responses = [
            {"organization": {"login": "BETA-SDC"}, "state": "active", "role": "admin"},
            [{"id": 20, "name": "Child", "slug": "child", "parent": {"id": 10, "name": "Parent", "slug": "parent"},
              "organization": {"login": "BETA-SDC"}}],
            [{"id": 10, "name": "Parent", "slug": "parent", "parent": None},
             {"id": 20, "name": "Child", "slug": "child", "parent": {"id": 10}}],
        ]
        with patch("server.github_broker.remote_json", side_effect=responses):
            result = fetch_organization("synthetic_private_token", "read:org", time.monotonic() + 20)
        self.assertEqual(result["state"], "member")
        self.assertTrue(result["structure_complete"])
        self.assertEqual(result["member_team_ids"], [20])
        self.assertNotIn("synthetic_private_token", str(result))
        with patch("server.github_broker.remote_json") as remote:
            self.assertEqual(fetch_organization("token", "", time.monotonic() + 20)["state"], "permission_required")
            remote.assert_not_called()
        with patch("server.github_broker.remote_json", side_effect=OSError("permission")):
            self.assertEqual(fetch_organization("token", "read:org", time.monotonic() + 20)["state"], "unavailable")
        with patch("server.github_broker.remote_json", return_value=[]):
            self.assertEqual(fetch_organization("token", "read:org", time.monotonic() + 20)["state"], "permission_required")

    def test_invisible_org_is_not_non_membership_and_public_proof_cannot_grant_owner(self):
        from urllib.error import HTTPError
        with patch("server.github_broker.remote_json",
                   side_effect=HTTPError("https://api.github.com/user/memberships/orgs/BETA-SDC",
                                         404, "Not Found", None, None)):
            self.assertEqual(fetch_organization("token", "read:org", time.monotonic() + 20)["state"], "permission_required")
        with self.store.connection() as connection:
            sync_memberships(connection, 2, {
                "organizations": [{"id": 123, "login": "BETA-SDC"}],
                "organization_access": {"state": "not_member", "teams": [],
                    "member_team_ids": [], "structure_complete": False}})
            self.assertEqual(connection.execute("SELECT state FROM organization_sync WHERE user_id=2").fetchone()[0], "public_member")
            self.assertEqual(connection.execute("SELECT count(*) FROM group_members WHERE user_id=2").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM github_admin_grants WHERE user_id=2").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
