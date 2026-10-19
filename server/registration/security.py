"""Campus access policy and response headers shared by all feature routes."""
import ipaddress
import secrets
import sqlite3

from flask import jsonify, request, session

HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Strict-Transport-Security": "max-age=86400",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' https://avatars.githubusercontent.com; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'self'"
    ),
}


def install_security(app):
    allowed = tuple(ipaddress.ip_network(n) for n in app.config["REGISTRATION_NETWORKS"])
    domain = app.config["REGISTRATION_DOMAIN"]

    @app.before_request
    def enforce_network():
        try:
            source = ipaddress.ip_address(request.remote_addr)
        except (TypeError, ValueError):
            return jsonify(error="Invalid proxy address"), 403
        if not any(source in subnet for subnet in allowed):
            return jsonify(error="当前网络不允许访问。"), 403
        if request.host.lower() != domain or request.scheme != "https":
            return jsonify(error="访问地址无效。"), 403
        from .site_accounts.domain import ACCOUNT_HOST, SITE_HOSTS
        if domain in SITE_HOSTS:
            allowed_api = (request.path in ("/api/session", "/api/logout", "/api/site/session")
                           or request.path.startswith("/api/calendar/")
                           or domain == "profile.betasdc.duckdns.org" and request.path.startswith("/api/public-profile/"))
            if request.path.startswith("/api/") and not allowed_api:
                return jsonify(error="请在账户中心进行此操作。"), 403
        account_navigation = (
            request.method == "GET" and (request.path in ("/verify", "/api/github/callback", "/account",
                                                        "/", "/sso/start", "/sso/authorize", "/sso/callback")
                                        or request.path in ("/checkin", "/checkin/requests", "/checkin/access-requests")
                                        or request.path.startswith(("/checkin/join/", "/checkin/events/", "/checkin/records/"))
                                        or not request.path.startswith(("/api/", "/sso/"))
                                        and domain != ACCOUNT_HOST)
            and request.headers.get("Sec-Fetch-Mode") == "navigate"
            and request.headers.get("Sec-Fetch-Dest") == "document"
        )
        if request.headers.get("Sec-Fetch-Site") == "cross-site" and not account_navigation:
            return jsonify(error="请求来源无效。"), 403
        if request.headers.get("Origin") not in (None, "https://" + domain):
            return jsonify(error="请求来源无效。"), 403
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            supplied = request.headers.get("X-CSRF-Token", "")
            expected = session.get("csrf_token", "")
            if not expected or not secrets.compare_digest(supplied.encode(), expected.encode()):
                return jsonify(error="页面会话已失效，请刷新后重试。", code="csrf_failed"), 403

    @app.after_request
    def protect_response(response):
        response.headers.update(HEADERS)
        return response

    def invalid_request(error):
        return jsonify(error="请求格式或地址无效。"), error.code

    for status in (400, 404, 405, 413, 415):
        app.register_error_handler(status, invalid_request)

    @app.errorhandler(sqlite3.Error)
    def storage_error(error):
        app.logger.error("Authentication storage unavailable: %s", type(error).__name__)
        return jsonify(error="服务暂时不可用，请稍后重试。"), 503
