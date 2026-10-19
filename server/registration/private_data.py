"""Compatibility imports; implementation lives in feature modules."""
from .privacy.domain import (
    POLICY,
)
from .privacy.schema import (
    initialize_private,
)
from .privacy.crypto import (
    configure_private, encrypt, decrypt,
)
from .privacy.identity import (
    actor, password_valid, session_hash, active_session,
)
from .privacy.consent import (
    consent_context, audit, issue_consent, consume,
)
from .privacy.routes import (
    private, private_status, authorize, use_private,
)
