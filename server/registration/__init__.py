"""Create isolated Flask applications from configuration and feature modules."""
from pathlib import Path
import sys

# Account-only pinned dependencies, without changing the shared calendar runtime.
dependencies = Path(__file__).resolve().parents[1] / "_vendor"
if dependencies.is_dir():
    sys.path.insert(0, str(dependencies))

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import DEFAULTS
from .auth import configure_auth
from .repository import RegistrationStore
from .security import install_security
from .github import configure_github
from .privacy.crypto import configure_private
from .site_accounts.shell import configure_site_pages, install_site_shell
from .features import register_features


def create_app(database=None, networks=None, max_records=1000, *, config=None):
    app = Flask(__name__, static_folder=None)
    app.config.from_mapping(DEFAULTS)
    app.config["REGISTRATION_MAX_RECORDS"] = max_records
    if database is not None:
        app.config["REGISTRATION_DATABASE"] = database
    if networks is not None:
        app.config["REGISTRATION_NETWORKS"] = networks
    if config is not None:
        app.config.from_mapping(config)
    store = RegistrationStore(
        app.config["REGISTRATION_DATABASE"],
        app.config["REGISTRATION_MAX_RECORDS"],
    )
    store.initialize()
    app.extensions["registration_store"] = store
    configure_auth(app, store.path)
    configure_private(app, store)
    configure_github(app)
    # Only the protected Nginx socket may supply these two proxy headers.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    install_security(app)
    register_features(app)
    configure_site_pages(app)
    install_site_shell(app)
    return app
