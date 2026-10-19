"""Compatibility imports; implementation lives in feature modules."""
from .site_accounts.domain import (
    sites, ACCOUNT_HOST, NAV_HOST, PROFILE_HOST, CALENDAR_HOST, SITE_HOSTS, RESERVED, safe_next,
)
from .site_accounts.schema import (
    initialize_sites, initialize_profile, own_profile,
)
from .site_accounts.preferences import (
    _preferences, preferences,
)
from .site_accounts.session import (
    site_session,
)
from .site_accounts.sso import (
    sso_start, sso_authorize, sso_callback,
)
from .site_accounts.profiles import (
    public_profile, edit_public_profile, request_address, review_addresses,
)
from .site_accounts.shell import (
    shell_asset, configure_site_pages, install_site_shell,
)
