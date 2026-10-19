"""Application defaults, independent of service-manager configuration."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = {
    "REGISTRATION_DOMAIN": "account.betasdc.duckdns.org",
    "REGISTRATION_DATABASE": Path("/var/lib/betasdc/registrations.sqlite3"),
    "REGISTRATION_NETWORKS": ("0.0.0.0/0", "::/0"),
    "REGISTRATION_MAX_RECORDS": 1000,
    "SSO_NETWORKS": ("0.0.0.0/0", "::/0"),
    "REGISTRATION_ASSETS": ROOT / (
        "campus-register" if (ROOT / "campus-register").is_dir() else "static"
    ),
    "MAX_CONTENT_LENGTH": 4096,
}
