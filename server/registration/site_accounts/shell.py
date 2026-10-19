"""Account service site_accounts: shell."""
import re
from flask import jsonify, redirect, request
from ..routes import asset
from .domain import CALENDAR_HOST, NAV_HOST, PROFILE_HOST, RESERVED, sites
from .profiles import public_profile


@sites.get("/beta-shell.js")
@sites.get("/beta-shell.css")
def shell_asset():
    return asset(request.path[1:])



def configure_site_pages(app):
    """Install before_request after the shared security checks."""
    @app.before_request
    def entry():
        if request.host not in (NAV_HOST, PROFILE_HOST):
            return None
        if request.method not in ("GET", "HEAD"):
            return None
        if request.path == "/api/public-profile/" or request.path.startswith("/api/public-profile/"):
            return public_profile(request.path.removeprefix("/api/public-profile/"))
        if request.host == NAV_HOST and request.path == "/":
            return redirect(f"https://{CALENDAR_HOST}/sso/start?next=%2Fcalendar%2F", 303)
        if request.path in ("/login", "/account"):
            return redirect("/sso/start?login=1&next=%2F", 303)
        if request.host == PROFILE_HOST and (request.path == "/" or re.fullmatch(r"/[^/.]+", request.path)):
            if request.path[1:] in RESERVED:
                return jsonify(error="页面不存在。"), 404
            return asset("public-profile.html")
        if request.path in ("/navigation.js", "/public-profile.js", "/sites.css"):
            return asset(request.path[1:])
        if request.path in ("/", "/register", "/verify", "/admin", "/privacy"):
            return jsonify(error="页面不存在。"), 404



def install_site_shell(app):
    @app.after_request
    def template(response):
        if response.status_code == 200 and response.mimetype == "text/html":
            response.direct_passthrough = False
            source = response.get_data(as_text=True)
            if "/beta-shell.js" not in source and "</head>" in source:
                icons = "" if 'src="/icons.js"' in source or "vendor/lucide.js" in source else '<script src="/icons.js" defer></script>'
                includes = ('<link rel="stylesheet" href="/beta-shell.css">' + icons
                            + '<script src="/beta-shell.js" defer></script>')
                # One shared shell is injected into account and calendar documents.
                response.set_data(source.replace("</head>", includes + "</head>"))
                response.headers.pop("ETag", None)
                response.headers.pop("Last-Modified", None)
        return response

