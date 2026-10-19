"""Account service site_accounts: domain."""
from urllib.parse import urlsplit
from flask import Blueprint


sites = Blueprint("sites", __name__)



ACCOUNT_HOST = "account.betasdc.duckdns.org"



NAV_HOST = "nav.betasdc.duckdns.org"



PROFILE_HOST = "profile.betasdc.duckdns.org"



CALENDAR_HOST = "calendar.betasdc.duckdns.org"



SITE_HOSTS = (NAV_HOST, PROFILE_HOST, CALENDAR_HOST)



RESERVED = {"admin", "api", "sso", "login", "logout", "register", "account",
            "settings", "assets", "privacy", "profile", "navigation"}



def safe_next(value):
    if not isinstance(value, str) or len(value) > 1500 or "\\" in value or any(ord(c) < 32 for c in value):
        return None
    parts = urlsplit(value)
    if not value.startswith("/") or value.startswith("//") or parts.netloc or parts.scheme or parts.fragment:
        return None
    if parts.path.startswith(("/sso/", "/api/")):
        return None
    return value

