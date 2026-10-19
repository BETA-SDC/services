#!/usr/bin/env python3
"""Render fixed-policy Nginx configuration for this Mac."""
import argparse


def configuration(port, root=False):
    if port not in (443, 8443) or root != (port == 443):
        raise ValueError("Use unprivileged 8443 or administrator-installed 443")
    base = "/Library/BetasdcNginx" if root else (
        "/Users/jietexue/Library/Application Support/BetasdcDemo/nginx"
    )
    tls = base + "/tls" if root else (
        "/Users/jietexue/Library/Application Support/BetasdcDemo/register-tls"
    )
    host = "register.betasdc.duckdns.org" + (":8443" if port == 8443 else "")
    user = "user _www _www;\n" if root else ""
    return f"""{user}worker_processes 1;
daemon off;
pid "{base}/nginx.pid";
lock_file "{base}/nginx.lock";
error_log stderr warn;
events {{ worker_connections 256; }}
http {{
    server_tokens off;
    access_log off;
    client_max_body_size 4k;
    client_body_timeout 10s;
    client_header_timeout 10s;
    keepalive_timeout 15s;
    send_timeout 10s;
    client_body_temp_path "{base}/temp/client";
    proxy_temp_path "{base}/temp/proxy";
    fastcgi_temp_path "{base}/temp/fastcgi";
    uwsgi_temp_path "{base}/temp/uwsgi";
    scgi_temp_path "{base}/temp/scgi";
    limit_req_zone $binary_remote_addr zone=registration:1m rate=10r/m;
    limit_conn_zone $binary_remote_addr zone=connections:1m;
    server {{
        listen 10.17.131.26:{port} ssl default_server;
        server_name register.betasdc.duckdns.org;
        ssl_certificate "{tls}/fullchain.pem";
        ssl_certificate_key "{tls}/key.pem";
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_session_tickets off;
        ssl_session_cache shared:TLS:1m;
        if ($http_host != "{host}") {{ return 444; }}
        allow 10.17.128.0/18;
        deny all;
        limit_conn connections 10;
        limit_conn_status 429;
        location / {{
            proxy_pass http://127.0.0.1:18080;
            proxy_http_version 1.1;
            proxy_set_header Host "{host}";
            proxy_set_header X-Forwarded-For $remote_addr;
            proxy_set_header X-Forwarded-Proto https;
            proxy_set_header Connection "";
            proxy_connect_timeout 2s;
            proxy_read_timeout 15s;
            proxy_send_timeout 10s;
            proxy_request_buffering on;
            proxy_buffering off;
        }}
        location = /api/register {{
            limit_req zone=registration burst=5 nodelay;
            limit_req_status 429;
            proxy_pass http://127.0.0.1:18080;
            proxy_http_version 1.1;
            proxy_set_header Host "{host}";
            proxy_set_header X-Forwarded-For $remote_addr;
            proxy_set_header X-Forwarded-Proto https;
            proxy_set_header Connection "";
            proxy_connect_timeout 2s;
            proxy_read_timeout 15s;
            proxy_send_timeout 10s;
            proxy_request_buffering on;
            proxy_buffering off;
        }}
    }}
}}
"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, choices=(443, 8443), default=8443)
    args = parser.parse_args()
    print(configuration(args.port, root=args.port == 443), end="")
