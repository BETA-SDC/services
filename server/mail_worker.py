"""Deliver queued verification messages without giving the web process network access."""
from contextlib import closing
from email.message import EmailMessage
from email.utils import formatdate
import fcntl
import hashlib
import logging
import os
from pathlib import Path
import smtplib
import sqlite3
import ssl
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
dependencies = Path(__file__).resolve().parent / "_vendor"
if dependencies.is_dir():
    sys.path.insert(0, str(dependencies))

SENDER = "betasdc@163.com"
DOMAIN = "account.betasdc.duckdns.org"
DATABASE = "/var/lib/betasdc/registrations.sqlite3"
LOGGER = logging.getLogger("betasdc-mail")


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
    from server.registration.permissions import is_admin
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
            message = EmailMessage()
            message["From"] = f"BETA SDC Service <{SENDER}>"
            message["To"] = job["recipient"]
            message["Subject"] = content["subject"]
            message["Date"] = formatdate(localtime=False)
            message["Message-ID"] = f"<system-{job['id']}@163.com>"
            message.set_content(content["body"] + "\nBETA SDC Service\n")
            with smtp_factory("smtp.163.com", 465, timeout=15, context=ssl.create_default_context()) as smtp:
                smtp.login(SENDER, password)
                smtp.send_message(message, from_addr=SENDER, to_addrs=[job["recipient"]])
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


def make_message(recipient, token):
    message = EmailMessage()
    message["From"] = f"BETA SDC Service <{SENDER}>"
    message["To"] = recipient
    message["Subject"] = "BETA · 验证你的邮箱"
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = f"<verify-{hashlib.sha256(token.encode()).hexdigest()}@163.com>"
    link = f"https://{DOMAIN}/verify#token={token}"
    message.set_content(
        "你好！\n\n请连接校园网，打开下方链接，再点击“确认验证邮箱”：\n\n"
        f"{link}\n\n链接自申请起 30 分钟内有效，且只能使用一次。"
        "重新发送邮件后，请使用最新链接。\n\n"
        "如果你没有注册 BETA 账户，请忽略此邮件，不要转发验证链接。\n\nBETA\n"
    )
    return message


def make_signup_message(recipient, code):
    message = EmailMessage()
    message["From"] = f"BETA SDC Service <{SENDER}>"
    message["To"] = recipient
    message["Subject"] = "BETA · 注册邮箱验证码"
    message["Date"] = formatdate(localtime=False)
    message.set_content(
        f"你好！\n\n你的 BETA 注册验证码是：{code}\n\n"
        "请在发起注册的浏览器中填写，10 分钟内有效。请勿向他人透露验证码。\n"
        "完成邮箱验证并提交注册后，才会创建账户。\n\n"
        "如果你没有申请注册，请忽略此邮件。\n\nBETA\n"
    )
    return message


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
            with smtp_factory("smtp.163.com", 465, timeout=15,
                              context=ssl.create_default_context()) as smtp:
                smtp.login(SENDER, password)
                smtp.send_message(make_signup_message(job["recipient"], job["code"]),
                                  from_addr=SENDER, to_addrs=[job["recipient"]])
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


def make_second_factor_message(recipient, code):
    message = EmailMessage()
    message["From"] = f"BETA SDC Service <{SENDER}>"
    message["To"] = recipient
    message["Subject"] = "BETA · 账户二次验证"
    message["Date"] = formatdate(localtime=False)
    message.set_content(
        f"你的 BETA 账户二次验证码是：{code}\n\n"
        "请在已登录且发起本次验证的浏览器中填写，5 分钟内有效，仅能使用一次。\n"
        "本次验证用于查看或修改隐私资料、管理通行密钥。不要将验证码告诉任何人。\n"
        "如非本人操作，请勿提供验证码，并检查账户登录安全。\n\nBETA SDC Service\n"
    )
    return message


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
            with smtp_factory("smtp.163.com", 465, timeout=15,
                              context=ssl.create_default_context()) as smtp:
                smtp.login(SENDER, password)
                smtp.send_message(make_second_factor_message(job["recipient"], job["code"]),
                                  from_addr=SENDER, to_addrs=[job["recipient"]])
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
                with smtp_factory("smtp.163.com", 465, timeout=15,
                                  context=ssl.create_default_context()) as smtp:
                    smtp.login(SENDER, password)
                    smtp.send_message(make_message(job["recipient"], job["token"]),
                                      from_addr=SENDER, to_addrs=[job["recipient"]])
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


def main():
    logging.basicConfig(level=logging.INFO)
    credential = Path(os.environ["CREDENTIALS_DIRECTORY"]) / "smtp-password"
    password = credential.read_text().strip()
    if not password or "\n" in password:
        raise ValueError("Missing SMTP credential")
    with open("/run/betasdc-mail/worker.lock", "a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        deliver_once(DATABASE, password)


if __name__ == "__main__":
    main()
