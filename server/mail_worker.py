"""Standalone mail entry point; the web service never sends SMTP directly."""
import fcntl
import logging
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
dependencies = Path(__file__).resolve().parent / "_vendor"
if dependencies.is_dir():
    sys.path.insert(0, str(dependencies))

from server.mailer.config import DATABASE, DOMAIN, LOGGER, SENDER
from server.mailer.templates import make_message, make_signup_message, make_second_factor_message
from server.mailer.verification import deliver_signup, deliver_second_factor
from server.mailer.system import deliver_system
from server.mailer.worker import deliver_once


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
