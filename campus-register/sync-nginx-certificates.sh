#!/bin/sh
# Installed root-owned; never run the project copy as a recurring root job.
set -eu
umask 077
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH
BASE=/Library/BetasdcNginx
OPENSSL_CONF=/dev/null
OPENSSL_MODULES="$BASE/lib"
export OPENSSL_CONF OPENSSL_MODULES
/usr/bin/python3 -I - <<'PY'
import os
from pathlib import Path
import pwd
import stat
import ssl
import subprocess
import tempfile

base = Path("/Library/BetasdcNginx")
source = Path("/Users/jietexue/Library/Application Support/BetasdcDemo/register-tls")
uid = pwd.getpwnam("jietexue").pw_uid
values = {}
for name in ("fullchain.pem", "key.pem"):
    fd = os.open(str(source / name), os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077:
            raise PermissionError("Unsafe certificate source")
        values[name] = stream.read(65537)
        if len(values[name]) > 65536:
            raise ValueError("Certificate file too large")
if all((base / "tls" / name).read_bytes() == value for name, value in values.items()):
    raise SystemExit(0)
with tempfile.TemporaryDirectory(dir=base) as folder:
    stage = Path(folder)
    for name, value in values.items():
        (stage / name).write_bytes(value)
        (stage / name).chmod(0o600)
    certificate_key = subprocess.check_output([
        "/usr/bin/openssl", "x509", "-in", str(stage / "fullchain.pem"),
        "-pubkey", "-noout",
    ])
    private_key = subprocess.check_output([
        "/usr/bin/openssl", "pkey", "-in", str(stage / "key.pem"), "-pubout",
    ])
    if certificate_key != private_key:
        raise ValueError("Certificate and key do not match; keeping current files")
    certificate = ssl._ssl._test_decode_cert(str(stage / "fullchain.pem"))
    ssl.match_hostname(certificate, "register.betasdc.duckdns.org")
    subprocess.run([
        "/usr/bin/openssl", "x509", "-in", str(stage / "fullchain.pem"),
        "-checkend", "86400", "-noout",
    ], check=True)
    subprocess.run([
        "/usr/bin/openssl", "verify", "-CAfile", "/etc/ssl/cert.pem",
        "-untrusted", str(stage / "fullchain.pem"), str(stage / "fullchain.pem"),
    ], check=True)
    previous = {name: (base / "tls" / name).read_bytes() for name in values}
    try:
        for name in values:
            os.replace(stage / name, base / "tls" / name)
        subprocess.run([
            str(base / "bin/nginx"), "-e", "stderr", "-t", "-p", str(base) + "/",
            "-c", str(base / "nginx.conf"),
        ], check=True)
        subprocess.run([
            str(base / "bin/nginx"), "-e", "stderr", "-s", "reload", "-p", str(base) + "/",
            "-c", str(base / "nginx.conf"),
        ], check=True)
    except Exception:
        for name, value in previous.items():
            (base / "tls" / name).write_bytes(value)
            (base / "tls" / name).chmod(0o600)
        raise
PY
