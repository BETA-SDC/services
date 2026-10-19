"""Attendance boundaries, release completeness and stable public imports."""
import ast
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from server.registration import create_app
from server.registration.checkin import POLICY, initialize_checkin, purge_expired
from server.registration.checkins import access, checkin
from server.registration.checkins.domain import BASIC_FIELDS, csv_safe
from server.registration.checkins.http import page_number
from server.registration.checkins.pages import FRONTEND_MODULES
from server.registration.checkins.presentation import public_event
from server.registration.sites import ACCOUNT_HOST

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "server/registration/checkins"
ASSETS = ROOT / "campus-register" if (ROOT / "campus-register").is_dir() else ROOT / "static"


class CheckinArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(Path(self.temp.name) / "accounts.sqlite3",
                              config={"REGISTRATION_ASSETS": ASSETS})
        self.client = self.app.test_client()

    def get(self, path, host=ACCOUNT_HOST):
        return self.client.get(path, headers={
            "Host": host, "X-Forwarded-For": "10.16.125.52", "X-Forwarded-Proto": "https",
        })

    def test_compatibility_interfaces_and_repeated_factories(self):
        self.assertIs(self.app.blueprints["checkin"], checkin)
        self.assertIs(self.app.blueprints["checkin_access"], access)
        other = create_app(Path(self.temp.name) / "other.sqlite3")
        self.assertEqual(
            {(rule.rule, rule.endpoint, tuple(sorted(rule.methods))) for rule in self.app.url_map.iter_rules()},
            {(rule.rule, rule.endpoint, tuple(sorted(rule.methods))) for rule in other.url_map.iter_rules()},
        )
        with self.app.extensions["registration_store"].connection() as connection:
            initialize_checkin(connection)
            initialize_checkin(connection)
            purge_expired(connection)
        self.assertEqual(self.get("/api/checkin/session").json["policy"], POLICY)

    def test_dependencies_are_acyclic_and_never_import_facades(self):
        graph = {}
        controllers = {"approvals", "attendance", "deletion", "events", "pages", "roster", "http"}
        for path in PACKAGE.glob("*.py"):
            if path.stem == "__init__":
                continue
            edges = set()
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.ImportFrom):
                    continue
                self.assertNotIn(node.module, ("checkin", "checkin_access"), str(path))
                if node.level == 1:
                    edges.add(node.module)
            graph[path.stem] = edges
            if path.stem not in controllers:
                self.assertFalse(edges & controllers, (path.stem, edges))

        def visit(name, stack=()):
            self.assertNotIn(name, stack, stack + (name,))
            for dependency in graph.get(name, ()):
                visit(dependency, stack + (name,))
        for name in graph:
            visit(name)

    def test_modules_are_served_as_javascript_with_same_security_headers(self):
        self.assertEqual(FRONTEND_MODULES, {path.stem for path in (ASSETS / "checkin").glob("*.js")})
        for name in FRONTEND_MODULES:
            with self.subTest(name=name):
                response = self.get(f"/checkin-assets/{name}.js")
                try:
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("javascript", response.mimetype)
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    self.assertIn("script-src 'self'", response.headers["Content-Security-Policy"])
                    self.assertEqual(response.data, (ASSETS / "checkin" / f"{name}.js").read_bytes())
                finally:
                    response.close()

    def test_unknown_assets_and_other_hosts_are_rejected(self):
        for path in ("/checkin-assets/secrets.js", "/checkin-assets/../settings.js",
                     "/checkin-assets/%2e%2e%2fsettings.js"):
            self.assertEqual(self.get(path).status_code, 404)
        self.assertEqual(self.get("/checkin-assets/context.js", "profile.betasdc.duckdns.org").status_code, 403)

    def test_static_modules_do_not_open_database_connections(self):
        with patch.object(self.app.extensions["registration_store"], "connection",
                          side_effect=AssertionError("Static asset accessed database")):
            response = self.get("/checkin-assets/context.js")
            try:
                self.assertEqual(response.status_code, 200)
            finally:
                response.close()

    def test_release_manifest_covers_all_modules_and_has_no_missing_paths(self):
        manifest = json.loads((ROOT / "server/checkin-release.json").read_text())
        for key, base in (("files", ROOT), ("assets", ASSETS), ("units", ROOT / "server")):
            paths = manifest[key]
            self.assertEqual(len(paths), len(set(paths)))
            for value in paths:
                self.assertRegex(value, r"^[A-Za-z0-9_./-]+$")
                self.assertNotIn("..", Path(value).parts)
                self.assertFalse(Path(value).is_absolute())
                self.assertTrue((base / value).is_file(), value)
        self.assertEqual(
            {str(path.relative_to(ROOT)) for path in PACKAGE.glob("*.py")},
            {value for value in manifest["files"] if value.startswith("server/registration/checkins/")},
        )
        self.assertEqual(
            {f"checkin/{name}.js" for name in FRONTEND_MODULES},
            {value for value in manifest["assets"] if value.startswith("checkin/")},
        )

    def test_frontend_dependencies_exist_and_are_acyclic(self):
        graph = {}
        for path in (ASSETS / "checkin").glob("*.js"):
            graph[path.stem] = set(re.findall(r'from "\./(\w+)\.js"|import "\./(\w+)\.js"',
                                             path.read_text(encoding="utf-8")))
        graph = {name: {left or right for left, right in values} for name, values in graph.items()}

        def visit(name, stack=()):
            self.assertIn(name, graph)
            self.assertNotIn(name, stack, stack + (name,))
            for dependency in graph[name]:
                visit(dependency, stack + (name,))
        for name in graph:
            visit(name)

    def test_public_serialization_reuses_connection_and_hides_secrets(self):
        store = self.app.extensions["registration_store"]
        event = {"id": "test", "creator_id": 999, "title": "Test", "location": "",
                 "purpose": "Test", "starts_at": 1, "ends_at": 2, "closed": 0,
                 "fields_json": '["email"]', "token_cipher": "must-not-leak"}
        with self.app.app_context(), store.connection() as connection:
            with patch.object(store, "connection", side_effect=AssertionError("Nested connection")):
                result = public_event(connection, event, {"custom_fields": []})
        self.assertNotIn("token_cipher", result)
        self.assertEqual(result["fields"], ["email"])
        self.assertEqual(result["policy"], POLICY)

    def test_shared_pagination_validation(self):
        for value, expected in (("0", 1), ("-1", 1), ("3", 3), ("100001", 10000)):
            with self.app.test_request_context(query_string={"page": value}):
                self.assertEqual(page_number(maximum=10000), expected)
        with self.app.test_request_context(query_string={"page": "bad"}):
            with self.assertRaisesRegex(Exception, "422"):
                page_number()

    def test_basic_policy_and_csv_formula_protection(self):
        self.assertEqual(BASIC_FIELDS, {"name", "email", "student_id"})
        for text in ("=SUM(A1)", " +1", "-1", "@name"):
            self.assertEqual(csv_safe(text), "'" + text)
        self.assertEqual(csv_safe("S20260001"), "S20260001")
