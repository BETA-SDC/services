"""Attendance feature package; assemble routes without importing compatibility facades."""
from .http import access, checkin
from . import approvals, attendance, deletion, events, pages, roster

__all__ = ["access", "checkin"]
