from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server.github_profile import public_profile


class PublicProfileTests(unittest.TestCase):
    def test_invalid_identities_and_organizations_are_rejected(self):
        for payload in ({}, {"id": True, "login": "test"}, {"id": 1, "login": "bad/url"},
                        {"id": 1, "login": "test", "organizations": [{"id": 2, "login": "javascript:bad"}]},
                        {"id": 1, "login": "test", "public_profile": []}):
            with self.assertRaises(ValueError):
                public_profile(payload)

    def test_display_name_is_bounded_and_only_expected_envelope_fields_returned(self):
        result = public_profile({"id": 1, "login": "test", "name": "\x00" + "x" * 100,
                                 "access_token": "not-allowed", "avatar_url": "https://evil.test"})
        self.assertEqual(len(result["name"]), 80)
        self.assertNotIn("access_token", result)
        self.assertNotIn("avatar_url", result)
