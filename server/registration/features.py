"""Ordered HTTP feature composition shared by every app factory."""
from .assets import modules
from .auth import auth
from .routes import api, pages
from .verification import verification
from .signup import signup
from .github import github
from .access_control.admin import admin
from .profile import profile
from .privacy.routes import private
from .webauthn.routes import passkeys
from .second_factor import second_factor
from .site_accounts.routes import sites
from .checkin import checkin
from .checkin_access import access

API_FEATURES = (api, auth, verification, signup, github, admin, profile, private, passkeys, second_factor)
SITE_FEATURES = (pages, modules, sites, checkin, access)


def register_features(app):
    for blueprint in API_FEATURES:
        app.register_blueprint(blueprint, url_prefix="/api")
    for blueprint in SITE_FEATURES:
        app.register_blueprint(blueprint)
