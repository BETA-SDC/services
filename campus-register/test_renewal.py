import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class RenewalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.log = self.home / "calls.log"
        shutil.copyfile(ROOT / "test-fixtures/fake-acme.sh", self.home / "acme.sh")
        for profile, domain in (
            ("wildcard", "*.betasdc.duckdns.org"),
            ("apex", "betasdc.duckdns.org"),
        ):
            folder = self.home / ("certificates-" + profile) / (domain + "_ecc")
            folder.mkdir(parents=True)
            (folder / (domain + ".conf")).touch()

    def tearDown(self):
        self.temp.cleanup()

    def run_renewal(self, wildcard="2", apex="2"):
        env = dict(os.environ, BETASDC_ACME_HOME=str(self.home),
                   RENEW_TEST_LOG=str(self.log), WILDCARD_RESULT=wildcard,
                   APEX_RESULT=apex)
        return subprocess.run(
            ["sh", str(ROOT / "renew-certificate.sh")],
            env=env, capture_output=True, text=True,
        )

    def test_not_due_is_success_and_domains_are_sequential(self):
        self.assertEqual(self.run_renewal().returncode, 0)
        self.assertEqual(self.log.read_text().splitlines(), [
            "wildcard *.betasdc.duckdns.org",
            "apex betasdc.duckdns.org",
        ])

    def test_successful_renewal(self):
        self.assertEqual(self.run_renewal("0", "0").returncode, 0)

    def test_first_failure_does_not_skip_second_certificate(self):
        self.assertEqual(self.run_renewal("1", "0").returncode, 1)
        self.assertEqual(len(self.log.read_text().splitlines()), 2)

    def test_second_failure_is_reported(self):
        self.assertEqual(self.run_renewal("0", "1").returncode, 1)

    def test_missing_configuration_is_failure(self):
        config = self.home / "certificates-wildcard/*.betasdc.duckdns.org_ecc/*.betasdc.duckdns.org.conf"
        config.unlink()
        result = self.run_renewal()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Missing certificate configuration", result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), ["apex betasdc.duckdns.org"])


if __name__ == "__main__":
    unittest.main()
