"""Whole-account composition, persistence and public module regression checks."""
import ast
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from server.registration import create_app
from server.registration.assets import ACCOUNT_MODULES, SHARED_MODULES
from server.registration.access_control.repository import populate_user_access
from server.registration.features import API_FEATURES, SITE_FEATURES
from server.registration.private_data import decrypt, encrypt
from server.registration.privacy.crypto import encrypt as feature_encrypt
from server.registration.passkeys import user_handle
from server.registration.webauthn.protocol import user_handle as feature_handle
from server.mail_worker import deliver_once
from server.mailer.worker import deliver_once as worker_deliver

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "campus-register" if (ROOT / "campus-register").is_dir() else ROOT / "static"
PACKAGES = ("access_control", "privacy", "site_accounts", "storage", "webauthn")


class ServiceArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "accounts.sqlite3"
        self.app = create_app(self.path, config={"REGISTRATION_ASSETS": ASSETS})
        self.store = self.app.extensions["registration_store"]
        self.client = self.app.test_client()
        self.headers = {"Host": "account.betasdc.duckdns.org",
                        "X-Forwarded-Proto": "https", "X-Forwarded-For": "10.16.125.52"}

    def test_feature_registry_and_compatibility_interfaces(self):
        self.assertEqual(set(self.app.blueprints),
                         {feature.name for feature in API_FEATURES + SITE_FEATURES})
        self.assertIs(encrypt, feature_encrypt)
        self.assertIs(user_handle, feature_handle)
        self.assertIs(deliver_once, worker_deliver)
        with self.app.app_context():
            value = encrypt(1, "phone", "13800138000")
            self.assertEqual(decrypt(1, "phone", value), "13800138000")
        self.store.initialize()
        with self.store.connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 11)

    def test_feature_internal_dependencies_are_acyclic(self):
        directories = [ROOT / "server/registration" / package for package in PACKAGES]
        for directory in (*directories, ROOT / "server/mailer"):
            graph = {}
            for path in directory.glob("*.py"):
                if path.stem == "__init__":
                    continue
                edges = set()
                for node in ast.walk(ast.parse(path.read_text())):
                    if isinstance(node, ast.ImportFrom) and node.level == 1:
                        edges.update([node.module.split(".")[0]] if node.module
                                     else [alias.name for alias in node.names])
                graph[path.stem] = edges
            self.assertTrue(graph, directory)
            self.assert_acyclic(graph)

    def assert_acyclic(self, graph):
        def visit(name, stack=()):
            self.assertIn(name, graph, (name, stack))
            self.assertNotIn(name, stack, stack + (name,))
            for dependency in graph[name]:
                visit(dependency, stack + (name,))
        for name in graph:
            visit(name)

    def test_account_and_shared_modules_are_explicit_and_static(self):
        for folder, allowed in (("account", ACCOUNT_MODULES), ("shared", SHARED_MODULES)):
            self.assertEqual({path.stem for path in (ASSETS / folder).glob("*.js")}, allowed)
            with patch.object(self.store, "connection", side_effect=AssertionError("Static SQL")):
                for name in allowed:
                    with self.subTest(folder=folder, name=name):
                        response = self.client.get(f"/{folder}/{name}.js", headers=self.headers)
                        try:
                            self.assertEqual(response.status_code, 200)
                            self.assertIn("javascript", response.mimetype)
                            self.assertEqual(response.data, (ASSETS / folder / f"{name}.js").read_bytes())
                            self.assertEqual(response.headers["Cache-Control"], "no-store")
                            self.assertIn("script-src 'self'", response.headers["Content-Security-Policy"])
                        finally:
                            response.close()
        for path in ("/account/secrets.js", "/shared/secrets.js", "/account/%2e%2e%2fauth.py"):
            self.assertEqual(self.client.get(path, headers=self.headers).status_code, 404)

    def test_frontend_dependencies_are_acyclic_and_cannot_escape_assets(self):
        graph = {}
        for directory in ("account", "shared", "checkin"):
            for path in (ASSETS / directory).glob("*.js"):
                dependencies = re.findall(r'(?:from\s+|import\s*)["\'](\.[^"\']+)["\']',
                                          path.read_text())
                graph[path.resolve()] = {(path.parent / dependency).resolve() for dependency in dependencies}
        self.assert_acyclic(graph)
        html = (ASSETS / "index.html").read_text()
        self.assertIn('src="/main.js" type="module"', html)
        self.assertNotIn('src="/settings.js"', html)
        self.assertNotIn('src="/private.js"', html)

    def test_admin_access_reads_are_bounded_and_preserve_expiry_rules(self):
        now = 1000
        with self.store.connection() as connection:
            connection.executemany(
                "INSERT INTO registrations(id,username,email) VALUES(?,?,?)",
                [(identifier, f"user{identifier}", f"user{identifier}@westlake.edu.cn") for identifier in range(1, 51)])
            connection.execute("INSERT INTO account_groups(id,name,source) VALUES(1,'Test','local')")
            connection.executemany("INSERT INTO group_members VALUES(?,1,'local',?)",
                                   [(1, None), (2, now), (3, now + 1)])
            connection.executemany("INSERT INTO github_accounts(user_id,github_id,login) VALUES(?,?,?)",
                                   [(1, 101, "owner"), (2, 102, "expired")])
            connection.executemany("INSERT INTO github_admin_grants VALUES(?,?)",
                                   [(1, now + 1), (2, now), (3, now + 1)])
            users = [{"id": identifier, "role": "admin" if identifier == 4 else "member"}
                     for identifier in range(1, 51)]
            queries = []
            connection.set_trace_callback(queries.append)
            populate_user_access(connection, users, now)
            connection.set_trace_callback(None)
            self.assertEqual(len(queries), 2)
            self.assertEqual(users[0]["role_source"], "github_owner")
            self.assertEqual(users[0]["group_ids"], [1])
            self.assertEqual(users[1]["role"], "member")
            self.assertEqual(users[1]["group_ids"], [])
            self.assertEqual(users[2]["role"], "member")  # Detached identity grants nothing.
            self.assertEqual(users[2]["group_ids"], [1])
            self.assertEqual(users[3]["role"], "admin")
            queries.clear()
            connection.set_trace_callback(queries.append)
            populate_user_access(connection, [], now)
            self.assertEqual(queries, [])

    def test_csrf_errors_are_distinct_from_permission_errors(self):
        response = self.client.post("/api/logout", json={}, headers=self.headers)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json["code"], "csrf_failed")
        csrf = self.client.get("/api/session", headers=self.headers).json["csrf_token"]
        response = self.client.post("/api/logout", json={},
                                    headers={**self.headers, "X-CSRF-Token": csrf})
        self.assertEqual(response.status_code, 200)

    def test_session_response_resolves_identity_once_without_cross_request_cache(self):
        with patch.object(self.store, "session_account", wraps=self.store.session_account) as resolve:
            first = self.client.get("/api/session", headers=self.headers)
            second = self.client.get("/api/session", headers=self.headers)
        self.assertEqual(resolve.call_count, 2)
        self.assertIsNone(first.json["user"])
        self.assertIsNone(second.json["user"])
        self.assertFalse(second.json["access"]["is_admin"])

    def test_release_contains_all_runtime_modules_and_frontend_assets(self):
        manifest = json.loads((ROOT / "server/service-release.json").read_text())
        files = set(manifest["files"])
        assets = set(manifest["assets"])
        for folder in ("server/registration", "server/mailer"):
            expected = {str(path.relative_to(ROOT)) for path in (ROOT / folder).rglob("*.py")}
            self.assertTrue(expected <= files, expected - files)
        expected = {str(path.relative_to(ASSETS)) for folder in ("account", "shared", "checkin")
                    for path in (ASSETS / folder).glob("*.js")}
        self.assertTrue(expected <= assets, expected - assets)
        units = ROOT / "server" if (ROOT / "campus-register").is_dir() else Path("/etc/systemd/system")
        for category, base in (("files", ROOT), ("assets", ASSETS), ("units", units)):
            self.assertEqual(len(manifest[category]), len(set(manifest[category])))
            for value in manifest[category]:
                self.assertRegex(value, r"^[A-Za-z0-9_./-]+$")
                self.assertNotIn("..", Path(value).parts)
                self.assertFalse(Path(value).is_absolute())
                self.assertTrue((base / value).is_file(), value)
