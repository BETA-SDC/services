import socket
import time

import test_app


class HardeningTests(test_app.RegistrationTests):
    def test_connection_slots_are_bounded_and_released(self):
        peers = []
        self.server.max_connections_per_ip = 2
        try:
            for _ in range(2):
                peers.append(socket.create_connection(self.server.server_address, timeout=2))
            deadline = time.monotonic() + 2
            while self.server.connection_count < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(self.server.connection_count, 2)
            rejected = socket.create_connection(self.server.server_address, timeout=2)
            try:
                rejected.settimeout(2)
                self.assertEqual(rejected.recv(1), b"")
            finally:
                rejected.close()
        finally:
            for peer in peers:
                peer.close()
        deadline = time.monotonic() + 2
        while self.server.connection_count and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.server.connection_count, 0)
        self.assertEqual(self.request("GET", "/api/health")[0], 200)

    def test_global_connection_limit(self):
        self.server.max_connections = 0
        peer = socket.create_connection(self.server.server_address, timeout=2)
        try:
            peer.settimeout(2)
            self.assertEqual(peer.recv(1), b"")
            self.assertEqual(self.server.connection_count, 0)
        finally:
            peer.close()

    def test_duplicate_framing_headers_rejected(self):
        for extra in (
            "Host: attacker.example\r\n",
            "Content-Length: 3\r\nContent-Length: 2\r\n",
            "Transfer-Encoding: chunked\r\n",
            "Origin: https://one.example\r\nOrigin: https://two.example\r\n",
        ):
            with self.subTest(headers=extra):
                peer = socket.create_connection(self.server.server_address, timeout=2)
                try:
                    host = "{}:{}".format(*self.server.server_address)
                    request = (
                        "POST /api/register HTTP/1.1\r\nHost: " + host + "\r\n"
                        + extra + "Content-Type: application/json\r\n"
                        "Connection: close\r\n\r\n{}"
                    )
                    peer.sendall(request.encode())
                    self.assertIn(b" 400 ", peer.recv(4096).split(b"\r\n")[0])
                finally:
                    peer.close()

    def test_cross_site_browser_submission_rejected(self):
        self.assertEqual(self.request(
            "POST", "/api/register", {"username": "csrf_demo", "email": "csrf@example.com"},
            headers={"Content-Type": "application/json", "Sec-Fetch-Site": "cross-site"},
        )[0], 403)

    def test_deep_json_does_not_crash_request_worker(self):
        self.assertEqual(self.request(
            "POST", "/api/register", raw="[" * 1500 + "]" * 1500,
            headers={"Content-Type": "application/json"},
        )[0], 400)
