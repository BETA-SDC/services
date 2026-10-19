"""Mail delivery: system."""
from pathlib import Path
import time
from .config import LOGGER
from .templates import base_message
from .transport import send_message


def deliver_system(connection, database, password, smtp_factory, limit=5):
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='system_mail_outbox'").fetchone():
        return 0, 0
    now = int(time.time())
    with connection:
        connection.execute("DELETE FROM system_mail_outbox WHERE expires_at<=?", (now,))
    jobs = connection.execute(
        "SELECT * FROM system_mail_outbox WHERE next_attempt<=? AND attempts<8 ORDER BY created_at LIMIT ?",
        (now, limit)).fetchall()
    if not jobs:
        return 0, 0
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from server.system_mail import read_message
    from server.registration.access_control.policy import is_admin
    try:
        cipher = AESGCM(Path(database).with_suffix(".private-key").read_bytes())
    except (OSError, ValueError) as error:
        LOGGER.warning("System delivery key unavailable: %s", type(error).__name__)
        return 0, len(jobs)
    delivered = failed = 0
    for job in jobs:
        recipient = connection.execute(
            "SELECT 1 FROM registrations WHERE id=? AND email=? AND email_verified_at IS NOT NULL",
            (job["user_id"], job["recipient"])).fetchone()
        permitted = bool(recipient)
        if job["kind"].endswith(".requested"):
            permitted = permitted and is_admin(connection, job["user_id"])
        if job["grant_id"] is not None:
            grant = connection.execute(
                "SELECT g.*,a.reviewer_id FROM checkin_access_grants g "
                "JOIN checkin_access_requests a ON a.id=g.request_id "
                "JOIN checkin_events e ON e.id=g.event_id WHERE g.request_id=? "
                "AND a.status='approved' AND g.revoked_at IS NULL AND g.user_id=? AND g.email=?",
                (job["grant_id"], job["user_id"], job["recipient"])).fetchone()
            permitted = permitted and bool(grant) and is_admin(connection, grant["reviewer_id"])
            if job["kind"] == "checkin.access.issued":
                permitted = permitted and grant["token_hash"] == job["grant_hash"] and grant["expires_at"] > now
        if not permitted:
            with connection:
                connection.execute("DELETE FROM system_mail_outbox WHERE id=?", (job["id"],))
            continue
        try:
            content = read_message(cipher, job)
            message = base_message(job["recipient"], content["subject"])
            message["Message-ID"] = f"<system-{job['id']}@163.com>"
            message.set_content(content["body"] + "\nBETA SDC Service\n")
            send_message(message, password, smtp_factory)
        except Exception as error:
            # Neither SMTP exceptions nor corrupt payloads may expose recipients or PATs.
            LOGGER.warning("System delivery postponed: %s", type(error).__name__)
            with connection:
                connection.execute("UPDATE system_mail_outbox SET attempts=attempts+1,next_attempt=? WHERE id=?",
                                   (int(time.time()) + min(30 * 2 ** job["attempts"], 900), job["id"]))
            failed += 1
        else:
            with connection:
                connection.execute("DELETE FROM system_mail_outbox WHERE id=?", (job["id"],))
                if job["kind"] == "checkin.access.issued":
                    connection.execute("UPDATE checkin_access_grants SET mailed_at=? WHERE request_id=? AND token_hash=?",
                                       (int(time.time()), job["grant_id"], job["grant_hash"]))
            delivered += 1
    return delivered, failed

