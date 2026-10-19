from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent


@unittest.skipUnless(sys.platform == "darwin", "macOS policy")
class SandboxTests(unittest.TestCase):
    def run_probe(self, code, *args):
        return subprocess.run([
            "/usr/bin/sandbox-exec", "-f", str(ROOT / "registration.sb"),
            "/usr/bin/python3", "-I", "-c", code, *args,
        ], capture_output=True, text=True, timeout=10)

    def test_sensitive_reads_and_code_writes_denied(self):
        result = self.run_probe("""
import os
import socket
import ssl
import sqlite3

paths = [
    "/Users/jietexue/Library/Application Support/DuckDNS/betasdc.token",
    "/Users/jietexue/Library/BetasdcACME/account.conf",
    "/Users/jietexue/_projects/betasdc/campus-register/app.py",
]
for path in paths:
    try:
        fd = os.open(path, os.O_RDONLY)
    except PermissionError:
        pass
    else:
        os.close(fd)
        raise AssertionError("Sensitive file was accessible")
for name in ("app.py", "registration.sb", "register-tls/key.pem"):
    try:
        fd = os.open("/Users/jietexue/Library/Application Support/BetasdcDemo/" + name,
                     os.O_WRONLY)
    except PermissionError:
        pass
    else:
        os.close(fd)
        raise AssertionError("Protected file was writable")
# The service must retain read access to its active certificate and assets.
with open("/Users/jietexue/Library/Application Support/BetasdcDemo/index.html", "rb"):
    pass
peer = socket.socket()
try:
    peer.connect(("127.0.0.1", 1))
except PermissionError:
    pass
else:
    raise AssertionError("Outbound connection was allowed")
finally:
    peer.close()
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_symlink_alias_does_not_bypass_read_restriction(self):
        with tempfile.TemporaryDirectory() as folder:
            alias = Path(folder) / "alias"
            alias.symlink_to(
                "/Users/jietexue/Library/Application Support/DuckDNS/betasdc.token"
            )
            result = self.run_probe("""
import os, sys
try:
    fd = os.open(sys.argv[1], os.O_RDONLY)
except PermissionError:
    pass
else:
    os.close(fd)
    raise AssertionError("Symlink bypassed the policy")
""", str(alias))
            self.assertEqual(result.returncode, 0, result.stderr)
