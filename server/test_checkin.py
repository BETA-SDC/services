import csv
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
import io
import json
import re
import sqlite3
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
from xml.etree import ElementTree

from server.registration import create_app
from server.registration.checkin import POLICY, ZONE, initialize_checkin, purge_expired
from server.registration.private_data import encrypt, POLICY as PRIVATE_POLICY
from server.registration.sites import ACCOUNT_HOST
from server.system_mail import read_message


class CheckinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "accounts.sqlite3"
        self.calendar = Path(self.temp.name) / "calendar"
        self.app = create_app(self.path, networks=("10.16.112.0/20",),
                              config={"CHECKIN_CALENDAR_STATE": str(self.calendar)})
        self.store = self.app.extensions["registration_store"]
        self.pats = {}
        for name in ("creator", "outsider", "other-member"):
            self.store.add(name, name + "@westlake.edu.cn")
        with self.store.connection() as connection:
            connection.execute("UPDATE registrations SET email_verified_at=1,real_name='Test real name'")
            connection.execute("INSERT INTO github_accounts(user_id,github_id,login) VALUES(1,101,'creator'),(3,103,'other')")
            connection.execute("INSERT INTO account_groups(id,name,source,external_key) "
                               "VALUES(1,'BETA-SDC','github','github:org:BETA-SDC')")
            connection.executemany("INSERT INTO group_members VALUES(?,1,'github',?)",
                                   [(1, int(time.time()) + 86400), (3, int(time.time()) + 86400)])
            connection.execute("INSERT INTO account_roles VALUES(1,'admin')")
        self.owner = self.client(1)
        self.guest = self.client()
        self.other = self.client(3)
        self.outsider = self.client(2)

    def tearDown(self):
        self.temp.cleanup()

    def test_read_handlers_do_not_reserve_writer_lock(self):
        event, join_path = self.event()
        statements = []
        original = self.store.connection

        @contextmanager
        def traced_connection():
            with original() as connection:
                connection.set_trace_callback(statements.append)
                yield connection

        with patch.object(self.store, "connection", traced_connection):
            for client, path in (
                (self.owner, f"/api/checkin/events/{event['id']}"),
                (self.guest, join_path),
                (self.owner, f"/api/checkin/events/{event['id']}/access-requests"),
            ):
                response = self.get(client, path)
                self.assertEqual(response.status_code, 200, response.json)
        self.assertFalse(any(sql.upper().startswith("BEGIN IMMEDIATE") for sql in statements))

    def client(self, user_id=None):
        client = self.app.test_client()
        if user_id:
            with client.session_transaction(base_url="https://" + ACCOUNT_HOST) as session:
                session["auth_token"] = self.store.create_session(user_id, 3600)
        result = client.get("/api/checkin/session", headers=self.base_headers())
        self.assertEqual(result.status_code, 200, result.json)
        client.csrf = result.json["csrf_token"]
        return client

    def base_headers(self):
        return {"Host": ACCOUNT_HOST, "X-Forwarded-For": "10.16.125.52", "X-Forwarded-Proto": "https"}

    def get(self, client, path, **headers):
        return client.get(path, headers=self.base_headers() | headers)

    def send(self, client, path, data, method="POST", **headers):
        return client.open(path, method=method, json=data,
                           headers=self.base_headers() | {"X-CSRF-Token": client.csrf} | headers)

    def event(self, fields=None, **changes):
        now = int(time.time())
        data = {"title": "测试活动 / Test meeting", "location": "E12",
                "purpose": "本次活动出勤统计", "starts_at": now - 30, "ends_at": now + 3600,
                "fields": fields or ["name", "email"]}
        data.update(changes)
        result = self.send(self.owner, "/api/checkin/events", data)
        self.assertEqual(result.status_code, 201, result.json)
        event = result.json["event"]
        token = urlsplit(event["join_url"]).path.rsplit("/", 1)[1]
        return event, "/api/checkin/join/" + token

    def answer(self, client, path, **changes):
        data = {"values": {"name": "Synthetic attendee", "email": "attendee@example.org"},
                "consent": True, "policy": POLICY}
        data.update(changes)
        return self.send(client, path, data)

    def factor(self, client):
        if self.get(client, "/api/security").json["verification"]:
            return
        result = self.send(client, "/api/security/email/send", {})
        self.assertEqual(result.status_code, 200, result.json)
        with self.store.connection() as connection:
            code = connection.execute("SELECT code FROM second_factor_outbox WHERE challenge_hash=?",
                                      (self.store.token_hash(result.json["challenge"]),)).fetchone()[0]
        result = self.send(client, "/api/security/email/verify", {"challenge": result.json["challenge"], "code": code})
        self.assertEqual(result.status_code, 200, result.json)

    def pat_headers(self, client, event, allow_export=False):
        key = (id(client), event["id"], allow_export)
        if key not in self.pats:
            result = self.send(client, f"/api/checkin/events/{event['id']}/access-requests", {"reason": "Synthetic read test"})
            self.assertEqual(result.status_code, 201, result.json)
            self.factor(self.owner)
            request_id = result.json["id"]
            review = self.send(self.owner, f"/api/checkin/access-requests/{request_id}/review", {
                "decision": "approve", "note": "", "expires_at": int(time.time()) + 3600, "allow_export": allow_export})
            self.assertEqual(review.status_code, 200, review.json)
            with self.store.connection() as connection:
                connection.row_factory = sqlite3.Row
                job = connection.execute("SELECT * FROM system_mail_outbox WHERE grant_id=? AND kind='checkin.access.issued'",
                                         (request_id,)).fetchone()
            content = read_message(self.app.extensions["private_cipher"], job)
            self.pats[key] = re.search(r"beta_checkin_[A-Za-z0-9_-]{43}", content["body"])[0]
        return {"X-Checkin-PAT": self.pats[key]}

    def test_admin_management_and_member_creator_isolation(self):
        event, path = self.event()
        self.assertTrue(self.get(self.owner, "/api/checkin/session").json["can_manage"])
        for client, expected in ((self.guest, 401), (self.outsider, 403)):
            self.assertEqual(self.get(client, "/api/checkin/events").status_code, expected)
        for suffix in ("", "/qr.svg"):
            self.assertEqual(self.get(self.other, "/api/checkin/events/" + event["id"] + suffix).status_code, 404)
        self.assertEqual(self.get(self.other, "/api/checkin/events/" + event["id"] + "/entries").status_code, 200)
        public = self.get(self.guest, path).json
        self.assertEqual(public["event"]["organizer"], "Test real name")
        self.assertNotIn("join_url", public["event"])
        self.assertNotIn("creator_id", public["event"])
        self.assertIsNone(public["receipt"])

    def test_organizer_uses_real_name_in_public_and_management_views(self):
        with self.store.connection() as connection:
            connection.execute("UPDATE registrations SET real_name='真实姓名',nickname='昵称' WHERE id=1")
        event, path = self.event()
        self.assertEqual(event["organizer"], "真实姓名")
        self.assertEqual(self.get(self.guest, path).json["event"]["organizer"], "真实姓名")
        listed = self.get(self.owner, "/api/checkin/events").json["events"]
        self.assertEqual(listed[0]["organizer"], "真实姓名")
        for missing in ("", "   "):
            with self.subTest(real_name=missing), self.store.connection() as connection:
                connection.execute("UPDATE registrations SET real_name=? WHERE id=1", (missing,))
            self.assertEqual(self.get(self.guest, path).json["event"]["organizer"], "BETA-SDC 活动发起人")

    def test_local_admin_independent_and_expired_unlinked_members_cannot_create(self):
        with self.store.connection() as connection:
            connection.execute("UPDATE group_members SET valid_until=1 WHERE user_id=1")
        self.assertTrue(self.get(self.owner, "/api/checkin/session").json["can_manage"])
        with self.store.connection() as connection:
            connection.execute("DELETE FROM account_roles WHERE user_id=1")
        self.assertFalse(self.get(self.owner, "/api/checkin/session").json["can_create"])
        with self.store.connection() as connection:
            connection.execute("UPDATE group_members SET valid_until=? WHERE user_id=1", (int(time.time()) + 1000,))
            connection.execute("DELETE FROM github_accounts WHERE user_id=1")
        self.assertFalse(self.get(self.owner, "/api/checkin/session").json["can_create"])
        with self.store.connection() as connection:
            connection.execute("UPDATE account_groups SET archived=1")
        self.assertFalse(self.get(self.other, "/api/checkin/session").json["can_create"])

    def test_owner_grant_counts_as_member(self):
        with self.store.connection() as connection:
            connection.execute("DELETE FROM group_members WHERE user_id=1")
            connection.execute("INSERT INTO github_admin_grants VALUES(1,?)", (int(time.time()) + 1000,))
        self.assertTrue(self.get(self.owner, "/api/checkin/session").json["can_manage"])

    def test_guest_submit_server_timestamp_idempotence_and_private_receipt(self):
        event, path = self.event()
        before = int(time.time())
        result = self.answer(self.guest, path)
        self.assertEqual(result.status_code, 201, result.json)
        self.assertGreaterEqual(result.json["receipt"]["checked_at"], before)
        self.assertLessEqual(result.json["receipt"]["checked_at"], int(time.time()))
        self.assertEqual(self.answer(self.guest, path).status_code, 200)
        self.assertEqual(self.get(self.guest, path).json["receipt"], result.json["receipt"])
        another = self.client()
        self.assertIsNone(self.get(another, path).json["receipt"])
        self.assertEqual(self.answer(another, path).status_code, 409)
        entries = self.get(self.owner, f"/api/checkin/events/{event['id']}/entries", **self.pat_headers(self.owner, event)).json
        self.assertEqual(entries["total"], 1)
        self.assertFalse(entries["entries"][0]["registered"])
        self.assertEqual(self.answer(self.guest, path, checked_at=1).status_code, 422)

    def test_registered_account_dedupes_even_with_different_answers(self):
        event, path = self.event()
        result = self.answer(self.outsider, path)
        self.assertEqual(result.status_code, 201)
        second = self.answer(self.outsider, path, values={"name": "Changed", "email": "changed@example.org"})
        self.assertTrue(second.json["already_checked"])
        self.assertEqual(second.json["receipt"], result.json["receipt"])
        rows = self.get(self.owner, f"/api/checkin/events/{event['id']}/entries", **self.pat_headers(self.owner, event)).json
        self.assertEqual(rows["total"], 1)
        self.assertTrue(rows["entries"][0]["registered"])

    def test_plaintext_and_extra_unrequested_fields_not_stored(self):
        event, path = self.event(["name", "phone", "student_id"])
        values = {"name": "Unique private attendee", "phone": "13800138000", "student_id": "S202612345"}
        self.assertEqual(self.answer(self.guest, path, values=values | {"document": "no"}).status_code, 422)
        self.assertEqual(self.answer(self.guest, path, values=values).status_code, 201)
        raw = self.path.read_bytes()
        for value in values.values():
            self.assertNotIn(value.encode(), raw)
        token = path.rsplit("/", 1)[1]
        self.assertNotIn(token.encode(), raw)
        result = self.get(self.owner, f"/api/checkin/events/{event['id']}/entries", **self.pat_headers(self.owner, event))
        self.assertEqual(result.json["entries"][0]["values"], values)
        with self.store.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM private_fields").fetchone()[0], 0)

    def test_invalid_answers_consent_and_phone(self):
        _, path = self.event(["name", "phone"])
        for phone in ("+8613800138000", "12800138000", "1380013800", "", None):
            self.assertEqual(self.answer(self.guest, path, values={"name": "Test", "phone": phone}).status_code, 422)
        for changes in ({"consent": False}, {"consent": 1}, {"policy": "old"}, {"values": []},
                        {"values": {"name": "A", "phone": "13800138000", "time": 1}}):
            self.assertEqual(self.answer(self.guest, path, **changes).status_code, 422)

    def test_pause_resume_end_and_rotation(self):
        event, path = self.event()
        endpoint = "/api/checkin/events/" + event["id"]
        self.assertEqual(self.send(self.owner, endpoint, {"closed": True}, "PATCH").json["event"]["status"], "closed")
        self.assertEqual(self.answer(self.guest, path).status_code, 409)
        self.assertEqual(self.send(self.owner, endpoint, {"closed": False}, "PATCH").status_code, 200)
        rotated = self.send(self.owner, endpoint, {"rotate": True}, "PATCH").json["event"]
        self.assertEqual(self.get(self.guest, path).status_code, 404)
        new_path = "/api/checkin/join/" + rotated["join_url"].rsplit("/", 1)[1]
        self.assertEqual(self.answer(self.guest, new_path).status_code, 201)
        with self.store.connection() as connection:
            connection.execute("UPDATE checkin_events SET ends_at=?", (int(time.time()) - 1,))
        self.assertEqual(self.answer(self.client(), new_path).status_code, 409)
        self.assertEqual(self.get(self.guest, new_path).json["receipt"]["id"],
                         self.get(self.guest, new_path).json["receipt"]["id"])

    def test_event_validation_and_future_start(self):
        now = int(time.time())
        event, path = self.event(starts_at=now + 100, ends_at=now + 3600)
        self.assertEqual(self.get(self.guest, path).json["event"]["status"], "scheduled")
        self.assertEqual(self.answer(self.guest, path).status_code, 409)
        base = {"title": "A", "location": "", "purpose": "B", "fields": ["email"],
                "starts_at": now, "ends_at": now + 100}
        for changes in ({"fields": ["name"]}, {"fields": ["email", "email"]}, {"fields": ["unknown"]},
                        {"fields": [[]]}, {"title": ""}, {"purpose": ""},
                        {"ends_at": now - 1}, {"ends_at": now + 8 * 86400},
                        {"starts_at": True}, {"unknown": 1}):
            result = self.send(self.owner, "/api/checkin/events", base | changes)
            self.assertEqual(result.status_code, 422, result.json)

    def test_prefill_only_requested_fields_and_single_use_session_binding(self):
        _, path = self.event(["name", "phone"])
        with self.app.app_context(), self.store.connection() as connection:
            for field, value in {"phone": "13800138000", "student_id": "S999999", "document": {"secret": "not shared"}}.items():
                connection.execute("INSERT INTO private_fields VALUES(?,?,?,?)", (2, field, encrypt(2, field, value), int(time.time())))
        context = {"consent": True, "policy": POLICY}
        self.assertEqual(self.send(self.outsider, path + "/prefill/authorize", context).status_code, 401)
        self.factor(self.outsider)
        self.assertEqual(self.send(self.outsider, path + "/prefill/authorize", {"consent": False, "policy": POLICY}).status_code, 422)
        grant = self.send(self.outsider, path + "/prefill/authorize", context).json["token"]
        result = self.send(self.outsider, path + "/prefill", {"token": grant})
        self.assertEqual(result.json["values"], {"name": "Test real name", "phone": "13800138000"})
        self.assertEqual(self.send(self.outsider, path + "/prefill", {"token": grant}).status_code, 409)
        grant = self.send(self.outsider, path + "/prefill/authorize", context).json["token"]
        other_browser = self.client(2)
        self.factor(other_browser)
        self.assertEqual(self.send(other_browser, path + "/prefill", {"token": grant}).status_code, 409)
        with self.store.connection() as connection:
            connection.execute("UPDATE checkin_grants SET expires_at=1")
        self.assertEqual(self.send(self.outsider, path + "/prefill", {"token": grant}).status_code, 409)
        self.assertEqual(self.send(self.guest, path + "/prefill", {"token": grant}).status_code, 401)

    def test_prefill_wrong_event_and_revoked_factor(self):
        _, path = self.event(["phone"])
        _, other_path = self.event(["phone"])
        self.factor(self.outsider)
        grant = self.send(self.outsider, path + "/prefill/authorize", {"consent": True, "policy": POLICY}).json["token"]
        self.assertEqual(self.send(self.outsider, other_path + "/prefill", {"token": grant}).status_code, 409)
        with self.store.connection() as connection:
            connection.execute("DELETE FROM second_factor_proofs")
        self.assertEqual(self.send(self.outsider, path + "/prefill", {"token": grant}).status_code, 401)

    def test_no_private_data_prefill_does_not_need_stepup(self):
        _, path = self.event()
        grant = self.send(self.outsider, path + "/prefill/authorize", {"consent": True, "policy": POLICY}).json["token"]
        result = self.send(self.outsider, path + "/prefill", {"token": grant})
        self.assertEqual(result.json["values"], {"name": "Test real name", "email": "outsider@westlake.edu.cn"})

    def test_student_prefill_without_stepup_preserves_scope_and_single_use(self):
        _, path = self.event(["name", "email", "student_id"])
        with self.app.app_context(), self.store.connection() as connection:
            for field, value in {"student_id": "S20260001", "phone": "13800138000"}.items():
                connection.execute("INSERT INTO private_fields VALUES(?,?,?,?)",
                                   (2, field, encrypt(2, field, value), int(time.time())))
        grant = self.send(self.outsider, path + "/prefill/authorize",
                          {"consent": True, "policy": POLICY})
        self.assertEqual(grant.status_code, 200, grant.json)
        token = grant.json["token"]
        result = self.send(self.outsider, path + "/prefill", {"token": token})
        self.assertEqual(result.json["values"],
                         {"name": "Test real name", "email": "outsider@westlake.edu.cn", "student_id": "S20260001"})
        self.assertEqual(self.send(self.outsider, path + "/prefill", {"token": token}).status_code, 409)
        grant = self.send(self.outsider, path + "/prefill/authorize",
                          {"consent": True, "policy": POLICY}).json["token"]
        changed = self.outsider.put("/api/profile/student-id",
                                   headers=self.base_headers() | {"X-CSRF-Token": self.get(self.outsider, "/api/checkin/session").json["csrf_token"]},
                                   json={"student_id": "S20260002"})
        self.assertEqual(changed.status_code, 200, changed.json)
        self.assertEqual(self.send(self.outsider, path + "/prefill", {"token": grant}).status_code, 409)

    def test_profile_edit_revokes_outstanding_prefill(self):
        _, path = self.event(["phone"])
        self.factor(self.outsider)
        grant = self.send(self.outsider, path + "/prefill/authorize", {"consent": True, "policy": POLICY}).json["token"]
        edit = self.send(self.outsider, "/api/private/authorize",
                         {"consent": True, "policy": PRIVATE_POLICY, "fields": ["phone"], "purpose": "edit"})
        self.assertEqual(edit.status_code, 200)
        self.assertEqual(self.send(self.outsider, "/api/private/edit",
                                  {"token": edit.json["token"], "values": {"phone": "13800138000"}}).status_code, 200)
        self.assertEqual(self.send(self.outsider, path + "/prefill", {"token": grant}).status_code, 409)

    def test_pagination_and_event_scoped_duplicate_identifiers(self):
        first, path = self.event(["email"])
        second, other_path = self.event(["email"])
        self.assertEqual(self.answer(self.guest, path, values={"email": "same@example.org"}).status_code, 201)
        self.assertEqual(self.answer(self.guest, other_path, values={"email": "same@example.org"}).status_code, 201)
        with self.app.app_context(), self.store.connection() as connection:
            for i in range(51):
                entry_id = f"fixture-{i}"
                values = {"email": f"test-{i}@example.org"}
                connection.execute("INSERT INTO checkin_entries VALUES(?,?,NULL,?,?,?)",
                                   (entry_id, first["id"], encrypt(first["id"], "checkin:entry:" + entry_id, values),
                                    int(time.time()), POLICY))
        endpoint = "/api/checkin/events/" + first["id"] + "/entries"
        headers = self.pat_headers(self.owner, first)
        page_one = self.get(self.owner, endpoint, **headers).json
        page_two = self.get(self.owner, endpoint + "?page=2", **headers).json
        self.assertEqual(page_one["total"], 52)
        self.assertEqual(len(page_one["entries"]), 50)
        self.assertEqual(len(page_two["entries"]), 2)
        self.assertFalse(set(e["id"] for e in page_one["entries"]) & set(e["id"] for e in page_two["entries"]))

    def test_qr_svg_is_local_and_requires_creator(self):
        event, _ = self.event()
        response = self.get(self.owner, event["qr_url"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/svg+xml")
        self.assertEqual(ElementTree.fromstring(response.data).tag, "{http://www.w3.org/2000/svg}svg")
        self.assertNotIn(b"<script", response.data)
        self.assertEqual(self.get(self.guest, event["qr_url"]).status_code, 401)

    def test_csv_formula_protection_and_export_stepup(self):
        event, path = self.event()
        self.answer(self.guest, path, values={"name": "=HYPERLINK(\"evil\")", "email": "person@example.org"})
        export_path = f"/api/checkin/events/{event['id']}/entries?format=csv"
        self.assertEqual(self.get(self.owner, export_path).status_code, 403)
        result = self.get(self.owner, export_path, **self.pat_headers(self.owner, event, allow_export=True))
        self.assertEqual(result.status_code, 200)
        rows = list(csv.reader(io.StringIO(result.data.decode("utf-8-sig"))))
        self.assertEqual(rows[1][3], "'=HYPERLINK(\"evil\")")
        self.assertEqual(result.headers["Cache-Control"], "no-store")

    def test_attendance_never_expires_and_only_expired_grants_are_purged(self):
        event, path = self.event()
        self.answer(self.guest, path)
        with self.store.connection() as connection:
            connection.execute("UPDATE checkin_events SET retain_until=1")
        self.assertEqual(self.get(self.guest, path).status_code, 200)
        with self.store.connection() as connection:
            connection.execute("INSERT INTO checkin_grants VALUES('expired',?,2,'session',1,NULL)", (event["id"],))
            purge_expired(connection, now=int(time.time()) + 365 * 86400)
            for table in ("checkin_events", "checkin_entries", "checkin_identities"):
                self.assertEqual(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM checkin_grants").fetchone()[0], 0)
        public = self.get(self.guest, path).json["event"]
        self.assertIsNone(public["retain_until"])
        self.assertEqual(public["retention"], "until_admin_deletion")
        self.factor(self.owner)
        self.assertEqual(self.send(self.owner, "/api/checkin/events/" + event["id"], {}, "DELETE").status_code, 200)
        self.assertEqual(self.get(self.guest, path).status_code, 404)

    def test_network_origin_csrf_and_account_host_boundary(self):
        event, path = self.event()
        self.assertEqual(self.answer(self.guest, path).status_code, 201)
        self.assertEqual(self.send(self.guest, path, {}, **{"X-CSRF-Token": "bad"}).status_code, 403)
        self.assertEqual(self.get(self.guest, path, **{"X-Forwarded-For": "8.8.8.8"}).status_code, 403)
        self.assertEqual(self.send(self.guest, path, {}, Origin="https://evil.example").status_code, 403)
        page = "/checkin/join/" + path.rsplit("/", 1)[1]
        response = self.get(self.guest, page, **{"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"})
        self.assertEqual(response.status_code, 200)
        response.close()
        self.assertEqual(self.get(self.guest, path, Host="calendar.betasdc.duckdns.org").status_code, 403)

    def test_missing_key_fails_closed_even_without_profile_private_fields(self):
        self.event()
        self.path.with_suffix(".private-key").unlink()
        with self.assertRaises(RuntimeError):
            create_app(self.path)

    def calendar_snapshot(self):
        path = self.calendar / "current/server-events.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        items = [{"uid": "internal-calendar-id", "title": "Internal workshop", "location": "E12",
                  "start": "2026-10-17T19:00:00+08:00", "end": "2026-10-17T21:00:00+08:00",
                  "allDay": False, "scope": "internal", "status": "tentative",
                  "description": "Do not disclose the full calendar object"}]
        path.write_text(json.dumps({"events": items}), encoding="utf-8")
        return path

    def test_calendar_selection_member_only_and_minimal(self):
        self.calendar_snapshot()
        for client, expected in ((self.guest, 401), (self.outsider, 403)):
            self.assertEqual(self.get(client, "/api/checkin/calendar-events").status_code, expected)
        result = self.get(self.owner, "/api/checkin/calendar-events?date=2026-10-17")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["events"][0]["uid"], "internal-calendar-id")
        self.assertNotIn("description", result.json["events"][0])

    def test_calendar_date_defaults_to_beijing_today_and_rejects_invalid_dates(self):
        path = self.calendar_snapshot()
        today = datetime.now(ZONE).date()
        tomorrow = today + timedelta(days=1)
        items = [{"uid": day.isoformat(), "title": "All-day event", "start": day.isoformat(),
                  "end": (day + timedelta(days=1)).isoformat(), "allDay": True}
                 for day in (today, tomorrow)]
        path.write_text(json.dumps({"events": items}), encoding="utf-8")
        result = self.get(self.owner, "/api/checkin/calendar-events")
        self.assertEqual(result.json["date"], today.isoformat())
        self.assertEqual([item["uid"] for item in result.json["events"]], [today.isoformat()])
        result = self.get(self.owner, "/api/checkin/calendar-events?date=" + tomorrow.isoformat())
        self.assertEqual([item["uid"] for item in result.json["events"]], [tomorrow.isoformat()])
        for day in ("", "today", "2026-02-30", "2026-1-1", "9999-12-31"):
            self.assertEqual(self.get(self.owner, "/api/checkin/calendar-events?date=" + day).status_code, 422)

    def test_calendar_date_overlap_midnight_all_day_and_utc(self):
        path = self.calendar_snapshot()
        items = [
            {"uid": "overnight", "start": "2026-10-16T23:00:00+08:00", "end": "2026-10-17T01:00:00+08:00"},
            {"uid": "midnight-end", "start": "2026-10-16T23:00:00+08:00", "end": "2026-10-17T00:00:00+08:00"},
            {"uid": "all-day", "start": "2026-10-17", "end": "2026-10-18", "allDay": True},
            {"uid": "all-day-no-end", "start": "2026-10-17", "allDay": True},
            {"uid": "utc", "start": "2026-10-16T17:00:00Z", "end": "2026-10-16T18:00:00Z"},
            {"uid": "point", "start": "2026-10-17T12:00:00+08:00"},
            {"uid": "tomorrow", "start": "2026-10-18T00:00:00+08:00"},
        ]
        path.write_text(json.dumps({"events": [{"title": item["uid"]} | item for item in items]}), encoding="utf-8")
        result = self.get(self.owner, "/api/checkin/calendar-events?date=2026-10-17")
        self.assertEqual({item["uid"] for item in result.json["events"]},
                         {"overnight", "all-day", "all-day-no-end", "utc", "point"})
        result = self.get(self.owner, "/api/checkin/calendar-events?date=2026-10-18")
        self.assertEqual([item["uid"] for item in result.json["events"]], ["tomorrow"])

    def test_calendar_date_malformed_event_fails_without_exposing_other_events(self):
        path = self.calendar_snapshot()
        path.write_text(json.dumps({"events": [{"uid": "bad", "title": "Bad", "start": "bad-date"}]}), encoding="utf-8")
        self.assertEqual(self.get(self.owner, "/api/checkin/calendar-events?date=2026-10-17").status_code, 503)

    def test_calendar_link_validated_private_and_retained_when_source_removed(self):
        snapshot = self.calendar_snapshot()
        original = snapshot.read_bytes()
        event, path = self.event(calendar_uid="internal-calendar-id")
        self.assertEqual(event["calendar_uid"], "internal-calendar-id")
        self.assertNotIn("calendar_uid", self.get(self.guest, path).json["event"])
        self.assertEqual(snapshot.read_bytes(), original)
        snapshot.unlink()
        result = self.get(self.owner, "/api/checkin/events/" + event["id"])
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["event"]["calendar_uid"], "internal-calendar-id")
        self.event()  # No snapshot is required for manual creation.
        self.assertEqual(self.get(self.owner, "/api/checkin/calendar-events").status_code, 503)

    def test_unknown_calendar_and_malformed_snapshot_rejected(self):
        path = self.calendar_snapshot()
        now = int(time.time())
        data = {"title": "Test", "location": "", "purpose": "Attendance", "fields": ["email"],
                "starts_at": now, "ends_at": now + 3600, "calendar_uid": "fabricated"}
        self.assertEqual(self.send(self.owner, "/api/checkin/events", data).status_code, 422)
        for content in ('{"events":null}', '{"events":[null]}', "not json"):
            path.write_text(content, encoding="utf-8")
            self.assertEqual(self.get(self.owner, "/api/checkin/calendar-events").status_code, 503)
            self.assertEqual(self.send(self.owner, "/api/checkin/events", data).status_code, 503)

    def test_document_custom_encryption_validation_stepup_and_csv(self):
        event, path = self.event(["name", "document", "custom_1"],
                                 custom_fields=[{"id": "custom_1", "label": "餐食偏好"}])
        self.assertEqual(event["custom_fields"], [{"id": "custom_1", "label": "餐食偏好"}])
        document = {"type": "cn_id", "country": "CN", "number": "11010519491231002X"}
        values = {"name": "Synthetic", "document": document, "custom_1": "=1+2"}
        bad = values | {"document": document | {"number": "110105194912310020"}}
        self.assertEqual(self.answer(self.guest, path, values=bad).status_code, 422)
        self.assertEqual(self.answer(self.guest, path, values=values).status_code, 201)
        raw = self.path.read_bytes()
        self.assertNotIn(document["number"].encode(), raw)
        self.assertNotIn(b"=1+2", raw)
        endpoint = f"/api/checkin/events/{event['id']}/entries"
        self.assertEqual(self.get(self.owner, endpoint + "?sensitive=1").json["code"], "checkin_pat_required")
        headers = self.pat_headers(self.owner, event, allow_export=True)
        result = self.get(self.owner, endpoint, **headers)
        self.assertEqual(result.json["entries"][0]["values"]["document"]["validation"], "checksum")
        rows = list(csv.reader(io.StringIO(self.get(self.owner, endpoint + "?format=csv", **headers).data.decode("utf-8-sig"))))
        self.assertEqual(rows[0][-1], "餐食偏好")
        self.assertEqual(rows[1][-1], "'=1+2")
        self.assertIn(document["number"], rows[1][-2])
        duplicate = self.answer(self.client(), path, values=values | {"custom_1": "Changed"})
        self.assertEqual(duplicate.status_code, 409)
        with self.store.connection() as connection:
            connection.execute("UPDATE checkin_events SET retain_until=1")
        self.get(self.guest, path)
        with self.store.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM checkin_options").fetchone()[0], 1)

    def test_custom_schema_bounded_and_no_extra_answers(self):
        now = int(time.time())
        base = {"title": "A", "location": "", "purpose": "B", "fields": ["email"],
                "starts_at": now, "ends_at": now + 100}
        for custom in (None, "bad", [{"id": "evil", "label": "A"}],
                       [{"id": "custom_1", "label": "姓名"}],
                       [{"id": "custom_1", "label": ""}],
                       [{"id": f"custom_{i}", "label": str(i)} for i in range(1, 10)]):
            result = self.send(self.owner, "/api/checkin/events", base | {"custom_fields": custom})
            self.assertEqual(result.status_code, 422, result.json)
        event, path = self.event(["email", "custom_1"], custom_fields=[{"id": "custom_1", "label": "备注"}])
        for answers in ({"email": "a@example.org"}, {"email": "a@example.org", "custom_1": ""},
                        {"email": "a@example.org", "custom_1": "x" * 501}):
            self.assertEqual(self.answer(self.guest, path, values=answers).status_code, 422)
        self.factor(self.owner)
        self.assertEqual(self.send(self.owner, "/api/checkin/events/" + event["id"], {}, "DELETE").status_code, 200)
        with self.store.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM checkin_options").fetchone()[0], 0)

    def test_document_prefill_requires_factor_and_never_returns_unrequested_fields(self):
        _, path = self.event(["document", "custom_1"], custom_fields=[{"id": "custom_1", "label": "备注"}])
        document = {"type": "passport", "country": "US", "number": "A12345678", "validation": "format_only"}
        with self.app.app_context(), self.store.connection() as connection:
            connection.execute("INSERT INTO private_fields VALUES(?,?,?,?)",
                               (2, "document", encrypt(2, "document", document), int(time.time())))
        context = {"consent": True, "policy": POLICY}
        self.assertEqual(self.send(self.outsider, path + "/prefill/authorize", context).status_code, 401)
        self.factor(self.outsider)
        grant = self.send(self.outsider, path + "/prefill/authorize", context).json["token"]
        result = self.send(self.outsider, path + "/prefill", {"token": grant})
        self.assertEqual(result.json["values"], {"document": document})

    def member_event(self):
        now = int(time.time())
        result = self.send(self.other, "/api/checkin/events", {
            "title": "Member event", "location": "E12", "purpose": "Attendance",
            "fields": ["email"], "starts_at": now, "ends_at": now + 3600})
        self.assertEqual(result.status_code, 201, result.json)
        return result.json["event"]

    def test_public_intro_allowlist_and_live_count_never_expose_attendee_data_or_secrets(self):
        event, join = self.event(description="公开介绍\n第二行")
        self.assertEqual(event["visibility"], "public")
        self.assertEqual(self.answer(self.guest, join).status_code, 201)
        endpoint = event["detail_path"].replace("/checkin/events/", "/api/checkin/pages/")
        result = self.get(self.guest, endpoint)
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(result.json, {
            "can_edit": False, "event": {"title": event["title"], "description": "公开介绍\n第二行",
                                        "organizer": "Test real name", "count": 1}})
        for secret in ("attendee@example.org", "Synthetic attendee", "calendar_uid", "fields",
                       "join_url", "qr_url", "entries", "ciphertext", "purpose"):
            self.assertNotIn(secret, result.get_data(as_text=True))
        self.assertEqual(self.get(self.guest, "/api/checkin/events/" + event["id"] + "/entries").status_code, 401)

    def test_intro_visibility_matrix_and_creator_admin_can_edit(self):
        event = self.member_event()
        endpoint = event["detail_path"].replace("/checkin/events/", "/api/checkin/pages/")
        settings = f"/api/checkin/events/{event['id']}/visibility"
        for visibility, allowed in (
                ("public", (self.guest, self.outsider, self.other, self.owner)),
                ("members", (self.other, self.owner)),
                ("private", (self.other, self.owner))):
            self.assertEqual(self.send(self.other, settings, {
                "description": "Introduction", "visibility": visibility}, "PUT").status_code, 200)
            for client in (self.guest, self.outsider, self.other, self.owner):
                expected = 200 if client in allowed else 404
                self.assertEqual(self.get(client, endpoint).status_code, expected)
                with closing(self.get(client, event["detail_path"])) as response:
                    self.assertEqual(response.status_code, expected)
        self.assertTrue(self.get(self.other, endpoint).json["can_edit"])
        self.assertTrue(self.get(self.owner, endpoint).json["can_edit"])
        self.assertEqual(self.send(self.owner, settings, {
            "description": "Admin edited", "visibility": "public"}, "PUT").status_code, 200)
        self.assertEqual(self.get(self.guest, endpoint).json["event"]["description"], "Admin edited")

    def test_intro_member_visibility_checks_live_membership_and_private_excludes_other_members(self):
        event = self.member_event()
        endpoint = event["detail_path"].replace("/checkin/events/", "/api/checkin/pages/")
        settings = f"/api/checkin/events/{event['id']}/visibility"
        with self.store.connection() as connection:
            connection.execute("DELETE FROM account_roles WHERE user_id=1")
        self.assertEqual(self.send(self.other, settings, {
            "description": "", "visibility": "members"}, "PUT").status_code, 200)
        self.assertEqual(self.get(self.owner, endpoint).status_code, 200)
        with self.store.connection() as connection:
            connection.execute("UPDATE group_members SET valid_until=1 WHERE user_id=1")
        self.assertEqual(self.get(self.owner, endpoint).status_code, 404)
        with self.store.connection() as connection:
            connection.execute("UPDATE group_members SET valid_until=? WHERE user_id=1", (int(time.time()) + 1000,))
        self.assertEqual(self.send(self.other, settings, {
            "description": "", "visibility": "private"}, "PUT").status_code, 200)
        self.assertEqual(self.get(self.owner, endpoint).status_code, 404)

    def test_intro_permission_changes_require_creator_or_admin_and_csrf(self):
        event, _ = self.event()
        endpoint = f"/api/checkin/events/{event['id']}/visibility"
        data = {"description": "Unauthorized", "visibility": "private"}
        self.assertEqual(self.send(self.guest, endpoint, data, "PUT").status_code, 401)
        for client in (self.outsider, self.other):
            self.assertEqual(self.send(client, endpoint, data, "PUT").status_code, 404)
        response = self.owner.put(endpoint, json=data, headers=self.base_headers())
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.get(self.owner, "/api/checkin/events/" + event["id"]).json["event"]["visibility"], "public")

    def test_intro_validation_and_private_creation_are_atomic(self):
        for invalid in ({"visibility": "everyone"}, {"description": "x" * 2001}, {"description": None},
                        {"description": "a\x00b"}, {"visibility": []}):
            event, _ = self.event()
            settings = f"/api/checkin/events/{event['id']}/visibility"
            result = self.send(self.owner, settings, {"description": "", "visibility": "public"} | invalid, "PUT")
            self.assertEqual(result.status_code, 422, result.json)
        event, join = self.event(description="Not public", visibility="private")
        endpoint = event["detail_path"].replace("/checkin/events/", "/api/checkin/pages/")
        self.assertEqual(self.get(self.guest, endpoint).status_code, 404)
        # Introduction permissions do not change QR-based participation or record permissions.
        self.assertEqual(self.get(self.guest, join).status_code, 200)
        self.assertEqual(self.get(self.other, "/api/checkin/events/" + event["id"] + "/entries").status_code, 404)

    def test_intro_slug_contains_creator_and_title_but_is_unique_and_stable(self):
        event, _ = self.event(title="同名活动 / 测试")
        other, _ = self.event(title="同名活动 / 测试")
        self.assertTrue(event["detail_path"].startswith("/checkin/events/1/"))
        from urllib.parse import unquote
        self.assertIn("同名活动-测试", unquote(event["detail_path"]))
        self.assertNotEqual(event["detail_path"], other["detail_path"])
        self.assertEqual(self.get(self.guest, event["detail_path"].replace("/events/1/", "/events/3/")).status_code, 404)
        self.assertEqual(self.get(self.owner, "/api/checkin/events/" + event["id"]).json["event"]["detail_path"],
                         event["detail_path"])

    def test_intro_backfill_is_idempotent_and_admin_delete_removes_page(self):
        event, _ = self.event()
        with self.store.connection() as connection:
            connection.execute("DELETE FROM checkin_pages WHERE event_id=?", (event["id"],))
            initialize_checkin(connection)
            initialize_checkin(connection)
            self.assertEqual(connection.execute("SELECT count(*) FROM checkin_pages").fetchone()[0], 1)
        with closing(self.get(self.guest, event["detail_path"])) as response:
            self.assertEqual(response.status_code, 200)
        self.factor(self.owner)
        self.assertEqual(self.send(self.owner, "/api/checkin/events/" + event["id"], {}, "DELETE").status_code, 200)
        self.assertEqual(self.get(self.guest, event["detail_path"]).status_code, 404)
        with self.store.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM checkin_pages").fetchone()[0], 0)

    def test_intro_cross_site_navigation_allowed_but_api_remains_same_origin_and_requests_admin_only(self):
        event, _ = self.event()
        navigation = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
        with closing(self.get(self.guest, event["detail_path"], **navigation)) as response:
            self.assertEqual(response.status_code, 200)
        endpoint = event["detail_path"].replace("/checkin/events/", "/api/checkin/pages/")
        self.assertEqual(self.get(self.guest, endpoint, **{"Sec-Fetch-Site": "cross-site"}).status_code, 403)
        with closing(self.get(self.owner, "/checkin/requests", **navigation)) as response:
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.get(self.other, "/checkin/requests").status_code, 403)
        self.assertEqual(self.get(self.guest, "/checkin/requests").status_code, 401)

    def test_intro_creator_can_keep_managing_visibility_after_membership_expiry(self):
        event = self.member_event()
        endpoint = event["detail_path"].replace("/checkin/events/", "/api/checkin/pages/")
        with self.store.connection() as connection:
            connection.execute("UPDATE group_members SET valid_until=1 WHERE user_id=3")
        settings = f"/api/checkin/events/{event['id']}/visibility"
        self.assertEqual(self.send(self.other, settings, {
            "description": "Creator retained", "visibility": "private"}, "PUT").status_code, 200)
        self.assertTrue(self.get(self.other, endpoint).json["can_edit"])
        self.assertEqual(self.get(self.guest, endpoint).status_code, 404)
        self.assertFalse(self.get(self.other, "/api/checkin/session").json["can_create"])

    def test_member_can_only_create_and_access_own_qr_not_manage_or_read_records(self):
        event = self.member_event()
        status = self.get(self.other, "/api/checkin/session").json
        self.assertTrue(status["can_create"])
        self.assertFalse(status["can_manage"])
        endpoint = "/api/checkin/events/" + event["id"]
        self.assertEqual(self.get(self.other, event["qr_url"]).status_code, 200)
        for patch in ({"closed": True}, {"rotate": True}):
            self.assertEqual(self.send(self.other, endpoint, patch, "PATCH").status_code, 403)
        self.assertEqual(self.send(self.other, endpoint, {}, "DELETE").status_code, 403)
        self.assertEqual(self.get(self.other, endpoint + "/entries").status_code, 200)
        for suffix in ("/entries?sensitive=1", "/entries?format=csv"):
            self.assertEqual(self.get(self.other, endpoint + suffix).status_code, 403)
        self.assertEqual(self.get(self.other, "/api/checkin/deletion-requests").status_code, 403)
        self.assertEqual(self.get(self.owner, endpoint).status_code, 200)
        self.assertIn(event["id"], [item["id"] for item in self.get(self.owner, "/api/checkin/events").json["events"]])
        self.event()
        self.assertEqual(len(self.get(self.other, "/api/checkin/events").json["events"]), 1)
        self.assertEqual(len(self.get(self.owner, "/api/checkin/events").json["events"]), 2)
        self.assertEqual(self.get(self.owner, endpoint + "/entries", **self.pat_headers(self.owner, event)).status_code, 200)
        self.assertEqual(self.send(self.owner, endpoint, {"closed": True}, "PATCH").status_code, 200)

    def deletion_request(self, event):
        result = self.send(self.other, f"/api/checkin/events/{event['id']}/deletion-request", {"reason": "活动重复"})
        self.assertEqual(result.status_code, 201, result.json)
        return result.json["id"]

    def test_deletion_approval_requires_admin_factor_and_is_atomic_one_use(self):
        event = self.member_event()
        request_id = self.deletion_request(event)
        endpoint = f"/api/checkin/events/{event['id']}"
        self.assertEqual(self.get(self.other, endpoint).json["event"]["deletion_request"]["status"], "pending")
        self.assertEqual(self.send(self.other, endpoint + "/deletion-request", {"reason": "重复提交"}).status_code, 409)
        rows = self.get(self.owner, "/api/checkin/deletion-requests").json["requests"]
        self.assertEqual(rows[0]["requester_id"], 3)
        review = f"/api/checkin/deletion-requests/{request_id}/review"
        decision = {"decision": "approve", "note": "确认重复"}
        self.assertEqual(self.send(self.other, review, decision).status_code, 403)
        self.assertEqual(self.send(self.owner, review, decision).status_code, 401)
        self.assertEqual(self.get(self.owner, endpoint).status_code, 200)
        self.factor(self.owner)
        self.assertEqual(self.send(self.owner, review, decision).status_code, 200)
        self.assertEqual(self.get(self.owner, endpoint).status_code, 404)
        self.assertEqual(self.send(self.owner, review, decision).status_code, 409)
        row = self.get(self.owner, "/api/checkin/deletion-requests").json["requests"][0]
        self.assertEqual((row["status"], row["reviewer_id"], row["note"]), ("approved", 1, "确认重复"))

    def test_reject_preserves_records_allows_new_request_and_direct_admin_delete_resolves_it(self):
        event = self.member_event()
        request_id = self.deletion_request(event)
        self.factor(self.owner)
        review = f"/api/checkin/deletion-requests/{request_id}/review"
        self.assertEqual(self.send(self.owner, review, {"decision": "reject", "note": ""}).status_code, 422)
        self.assertEqual(self.send(self.owner, review, {"decision": "reject", "note": "仍需使用"}).status_code, 200)
        self.assertEqual(self.get(self.other, "/api/checkin/events/" + event["id"]).status_code, 200)
        second = self.deletion_request(event)
        self.assertNotEqual(second, request_id)
        self.assertEqual(self.send(self.owner, "/api/checkin/events/" + event["id"], {}, "DELETE").status_code, 200)
        rows = self.get(self.owner, "/api/checkin/deletion-requests").json["requests"]
        self.assertEqual([item["status"] for item in rows], ["approved", "rejected"])

    def test_admin_grant_revocation_enforced_on_existing_event_and_requests(self):
        event = self.member_event()
        request_id = self.deletion_request(event)
        self.factor(self.owner)
        with self.store.connection() as connection:
            connection.execute("DELETE FROM account_roles WHERE user_id=1")
        self.assertEqual(self.get(self.owner, "/api/checkin/deletion-requests").status_code, 403)
        self.assertEqual(self.send(self.owner, f"/api/checkin/deletion-requests/{request_id}/review",
                                   {"decision": "approve", "note": ""}).status_code, 403)
        self.assertEqual(self.send(self.owner, f"/api/checkin/events/{event['id']}/deletion-request",
                                   {"reason": "不能删除别人的活动"}).status_code, 404)

    def test_direct_admin_delete_requires_factor(self):
        event = self.member_event()
        endpoint = "/api/checkin/events/" + event["id"]
        self.assertEqual(self.send(self.owner, endpoint, {}, "DELETE").status_code, 401)
        self.factor(self.owner)
        self.assertEqual(self.send(self.owner, endpoint, {}, "DELETE").status_code, 200)

    def test_deletion_queue_paginates_without_hiding_history(self):
        with self.store.connection() as connection:
            connection.executemany(
                "INSERT INTO checkin_deletion_requests(event_id,event_title,requester_id,reason,status,created_at) "
                "VALUES(?, 'History',3,'Reason','rejected',1)", [(f"history-{index}",) for index in range(51)])
        first = self.get(self.owner, "/api/checkin/deletion-requests").json
        second = self.get(self.owner, "/api/checkin/deletion-requests?page=2").json
        self.assertEqual(first["total"], 51)
        self.assertEqual(len(first["requests"]), 50)
        self.assertEqual(len(second["requests"]), 1)
        self.assertFalse({item["id"] for item in first["requests"]} & {item["id"] for item in second["requests"]})
        self.assertEqual(self.get(self.owner, "/api/checkin/deletion-requests?page=bad").status_code, 422)


if __name__ == "__main__":
    unittest.main()
