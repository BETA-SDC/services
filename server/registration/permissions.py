"""Compatibility imports; implementation lives in feature modules."""
from .access_control.domain import (
    ORG, MEMBERSHIP_LIFETIME, owner_expiry,
)
from .access_control.schema import (
    initialize_permissions,
)
from .access_control.policy import (
    is_admin, access_status, require_admin, audit, begin_admin,
)
from .access_control.sync import (
    ensure_github_group, sync_memberships,
)
from .access_control.admin import (
    admin, overview, groups_change, membership_change, role_change, group_update, memberships_batch,
)
