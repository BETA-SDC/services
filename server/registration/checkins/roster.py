"""Basic roster redaction and PAT/2FA-protected private fields and CSV exports."""
from datetime import datetime
import csv
import io
import json

from flask import Response, jsonify, request

from ..permissions import is_admin
from ..private_data import audit, decrypt
from .domain import BASIC_FIELDS, FIELDS, POLICY, ZONE, csv_safe, format_answer
from .grants import authorized_records
from .http import checkin, page_number
from .permissions import account
from .presentation import scoped_event
from .repository import event_options, store


@checkin.get("/api/checkin/events/<event_id>/entries")
def entries(event_id):
    with store().connection() as connection:
        export = request.args.get("format") == "csv"
        protected = export or "X-Checkin-PAT" in request.headers or request.args.get("sensitive") == "1" or "pat" in request.args
        grant = None
        if protected:
            user, event, grant = authorized_records(connection, event_id, export)
        else:
            user = account(connection)
            event, _ = scoped_event(connection, event_id, user)
        all_fields = json.loads(event["fields_json"])
        fields = all_fields if protected else [f for f in all_fields if f in BASIC_FIELDS]
        field_labels = FIELDS | {item["id"]: item["label"] for item in event_options(connection, event_id)["custom_fields"]}
        page = page_number(maximum=10000)
        historical = is_admin(connection, user["id"])
        policies = (POLICY, "checkin-2026-10-16-v4") if protected else (POLICY,)
        policy_filter = "" if historical else " AND policy IN (" + ",".join("?" for _ in policies) + ")"
        params = (event_id,) if historical else (event_id, *policies)
        rows = connection.execute(
            "SELECT id,ciphertext,checked_at,user_id FROM checkin_entries WHERE event_id=? "
            + policy_filter + " ORDER BY checked_at DESC,id DESC LIMIT ? OFFSET ?",
            (*params, 10000 if export else 50, 0 if export else (page - 1) * 50)).fetchall()
        items = [{"id": row[0], "values": {f: v for f, v in decrypt(event_id, "checkin:entry:" + row[0], row[1]).items() if f in fields},
                  "checked_at": row[2], "registered": row[3] is not None} for row in rows]
        audit(connection, user["id"], "checkin.export" if export else "checkin.view", fields)
        if not export:
            total = connection.execute("SELECT count(*) FROM checkin_entries WHERE event_id=?" + policy_filter, params).fetchone()[0]
            return jsonify(entries=items, total=total, page=page, page_size=50, title=event["title"],
                           fields=fields, field_labels={f: field_labels[f] for f in fields},
                           expires_at=grant["expires_at"] if grant else None,
                           can_export=bool(grant and grant["allow_export"] and historical),
                           protected=protected, has_private=any(f not in BASIC_FIELDS for f in all_fields),
                           legacy_restricted=not historical)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["签到编号", "服务器签到时间（北京时间）", "已登录账号"] + [field_labels[f] for f in fields])
    for item in items:
        writer.writerow([item["id"], datetime.fromtimestamp(item["checked_at"], ZONE).isoformat(),
                         "是" if item["registered"] else "否"] + [
                             csv_safe(format_answer(item["values"][f])) for f in fields])
    return Response("\ufeff" + output.getvalue(), content_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="checkin-{event_id}.csv"'})
