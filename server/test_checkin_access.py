"""Synthetic approval, delivery and PAT checks; never sends real email."""
import json
import re
import smtplib
import sqlite3
import time
import unittest

from server import test_checkin as fixture
from server.mail_worker import deliver_once
from server.registration import create_app
from server.registration.checkin import POLICY
from server.system_mail import read_message


class AccessTests(unittest.TestCase):
    setUp = fixture.CheckinTests.setUp
    tearDown = fixture.CheckinTests.tearDown
    client = fixture.CheckinTests.client
    base_headers = fixture.CheckinTests.base_headers
    get = fixture.CheckinTests.get
    send = fixture.CheckinTests.send
    event = fixture.CheckinTests.event
    answer = fixture.CheckinTests.answer
    factor = fixture.CheckinTests.factor
    pat_headers = fixture.CheckinTests.pat_headers

    def request_access(self, event, client=None):
        result = self.send(client or self.other, f"/api/checkin/events/{event['id']}/access-requests",
                           {"reason": "Synthetic attendance reconciliation"})
        self.assertEqual(result.status_code, 201, result.json)
        return result.json["id"]

    def decision(self, request_id, **changes):
        return self.send(self.owner, f"/api/checkin/access-requests/{request_id}/review", {
            "decision": "approve", "note": "", "expires_at": int(time.time()) + 3600,
            "allow_export": False} | changes)

    def issued_job(self, request_id):
        with self.store.connection() as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute("SELECT * FROM system_mail_outbox WHERE grant_id=? AND kind='checkin.access.issued'",
                                      (request_id,)).fetchone()

    def token(self, request_id):
        content = read_message(self.app.extensions["private_cipher"], self.issued_job(request_id))
        return re.search(r"beta_checkin_[A-Za-z0-9_-]{43}", content["body"])[0]

    def smtp(self, messages, failure=False):
        class SMTP:
            def __init__(inner, host, port, timeout, context):
                self.assertEqual((host, port), ("smtp.163.com", 465))
                self.assertTrue(context.check_hostname)
            def __enter__(inner):
                return inner
            def __exit__(inner, *args):
                return False
            def login(inner, sender, password):
                self.assertEqual((sender, password), ("betasdc@163.com", "synthetic-smtp"))
                if failure:
                    raise smtplib.SMTPAuthenticationError(535, b"private-smtp-text")
            def send_message(inner, message, **kwargs):
                messages.append(message)
        return SMTP

    def test_approval_generates_encrypted_account_scoped_pat_sent_only_by_system_mail(self):
        event, join = self.event()
        self.answer(self.guest, join)
        request_id = self.request_access(event)
        self.factor(self.owner)
        approved = self.decision(request_id)
        self.assertEqual(approved.status_code, 200, approved.json)
        token = self.token(request_id)
        self.assertNotIn(token, approved.get_data(as_text=True))
        self.assertNotIn(token.encode(), self.path.read_bytes())
        self.assertNotIn(token, self.get(self.owner, "/api/checkin/access-requests").get_data(as_text=True))
        messages = []
        self.assertEqual(deliver_once(self.path, "synthetic-smtp", self.smtp(messages)), (3, 0))
        pat_mail = next(message for message in messages if token in message.get_content())
        self.assertEqual(str(pat_mail["From"]), "BETA SDC Service <betasdc@163.com>")
        self.assertEqual(str(pat_mail["To"]), "other-member@westlake.edu.cn")
        self.assertIn(f"/checkin/records/{event['id']}", pat_mail.get_content())
        self.assertNotIn("?pat=", pat_mail.get_content())
        self.assertNotIn("attendee@example.org", "".join(m.get_content() for m in messages))
        self.assertIsNone(self.issued_job(request_id))
        with self.store.connection() as connection:
            grant = connection.execute("SELECT token_hash,mailed_at FROM checkin_access_grants").fetchone()
        self.assertEqual(grant[0], self.store.token_hash(token))
        self.assertTrue(grant[1])

    def test_all_readers_including_admin_need_pat_account_binding_and_fresh_second_factor(self):
        event, _ = self.event()
        headers = self.pat_headers(self.other, event)
        endpoint = f"/api/checkin/events/{event['id']}/entries"
        self.assertEqual(self.get(self.owner, endpoint).status_code, 200)
        self.assertEqual(self.get(self.owner, endpoint + "?sensitive=1").status_code, 403)
        self.assertEqual(self.get(self.owner, endpoint, **headers).status_code, 403)
        self.assertEqual(self.get(self.guest, endpoint, **headers).status_code, 401)
        required = self.get(self.other, endpoint, **headers)
        self.assertEqual((required.status_code, required.json["code"]), (401, "second_factor_required"))
        self.factor(self.other)
        self.assertEqual(self.get(self.other, endpoint, **headers).status_code, 200)
        with self.store.connection() as connection:
            connection.execute("DELETE FROM second_factor_proofs WHERE user_id=3")
        self.assertEqual(self.get(self.other, endpoint, **headers).status_code, 401)

    def test_pat_cannot_be_used_for_another_table_in_query_string_or_to_manage_or_export(self):
        event, _ = self.event()
        another, _ = self.event()
        headers = self.pat_headers(self.other, event)
        self.factor(self.other)
        self.assertEqual(self.get(self.other, f"/api/checkin/events/{another['id']}/entries", **headers).status_code, 403)
        endpoint = f"/api/checkin/events/{event['id']}/entries"
        self.assertEqual(self.get(self.other, endpoint + "?pat=" + headers["X-Checkin-PAT"]).status_code, 403)
        self.assertEqual(self.get(self.other, endpoint + "?format=csv", **headers).status_code, 403)
        self.assertEqual(self.get(self.other, event["qr_url"], **headers).status_code, 404)
        self.assertEqual(self.send(self.other, "/api/checkin/events/" + event["id"], {"closed": True}, "PATCH").status_code, 404)

    def test_expiry_revocation_email_change_and_issuer_role_loss_are_enforced_live(self):
        event, _ = self.event()
        headers = self.pat_headers(self.other, event)
        self.factor(self.other)
        endpoint = f"/api/checkin/events/{event['id']}/entries"
        with self.store.connection() as connection:
            connection.execute("UPDATE checkin_access_grants SET expires_at=1")
        self.assertEqual(self.get(self.other, endpoint, **headers).status_code, 403)
        with self.store.connection() as connection:
            connection.execute("UPDATE checkin_access_grants SET expires_at=?,revoked_at=1", (int(time.time()) + 3600,))
        self.assertEqual(self.get(self.other, endpoint, **headers).status_code, 403)
        with self.store.connection() as connection:
            connection.execute("UPDATE checkin_access_grants SET revoked_at=NULL")
            connection.execute("UPDATE registrations SET email='changed@westlake.edu.cn' WHERE id=3")
        self.assertEqual(self.get(self.other, endpoint, **headers).status_code, 403)
        with self.store.connection() as connection:
            connection.execute("UPDATE registrations SET email='other-member@westlake.edu.cn' WHERE id=3")
            connection.execute("DELETE FROM account_roles WHERE user_id=1")
        self.assertEqual(self.get(self.other, endpoint, **headers).status_code, 403)

    def test_request_and_review_enforce_roles_csrf_duplicate_and_valid_expiration(self):
        event, _ = self.event()
        endpoint = f"/api/checkin/events/{event['id']}/access-requests"
        for client, expected in ((self.guest, 401), (self.outsider, 403)):
            self.assertEqual(self.send(client, endpoint, {"reason": "Test"}).status_code, expected)
        self.assertEqual(self.other.post(endpoint, json={"reason": "Test"}, headers=self.base_headers()).status_code, 403)
        request_id = self.request_access(event)
        self.assertEqual(self.send(self.other, endpoint, {"reason": "Duplicate"}).status_code, 409)
        self.assertEqual(self.decision(request_id).status_code, 401)
        self.factor(self.owner)
        for expiry in (None, True, 1, int(time.time()) + 8 * 86400):
            self.assertEqual(self.decision(request_id, expires_at=expiry).status_code, 422)
        self.assertEqual(self.decision(request_id, allow_export=True).status_code, 422)
        self.assertEqual(self.send(self.other, f"/api/checkin/access-requests/{request_id}/review", {}).status_code, 422)
        self.assertEqual(self.decision(request_id).status_code, 200)
        self.assertEqual(self.decision(request_id).status_code, 409)

    def test_rejection_emails_result_and_allows_new_request(self):
        event, _ = self.event()
        request_id = self.request_access(event)
        self.factor(self.owner)
        self.assertEqual(self.decision(request_id, decision="reject", note="").status_code, 422)
        self.assertEqual(self.decision(request_id, decision="reject", note="Insufficient purpose").status_code, 200)
        self.assertIsNone(self.issued_job(request_id))
        messages = []
        deliver_once(self.path, "synthetic-smtp", self.smtp(messages))
        self.assertTrue(any("Insufficient purpose" in message.get_content() for message in messages))
        self.assertNotEqual(self.request_access(event), request_id)

    def test_non_admin_approved_reader_cannot_read_legacy_consent_rows(self):
        event, join = self.event()
        self.answer(self.guest, join)
        with self.store.connection() as connection:
            connection.execute("UPDATE checkin_entries SET policy='checkin-2026-10-16-v3'")
        self.answer(self.client(), join, values={"name": "Current consent", "email": "current@example.org"})
        headers = self.pat_headers(self.other, event)
        self.factor(self.other)
        endpoint = f"/api/checkin/events/{event['id']}/entries"
        result = self.get(self.other, endpoint, **headers)
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(result.json["total"], 1)
        self.assertEqual(result.json["entries"][0]["values"]["email"], "current@example.org")
        admin = self.get(self.owner, endpoint, **self.pat_headers(self.owner, event))
        self.assertEqual(admin.json["total"], 2)
        public = self.get(self.guest, event["detail_path"].replace("/checkin/events/", "/api/checkin/pages/"))
        self.assertEqual(public.json["event"]["count"], 2)
        self.assertEqual(POLICY, "checkin-2026-10-19-v5")

    def test_basic_roster_is_filtered_server_side_without_pat_or_factor(self):
        event, join = self.event(["name", "email", "student_id", "phone", "document", "custom_1"],
                                 custom_fields=[{"id": "custom_1", "label": "Other"}])
        values = {"name": "Test", "email": "test@example.org", "student_id": "S12345",
                  "phone": "13800138000", "document": {"type": "cn_id", "country": "CN", "number": "11010519491231002X"},
                  "custom_1": "Sensitive free text"}
        self.assertEqual(self.answer(self.guest, join, values=values).status_code, 201)
        endpoint = f"/api/checkin/events/{event['id']}/entries"
        result = self.get(self.other, endpoint)
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(result.json["fields"], ["name", "email", "student_id"])
        self.assertEqual(result.json["entries"][0]["values"], {f: values[f] for f in ("name", "email", "student_id")})
        for secret in ("13800138000", "11010519491231002X", "Sensitive free text"):
            self.assertNotIn(secret, result.get_data(as_text=True))
        self.assertFalse(result.json["can_export"])
        self.assertIsNone(result.json["expires_at"])
        self.assertEqual(self.get(self.other, endpoint + "?sensitive=1").status_code, 403)
        self.assertEqual(self.get(self.owner, endpoint + "?format=csv").status_code, 403)
        self.assertEqual(self.get(self.guest, endpoint).status_code, 401)
        self.assertEqual(self.get(self.outsider, endpoint).status_code, 403)
        with self.store.connection() as connection:
            connection.execute("UPDATE checkin_entries SET policy='checkin-2026-10-16-v4'")
        self.assertEqual(self.get(self.other, endpoint).json["total"], 0)
        headers = self.pat_headers(self.other, event)
        self.factor(self.other)
        full = self.get(self.other, endpoint, **headers)
        self.assertEqual(full.json["entries"][0]["values"]["phone"], values["phone"])

    def test_resend_rotates_secret_preserves_deadline_and_revoke_cancels_delivery(self):
        event, _ = self.event()
        old = self.pat_headers(self.other, event)
        self.factor(self.other)
        with self.store.connection() as connection:
            request_id, expiry = connection.execute("SELECT request_id,expires_at FROM checkin_access_grants").fetchone()
        endpoint = f"/api/checkin/access-requests/{request_id}"
        self.assertEqual(self.send(self.owner, endpoint + "/resend", {"note": ""}).status_code, 200)
        new = {"X-Checkin-PAT": self.token(request_id)}
        self.assertNotEqual(new, old)
        records = f"/api/checkin/events/{event['id']}/entries"
        self.assertEqual(self.get(self.other, records, **old).status_code, 403)
        self.assertEqual(self.get(self.other, records, **new).status_code, 200)
        with self.store.connection() as connection:
            self.assertEqual(connection.execute("SELECT expires_at FROM checkin_access_grants").fetchone()[0], expiry)
        self.assertEqual(self.send(self.owner, endpoint + "/revoke", {"note": "Purpose completed"}).status_code, 200)
        self.assertEqual(self.get(self.other, records, **new).status_code, 403)
        self.assertIsNone(self.issued_job(request_id))
        self.assertEqual(self.send(self.owner, endpoint + "/resend", {"note": ""}).status_code, 409)

    def test_worker_retries_without_leaking_pat_and_skips_stale_recipient_or_admin(self):
        event, _ = self.event()
        request_id = self.request_access(event)
        self.factor(self.owner)
        self.decision(request_id)
        token = self.token(request_id)
        with self.assertLogs("betasdc-mail", level="WARNING") as logs:
            delivered, failed = deliver_once(self.path, "synthetic-smtp", self.smtp([], failure=True))
        self.assertEqual((delivered, failed), (0, 3))
        self.assertNotIn(token, "".join(logs.output))
        self.assertNotIn("private-smtp-text", "".join(logs.output))
        self.assertEqual(self.issued_job(request_id)["attempts"], 1)
        with self.store.connection() as connection:
            connection.execute("UPDATE system_mail_outbox SET next_attempt=0 WHERE kind!='checkin.access.expired'")
            connection.execute("UPDATE registrations SET email='changed@westlake.edu.cn' WHERE id=3")
            connection.execute("DELETE FROM account_roles WHERE user_id=1")
        self.assertEqual(deliver_once(self.path, "synthetic-smtp", self.smtp([])), (0, 0))
        self.assertIsNone(self.issued_job(request_id))

    def test_deleted_applicant_cannot_receive_resend_but_grant_can_be_revoked(self):
        event, _ = self.event()
        self.pat_headers(self.other, event)
        with self.store.connection() as connection:
            request_id = connection.execute("SELECT request_id FROM checkin_access_grants").fetchone()[0]
            connection.execute("DELETE FROM registrations WHERE id=3")
        endpoint = f"/api/checkin/access-requests/{request_id}"
        self.assertEqual(self.send(self.owner, endpoint + "/resend", {"note": ""}).status_code, 409)
        self.assertEqual(self.send(self.owner, endpoint + "/revoke", {"note": "Account removed"}).status_code, 200)
        self.assertIsNone(self.issued_job(request_id))

    def test_expiry_notification_uses_system_email_and_contains_no_pat(self):
        event, _ = self.event()
        self.pat_headers(self.other, event)
        messages = []
        deliver_once(self.path, "synthetic-smtp", self.smtp(messages))
        messages.clear()
        with self.store.connection() as connection:
            connection.execute("UPDATE checkin_access_grants SET expires_at=1")
            connection.execute("UPDATE system_mail_outbox SET next_attempt=0 WHERE kind='checkin.access.expired'")
        self.assertEqual(deliver_once(self.path, "synthetic-smtp", self.smtp(messages)), (1, 0))
        self.assertIn("已到期", messages[0].get_content())
        self.assertNotIn("beta_checkin_", messages[0].get_content())

    def test_delete_revokes_pat_and_retains_access_audit_without_answers(self):
        event, _ = self.event()
        headers = self.pat_headers(self.other, event)
        self.assertEqual(self.send(self.owner, f"/api/checkin/events/{event['id']}", {}, "DELETE").status_code, 200)
        with self.store.connection() as connection:
            self.assertTrue(connection.execute("SELECT revoked_at FROM checkin_access_grants").fetchone()[0])
            self.assertEqual(connection.execute("SELECT count(*) FROM checkin_access_requests").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM system_mail_outbox WHERE grant_id IS NOT NULL").fetchone()[0], 0)
        self.assertEqual(self.get(self.other, f"/api/checkin/events/{event['id']}/entries", **headers).status_code, 403)

    def test_pending_queue_pagination_and_pat_material_never_returned(self):
        with self.store.connection() as connection:
            connection.executemany(
                "INSERT INTO checkin_access_requests(event_id,event_title,applicant_id,reason,status,created_at) "
                "VALUES(?,'Fixture',3,'Purpose','rejected',1)", [(f"history-{i}",) for i in range(51)])
        first = self.get(self.owner, "/api/checkin/access-requests").json
        second = self.get(self.owner, "/api/checkin/access-requests?page=2").json
        self.assertEqual((first["total"], len(first["requests"]), len(second["requests"])), (51, 50, 1))
        self.assertNotIn("token_hash", json.dumps(first))
        self.assertEqual(self.get(self.other, "/api/checkin/access-requests").status_code, 403)
        self.assertEqual(self.get(self.owner, "/api/checkin/access-requests?page=invalid").status_code, 422)

    def test_pending_encrypted_system_mail_prevents_silent_key_replacement(self):
        from server.system_mail import queue_message
        with self.store.connection() as connection:
            queue_message(connection, self.app.extensions["private_cipher"], {"id": 1, "email": "creator@westlake.edu.cn"},
                          "test", "Subject", "Encrypted message")
        self.path.with_suffix(".private-key").unlink()
        with self.assertRaisesRegex(RuntimeError, "key is missing"):
            create_app(self.path)


if __name__ == "__main__":
    unittest.main()
