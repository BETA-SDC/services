#!/usr/bin/python3
"""Copy validated apex/wildcard certificates from the isolated ACME account."""
import os
from pathlib import Path
import pwd
import ssl
import stat
import subprocess
import tempfile

BASE = Path("/var/lib/betasdc-acme/deployed")
TARGET = Path("/etc/betasdc")


def synchronize():
    uid = pwd.getpwnam("betasdc-acme").pw_uid
    changes = []
    with tempfile.TemporaryDirectory(prefix=".cert-", dir=TARGET) as folder:
        stage = Path(folder)
        for profile, hostname, destination in (
            ("apex", "betasdc.duckdns.org", "tls"),
            ("wildcard", "register.betasdc.duckdns.org", "wildcard"),
        ):
            values = {}
            for name in ("fullchain.pem", "key.pem"):
                fd = os.open(BASE / profile / name, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid
                            or stat.S_IMODE(info.st_mode) != 0o600):
                        raise PermissionError("Unsafe ACME certificate permissions")
                    values[name] = stream.read(65537)
                    if len(values[name]) > 65536:
                        raise ValueError("Oversized certificate file")
            certificate = stage / (profile + ".pem")
            key = stage / (profile + ".key")
            certificate.write_bytes(values["fullchain.pem"])
            key.write_bytes(values["key.pem"])
            certificate.chmod(0o600)
            key.chmod(0o600)
            decoded = ssl._ssl._test_decode_cert(str(certificate))
            names = {value for kind, value in decoded.get("subjectAltName", ()) if kind == "DNS"}
            expected_name = "betasdc.duckdns.org" if profile == "apex" else "*.betasdc.duckdns.org"
            if expected_name not in names:
                raise ValueError("Unexpected certificate DNS names")
            subprocess.run(["/usr/bin/openssl", "x509", "-in", str(certificate),
                            "-checkhost", hostname, "-noout"], check=True,
                           stdout=subprocess.DEVNULL)
            subprocess.run(["/usr/bin/openssl", "x509", "-in", str(certificate),
                            "-checkend", "86400", "-noout"], check=True,
                           stdout=subprocess.DEVNULL)
            subprocess.run(["/usr/bin/openssl", "verify", "-CAfile", "/etc/ssl/certs/ca-certificates.crt",
                            "-untrusted", str(certificate), str(certificate)],
                           check=True, stdout=subprocess.DEVNULL)
            cert_key = subprocess.check_output([
                "/usr/bin/openssl", "x509", "-in", str(certificate), "-pubkey", "-noout",
            ])
            private_key = subprocess.check_output([
                "/usr/bin/openssl", "pkey", "-in", str(key), "-pubout",
            ])
            if cert_key != private_key:
                raise ValueError("Certificate/key mismatch")
            for name, value in values.items():
                target = TARGET / destination / name
                if not target.exists() or target.read_bytes() != value:
                    changes.append((target, value))
        previous = {path: path.read_bytes() if path.exists() else None for path, _ in changes}
        try:
            for path, value in changes:
                with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
                    temporary.write(value)
                    temporary_path = Path(temporary.name)
                temporary_path.chmod(0o600)
                os.replace(temporary_path, path)
            subprocess.run(["/usr/sbin/nginx", "-e", "stderr", "-t",
                            "-c", "/etc/betasdc/nginx.conf"], check=True)
            active = subprocess.run(["/usr/bin/systemctl", "is-active", "--quiet",
                                     "betasdc-nginx.service"]).returncode == 0
            if changes and active:
                subprocess.run(["/usr/bin/systemctl", "reload", "betasdc-nginx.service"], check=True)
        except Exception:
            for path, value in previous.items():
                if value is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(value)
                    path.chmod(0o600)
            raise
    print("Certificate validation/synchronization succeeded")


if __name__ == "__main__":
    synchronize()
