"""Compatibility imports; implementation lives in feature modules."""
from .webauthn.domain import (
    passkeys,
)
from .webauthn.protocol import (
    user_handle, server, credentials,
)
from .webauthn.enrollment import (
    options, complete, remove,
)
from .webauthn.login import (
    login_options, login_complete,
)
