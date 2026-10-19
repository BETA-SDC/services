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
from .auth import auth, configure_auth
from .repository import RegistrationStore
from .routes import api, pages
from .security import install_security
from .verification import verification
from .signup import signup
from .github import github, configure_github
from .permissions import admin
from .profile import profile
from .private_data import private, configure_private
from .passkeys import passkeys
from .second_factor import second_factor
from .sites import sites, configure_site_pages, install_site_shell
from .checkin import checkin
from .checkin_access import access as checkin_access


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
    app.register_blueprint(pages)
    app.register_blueprint(api, url_prefix="/api")
    app.register_blueprint(auth, url_prefix="/api")
    app.register_blueprint(verification, url_prefix="/api")
    app.register_blueprint(signup, url_prefix="/api")
    app.register_blueprint(github, url_prefix="/api")
    app.register_blueprint(admin, url_prefix="/api")
    app.register_blueprint(profile, url_prefix="/api")
    app.register_blueprint(private, url_prefix="/api")
    app.register_blueprint(passkeys, url_prefix="/api")
    app.register_blueprint(second_factor, url_prefix="/api")
    app.register_blueprint(sites)
    app.register_blueprint(checkin)
    app.register_blueprint(checkin_access)
    configure_site_pages(app)
    install_site_shell(app)
    return app
