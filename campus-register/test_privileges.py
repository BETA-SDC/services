import copy
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import RegistrationServer, require_unprivileged_process

ROOT = Path(__file__).resolve().parent


class PrivilegeTests(unittest.TestCase):
    def test_ordinary_user_allowed(self):
        with patch("app.os.getuid", return_value=501), \
                patch("app.os.geteuid", return_value=501), \
                patch("app.os.getgid", return_value=20), \
                patch("app.os.getegid", return_value=20):
            require_unprivileged_process()

    def test_privileged_identities_rejected_before_database_access(self):
        for uid, euid, gid, egid in [
            (0, 0, 0, 0), (501, 0, 20, 20),
            (0, 501, 20, 20), (501, 502, 20, 20),
            (501, 501, 20, 0),
        ]:
            with self.subTest(identity=(uid, euid, gid, egid)), \
                    patch("app.os.getuid", return_value=uid), \
                    patch("app.os.geteuid", return_value=euid), \
                    patch("app.os.getgid", return_value=gid), \
                    patch("app.os.getegid", return_value=egid), \
                    patch("app.sqlite3.connect") as connect:
                with self.assertRaises(PermissionError):
                    RegistrationServer(("127.0.0.1", 0), "/unused/db", [])
                connect.assert_not_called()

    def check_config(self, config):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "service.plist"
            with path.open("wb") as target:
                plistlib.dump(config, target)
            return subprocess.run(
                ["/bin/sh", str(ROOT / "enable-standard-https.sh"), "--check", str(path)],
                capture_output=True, text=True,
            )

    def template(self):
        with (ROOT / "org.betasdc.registration-https.plist").open("rb") as source:
            return plistlib.load(source)

    def test_expected_configuration_allowed(self):
        result = self.check_config(self.template())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unsafe_configuration_changes_rejected(self):
        config = self.template()
        mutations = [
            ("UserName", "root"),
            ("GroupName", "wheel"),
            ("EnvironmentVariables", {"PYTHONPATH": "/tmp"}),
            ("StandardOutPath", "/tmp/user-controlled-log"),
            ("ProgramArguments", ["/bin/sh", "-c", "id"]),
            ("Sockets", {"CampusHTTPS": {
                **config["Sockets"]["CampusHTTPS"], "SockNodeName": "0.0.0.0",
            }}),
        ]
        for key, value in mutations:
            with self.subTest(key=key):
                changed = copy.deepcopy(config)
                changed[key] = value
                self.assertNotEqual(self.check_config(changed).returncode, 0)
        for argument in ("-I", "--key", "--allow"):
            with self.subTest(argument=argument):
                changed = copy.deepcopy(config)
                changed["ProgramArguments"].remove(argument)
                self.assertNotEqual(self.check_config(changed).returncode, 0)

    def test_malformed_configuration_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.plist"
            path.write_bytes(b"not a plist")
            result = subprocess.run(
                ["/bin/sh", str(ROOT / "enable-standard-https.sh"), "--check", str(path)],
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
