"""Stable approval blueprint import; implementation lives in the attendance package."""
from .checkins import access
from .checkins.schema import initialize_access

__all__ = ["access", "initialize_access"]
