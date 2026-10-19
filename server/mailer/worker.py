"""Mail delivery: worker."""
from contextlib import closing
import hashlib
import smtplib
import sqlite3
import time
from .config import LOGGER
from .templates import make_message
from .verification import deliver_signup, deliver_second_factor
from .system import deliver_system
from .transport import send_message


def deliver_once(database, password, smtp_factory=None):
    smtp_factory = smtp_factory or smtplib.SMTP_SSL
    now = int(time.time())
    delivered = failed = 0
    with closing(sqlite3.connect(database, timeout=5)) as connection:
        connection.row_factory = sqlite3.Row
        with connection:
            connection.execute(
                "DELETE FROM mail_outbox WHERE user_id NOT IN "
                "(SELECT user_id FROM email_verifications WHERE expires_at>?)", (now,),
            )
            connection.execute("DELETE FROM email_verifications WHERE expires_at<=?", (now,))
        jobs = connection.execute(
            "SELECT o.* FROM mail_outbox o JOIN email_verifications v USING(user_id) "
            "JOIN registrations r ON r.id=o.user_id "
            "WHERE o.next_attempt<=? AND o.attempts<8 AND v.expires_at>? "
            "AND r.email_verified_at IS NULL AND r.email=o.recipient "
            "ORDER BY o.created_at LIMIT 10", (now, now),
        ).fetchall()
        for job in jobs:
            # A resend may replace an old job while this worker is running.
            current = connection.execute(
                "SELECT v.token_hash FROM email_verifications v "
                "JOIN mail_outbox o USING(user_id) WHERE o.id=? AND o.token=? "
                "AND v.expires_at>?", (job["id"], job["token"], int(time.time())),
            ).fetchone()
            if not current or current[0] != hashlib.sha256(job["token"].encode()).hexdigest():
                continue
            try:
                send_message(make_message(job["recipient"], job["token"]), password, smtp_factory)
            except (OSError, smtplib.SMTPException) as error:
                # SMTP error text may contain recipient addresses; log only its class.
                LOGGER.warning("Delivery postponed: %s", type(error).__name__)
                delay = min(60 * 2 ** job["attempts"], 900)
                with connection:
                    connection.execute(
                        "UPDATE mail_outbox SET attempts=attempts+1,next_attempt=? "
                        "WHERE id=? AND token=?",
                        (int(time.time()) + delay, job["id"], job["token"]),
                    )
                failed += 1
            else:
                with connection:
                    connection.execute(
                        "DELETE FROM mail_outbox WHERE id=? AND token=?",
                        (job["id"], job["token"]),
                    )
                delivered += 1
        sent, postponed = deliver_signup(connection, password, smtp_factory, 10 - len(jobs))
        delivered += sent
        failed += postponed
        sent, postponed = deliver_second_factor(connection, password, smtp_factory)
        delivered += sent
        failed += postponed
        sent, postponed = deliver_system(connection, database, password, smtp_factory, min(5, max(0, 10 - delivered - failed)))
        delivered += sent
        failed += postponed
    LOGGER.info("Delivery batch: delivered=%d postponed=%d", delivered, failed)
    return delivered, failed

