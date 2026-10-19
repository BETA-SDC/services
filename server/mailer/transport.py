"""One verified-TLS SMTP transport; callers retain queue/retry ownership."""
import ssl

from .config import SENDER


def send_message(message, password, smtp_factory):
    with smtp_factory("smtp.163.com", 465, timeout=15,
                      context=ssl.create_default_context()) as smtp:
        smtp.login(SENDER, password)
        smtp.send_message(message, from_addr=SENDER, to_addrs=[str(message["To"])])
