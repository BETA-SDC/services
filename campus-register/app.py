#!/usr/bin/env python3
"""Small campus-only registration demo, not an authentication system."""
import argparse
import ctypes
import ipaddress
import json
import os
import re
import socket
import sqlite3
import ssl
import struct
import sys
import threading
import time
import zlib
from collections import defaultdict, deque
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
DOMAIN = "register.betasdc.duckdns.org"


def require_unprivileged_process():
    if (os.getuid() == 0 or os.geteuid() == 0
            or os.getuid() != os.geteuid()
            or os.getgid() != os.getegid()):
        raise PermissionError("The web process must run without root or set-ID privileges")


def launchd_listener(name):
    require_unprivileged_process()
    if sys.platform != "darwin":
        raise RuntimeError("launchd socket activation requires macOS")
    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    activate = library.launch_activate_socket
    activate.argtypes = [
        ctypes.c_char_p, ctypes.POINTER(ctypes.POINTER(ctypes.c_int)),
        ctypes.POINTER(ctypes.c_size_t),
    ]
    activate.restype = ctypes.c_int
    library.free.argtypes = [ctypes.c_void_p]
    library.free.restype = None
    descriptors = ctypes.POINTER(ctypes.c_int)()
    count = ctypes.c_size_t()
    error = activate(name.encode("utf-8"), ctypes.byref(descriptors), ctypes.byref(count))
    if error:
        raise OSError(error, os.strerror(error))
    try:
        if count.value != 1:
            for index in range(count.value):
                os.close(descriptors[index])
            raise RuntimeError("Expected one campus IPv4 listening socket")
        listener = socket.socket(fileno=descriptors[0])
        listener.set_inheritable(False)
        return listener
    finally:
        library.free(descriptors)


def brand_image():
    glyph = ("11110", "10001", "10001", "11110", "10001", "10001", "11110")
    rows = []
    for y in range(40):
        row = bytearray()
        for x in range(40):
            gx, gy = (x - 10) // 4, (y - 6) // 4
            ink = 0 <= gx < 5 and 0 <= gy < 7 and glyph[gy][gx] == "1"
            row.extend((255, 255, 255) if ink else (24, 113, 87))
        rows.append(b"\x00" + row)

    def chunk(kind, value):
        return struct.pack(">I", len(value)) + kind + value + struct.pack(
            ">I", zlib.crc32(kind + value) & 0xffffffff
        )

    return b"\x89PNG\r\n\x1a\n" + chunk(
        b"IHDR", struct.pack(">IIBBBBB", 40, 40, 8, 2, 0, 0, 0)
    ) + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b"")


class RegistrationServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32
    max_connections = 32
    max_connections_per_ip = 4

    def __init__(self, address, database, allowed_networks, certfile=None, keyfile=None,
                 listener=None, proxy_port=None):
        require_unprivileged_process()
        if proxy_port is not None and (
                address[0] != "127.0.0.1" or certfile or keyfile or listener
                or proxy_port not in (443, 8443)):
            raise ValueError("Proxy mode requires a plaintext loopback listener and a fixed HTTPS port")
        self.proxy_port = proxy_port
        self.connection_lock = threading.Lock()
        self.active_connections = defaultdict(int)
        self.connection_count = 0
        if listener is not None:
            if listener.getsockname() != address:
                raise ValueError("Activated socket does not match the configured address")
            if listener.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM:
                raise ValueError("Activated socket is not a TCP stream")
        if bool(certfile) != bool(keyfile):
            raise ValueError("Both certificate and private key are required")
        self.certfile, self.keyfile = certfile, keyfile
        self.scheme = "https" if certfile or proxy_port else "http"
        self._tls_context = None
        self._tls_signature = None
        self._tls_lock = threading.Lock()
        if certfile:
            self.tls_context()
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.allowed_networks = [ipaddress.ip_network(n) for n in allowed_networks]
        self.attempts = defaultdict(deque)
        self.rate_lock = threading.Lock()
        self.lock = threading.Lock()
        with self.connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS registrations (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
        self.database.chmod(0o600)
        super().__init__(address, Handler, bind_and_activate=listener is None)
        if listener is not None:
            self.socket.close()
            self.socket = listener
            self.server_address = listener.getsockname()
            self.server_name = socket.getfqdn(self.server_address[0])
            self.server_port = self.server_address[1]

    def process_request(self, request, client_address):
        address = client_address[0]
        with self.connection_lock:
            if (self.connection_count >= self.max_connections
                    or self.active_connections[address] >= self.max_connections_per_ip):
                self.shutdown_request(request)
                return
            self.connection_count += 1
            self.active_connections[address] += 1
        try:
            super().process_request(request, client_address)
        except Exception:
            self.release_connection(address)
            raise

    def release_connection(self, address):
        with self.connection_lock:
            self.connection_count -= 1
            self.active_connections[address] -= 1
            if not self.active_connections[address]:
                del self.active_connections[address]

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.release_connection(client_address[0])

    def tls_context(self):
        with self._tls_lock:
            try:
                signature = tuple(Path(p).stat().st_mtime_ns for p in (
                    self.certfile, self.keyfile
                ))
                if signature != self._tls_signature:
                    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                    context.minimum_version = ssl.TLSVersion.TLSv1_2
                    context.load_cert_chain(self.certfile, self.keyfile)
                    self._tls_context = context
                    self._tls_signature = signature
            except (OSError, ssl.SSLError):
                if self._tls_context is None:
                    raise
                print("TLS reload failed; retaining the previously loaded certificate", flush=True)
            return self._tls_context

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(str(self.database), timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def allowed(self, address):
        ip = ipaddress.ip_address(address)
        return any(ip in network for network in self.allowed_networks)

    def rate_allowed(self, address):
        now = time.monotonic()
        with self.rate_lock:
            for key in list(self.attempts):
                hits = self.attempts[key]
                while hits and now - hits[0] >= 60:
                    hits.popleft()
                if not hits:
                    del self.attempts[key]
            hits = self.attempts[address]
            if len(hits) >= 10:
                return False
            hits.append(now)
            return True


class Handler(BaseHTTPRequestHandler):
    server_version = "CampusDemo"
    sys_version = ""

    def setup(self):
        self.request.settimeout(10)
        if self.server.certfile:
            # Handshake in the request thread, not in the main accept loop.
            self.request = self.server.tls_context().wrap_socket(
                self.request, server_side=True
            )
        super().setup()
        self.connection.settimeout(10)

    def finish(self):
        try:
            super().finish()
        finally:
            if self.server.certfile:
                self.request.close()

    def log_message(self, fmt, *args):
        # Log only status codes; never registration bodies or email addresses.
        if len(args) > 1 and str(args[1]).isdigit():
            print("HTTP {}".format(args[1]), flush=True)

    def send(self, status, data, content_type="application/json; charset=utf-8"):
        if isinstance(data, dict):
            data = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        if self.server.scheme == "https":
            self.send_header("Strict-Transport-Security", "max-age=86400")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'",
        )
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def permitted(self):
        for name in ("Host", "Origin", "Content-Length", "Content-Type",
                     "X-Forwarded-For", "X-Forwarded-Proto"):
            if len(self.headers.get_all(name, [])) > 1:
                self.send(400, {"error": "Ambiguous request headers"})
                return False
        if self.headers.get_all("Transfer-Encoding", []):
            self.send(400, {"error": "Unsupported request framing"})
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.send(403, {"error": "Cross-site requests are not allowed"})
            return False
        self.source_address = self.client_address[0]
        if self.server.proxy_port is not None:
            forwarded = self.headers.get_all("X-Forwarded-For", [])
            try:
                if (self.client_address[0] != "127.0.0.1" or len(forwarded) != 1
                        or self.headers.get("X-Forwarded-Proto") != "https"):
                    raise ValueError()
                self.source_address = str(ipaddress.ip_address(forwarded[0]))
            except ValueError:
                self.send(403, {"error": "Invalid proxy request"})
                return False
        if not self.server.allowed(self.source_address):
            self.send(403, {"error": "当前网络不允许访问。"})
            return False
        host = self.headers.get("Host", "")
        allowed_hosts = {
            "{}:{}".format(DOMAIN, self.server.server_port),
            "{}:{}".format(self.server.server_address[0], self.server.server_port),
        }
        if self.server.proxy_port is not None:
            # Both fixed public endpoints permit an atomic 8443 -> 443 cutover.
            allowed_hosts = {DOMAIN, "{}:8443".format(DOMAIN)}
        if self.server.scheme == "https" and self.server.server_port == 443:
            allowed_hosts.add(DOMAIN)
            allowed_hosts.add(self.server.server_address[0])
        if host.lower() not in allowed_hosts:
            self.send(403, {"error": "访问地址无效。"})
            return False
        return True

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self.permitted():
            return
        path = urlsplit(self.path).path
        if path == "/api/health":
            self.send(200, {"status": "ok"})
        elif path in ("/mark.png", "/favicon.ico"):
            self.send(200, brand_image(), "image/png")
        elif path in ("/", "/styles.css", "/main.js"):
            filename, content_type = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/styles.css": ("styles.css", "text/css; charset=utf-8"),
                "/main.js": ("main.js", "application/javascript; charset=utf-8"),
            }[path]
            self.send(200, (ROOT / filename).read_bytes(), content_type)
        else:
            self.send(404, {"error": "页面不存在。"})

    def do_POST(self):
        if not self.permitted():
            return
        if self.path != "/api/register":
            self.send(404, {"error": "页面不存在。"})
            return
        origin = self.headers.get("Origin")
        if origin and origin != self.server.scheme + "://" + self.headers.get("Host", ""):
            self.send(403, {"error": "请求来源无效。"})
            return
        if not self.server.rate_allowed(self.source_address):
            self.send(429, {"error": "提交过于频繁，请稍后重试。"})
            return
        if self.headers.get("Transfer-Encoding"):
            self.send(400, {"error": "请求格式无效。"})
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self.send(415, {"error": "请求格式无效。"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                self.send(413, {"error": "请求内容过长或为空。"})
                return
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError()
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError()
            username, email = payload.get("username"), payload.get("email")
            if not isinstance(username, str) or not isinstance(email, str):
                raise ValueError()
        except (ValueError, UnicodeDecodeError, OSError, RecursionError):
            self.send(400, {"error": "请填写用户名和邮箱。"})
            return
        username, email = username.strip(), email.strip().lower()
        if not re.fullmatch(r"[A-Za-z0-9_\u4e00-\u9fff]{2,24}", username):
            self.send(422, {"error": "用户名须为 2–24 个汉字、字母、数字或下划线。"})
            return
        if len(email) > 254 or not re.fullmatch(
            r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
            r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+", email
        ) or ".." in email.split("@")[0]:
            self.send(422, {"error": "请填写有效的邮箱地址。"})
            return
        try:
            with self.server.lock, self.server.connect() as connection:
                connection.execute(
                    "INSERT INTO registrations (username, email) VALUES (?, ?)",
                    (username, email),
                )
        except sqlite3.IntegrityError:
            self.send(409, {"error": "该用户名或邮箱已登记，请更换后重试。"})
            return
        except sqlite3.Error:
            self.send(503, {"error": "暂时无法保存，请稍后重试。"})
            return
        self.send(201, {"username": username, "email": email})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="10.17.131.26")
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--database", required=True)
    parser.add_argument("--cert")
    parser.add_argument("--key")
    parser.add_argument("--proxy-port", type=int, choices=(443, 8443))
    parser.add_argument("--launchd-socket")
    parser.add_argument("--allow", action="append", default=None)
    args = parser.parse_args()
    if args.proxy_port is None and (not args.cert or not args.key):
        parser.error("TLS certificate/key are required outside loopback proxy mode")
    require_unprivileged_process()
    listener = launchd_listener(args.launchd_socket) if args.launchd_socket else None
    server = RegistrationServer(
        (args.bind, args.port), args.database,
        args.allow or ["10.17.128.0/18"],
        args.cert, args.key, listener, args.proxy_port,
    )
    print("Listening on https://{}:{}".format(args.bind, args.port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
