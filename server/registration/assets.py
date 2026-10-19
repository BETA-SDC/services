"""Explicit public module allowlists; never expose arbitrary workspace files."""
from flask import Blueprint, abort

from .routes import asset

modules = Blueprint("modules", __name__)
ACCOUNT_MODULES = frozenset({
    "auth-events", "bootstrap", "client", "context", "factor", "github", "preferences",
    "privacy", "privacy-events", "profile", "settings", "settings-events", "signup",
    "ui", "views", "webauthn",
})
SHARED_MODULES = frozenset({"http"})


@modules.get("/account/<name>.js")
def account_module(name):
    if name not in ACCOUNT_MODULES:
        abort(404)
    return asset(f"account/{name}.js")


@modules.get("/shared/<name>.js")
def shared_module(name):
    if name not in SHARED_MODULES:
        abort(404)
    return asset(f"shared/{name}.js")
