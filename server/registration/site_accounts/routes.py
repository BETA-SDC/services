"""Assemble site controllers without making shared helpers load HTTP routes."""
from .domain import sites
from . import preferences, profiles, session, shell, sso
