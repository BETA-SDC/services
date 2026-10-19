"""Stable Gunicorn entry point: server.webapp:create_app()."""
from .registration import create_app

__all__ = ["create_app"]
