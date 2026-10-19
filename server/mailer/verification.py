"""Mail delivery: verification."""
import smtplib
import time
from .config import LOGGER
from .templates import make_signup_message, make_second_factor_message
from .transport import send_message


def deliver_signup(connection, password, smtp_factory, limit):
    now = int(time.time())
    with connection:
        connection.execute("DELETE FROM signup_mail_outbox WHERE challenge_hash NOT IN "
                           "(SELECT challenge_hash FROM signup_challenges WHERE expires_at>?)", (now,))
        connection.execute("DELETE FROM signup_challenges WHERE expires_at<=?", (now,))
    jobs = connection.execute(
        "SELECT o.* FROM signup_mail_outbox o JOIN signup_challenges c USING(challenge_hash) "
        "WHERE o.next_attempt<=? AND o.attempts<8 AND c.expires_at>? AND c.attempts<5 "
        "AND c.email=o.recipient ORDER BY o.created_at LIMIT ?", (now, now, limit),
    ).fetchall()
    delivered = failed = 0
    for job in jobs:
        if not connection.execute(
            "SELECT 1 FROM signup_mail_outbox o JOIN signup_challenges c USING(challenge_hash) "
            "WHERE o.challenge_hash=? AND o.code=? AND c.expires_at>? AND c.attempts<5",
            (job["challenge_hash"], job["code"], int(time.time())),
        ).fetchone():
            continue
        try:
            send_message(make_signup_message(job["recipient"], job["code"]), password, smtp_factory)
        except (OSError, smtplib.SMTPException) as error:
            LOGGER.warning("Delivery postponed: %s", type(error).__name__)
            with connection:
                connection.execute(
                    "UPDATE signup_mail_outbox SET attempts=attempts+1,next_attempt=? "
                    "WHERE challenge_hash=? AND code=?",
                    (int(time.time()) + min(60 * 2 ** job["attempts"], 900),
                     job["challenge_hash"], job["code"]),
                )
            failed += 1
        else:
            with connection:
                connection.execute("DELETE FROM signup_mail_outbox WHERE challenge_hash=? AND code=?",
                                   (job["challenge_hash"], job["code"]))
            delivered += 1
    return delivered, failed



def deliver_second_factor(connection, password, smtp_factory, limit=10):
    # An older deployment may run the worker briefly before the schema upgrade.
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='second_factor_outbox'").fetchone():
        return 0, 0
    now = int(time.time())
    with connection:
        connection.execute(
            "DELETE FROM second_factor_outbox WHERE challenge_hash NOT IN "
            "(SELECT c.challenge_hash FROM second_factor_challenges c JOIN auth_sessions s "
            "ON s.token_hash=c.session_hash WHERE c.expires_at>? AND s.expires_at>? AND c.attempts<5)",
            (now, now))
        connection.execute("DELETE FROM second_factor_challenges WHERE expires_at<=?", (now,))
    jobs = connection.execute(
        "SELECT o.* FROM second_factor_outbox o JOIN second_factor_challenges c USING(challenge_hash) "
        "JOIN registrations r ON r.id=c.user_id JOIN auth_sessions s ON s.token_hash=c.session_hash "
        "WHERE o.next_attempt<=? AND o.attempts<5 AND c.expires_at>? AND c.attempts<5 "
        "AND r.email=o.recipient AND c.email=o.recipient AND r.email_verified_at IS NOT NULL "
        "AND s.user_id=c.user_id AND s.expires_at>? ORDER BY o.created_at LIMIT ?",
        (now, now, now, limit),
    ).fetchall()
    delivered = failed = 0
    for job in jobs:
        if not connection.execute(
            "SELECT 1 FROM second_factor_outbox o JOIN second_factor_challenges c USING(challenge_hash) "
            "JOIN auth_sessions s ON s.token_hash=c.session_hash "
            "WHERE o.challenge_hash=? AND o.code=? AND c.expires_at>? AND c.attempts<5 AND s.expires_at>?",
            (job["challenge_hash"], job["code"], int(time.time()), int(time.time())),
        ).fetchone():
            continue
        try:
            send_message(make_second_factor_message(job["recipient"], job["code"]), password, smtp_factory)
        except (OSError, smtplib.SMTPException) as error:
            LOGGER.warning("Second-factor delivery postponed: %s", type(error).__name__)
            with connection:
                connection.execute(
                    "UPDATE second_factor_outbox SET attempts=attempts+1,next_attempt=? "
                    "WHERE challenge_hash=? AND code=?",
                    (int(time.time()) + min(30 * 2 ** job["attempts"], 120), job["challenge_hash"], job["code"]))
            failed += 1
        else:
            with connection:
                connection.execute("DELETE FROM second_factor_outbox WHERE challenge_hash=? AND code=?",
                                   (job["challenge_hash"], job["code"]))
            delivered += 1
    return delivered, failed

