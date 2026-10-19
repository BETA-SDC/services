import hashlib
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("nginx_config", ROOT / "nginx-config.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class NginxConfigurationTests(unittest.TestCase):
    def test_fixed_network_and_proxy_policy(self):
        for port in (443, 8443):
            config = module.configuration(port, root=port == 443)
            self.assertIn("listen 10.17.131.26:{} ssl default_server;".format(port), config)
            self.assertIn("allow 10.17.128.0/18;\n        deny all;", config)
            self.assertIn("proxy_pass http://127.0.0.1:18080;", config)
            self.assertIn("proxy_set_header X-Forwarded-For $remote_addr;", config)
            self.assertNotIn("$proxy_add_x_forwarded_for", config)
            self.assertIn("ssl_protocols TLSv1.2 TLSv1.3;", config)
            self.assertIn("client_max_body_size 4k;", config)
            self.assertIn("limit_req_status 429;", config)
            self.assertIn("access_log off;", config)
            self.assertNotIn("include ", config)
            self.assertNotIn("load_module ", config)
            self.assertNotIn("listen 0.0.0.0", config)

    def test_privileged_configuration_is_pinned(self):
        installer = (ROOT / "enable-nginx-443.sh").read_text()
        digest = hashlib.sha256(module.configuration(443, root=True).encode()).hexdigest()
        self.assertIn(digest, installer)
        digest = hashlib.sha256((ROOT / "sync-nginx-certificates.sh").read_bytes()).hexdigest()
        self.assertIn(digest, installer)
        config = module.configuration(443, root=True)
        self.assertIn("user _www _www;", config)
        self.assertNotIn("/opt/homebrew", config)
        self.assertNotIn("/Users/jietexue", config)

    def test_invalid_port_or_identity_rejected(self):
        for port, root in [(80, True), (443, False), (8443, True)]:
            with self.assertRaises(ValueError):
                module.configuration(port, root=root)
