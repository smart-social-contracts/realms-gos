"""Principal delegation (Power of Attorney) — act-on-behalf grants between Users."""

from ic_python_db import Entity, Integer, String, TimestampedMixin
from ic_python_logging import get_logger

logger = get_logger("entity.delegation")

STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_REVOKED = "revoked"
STATUS_EXPIRED = "expired"


class Delegation(Entity, TimestampedMixin):
    """Scoped grant for one principal to act on behalf of another.

    ``scope_json`` is a JSON object, e.g.
    ``{"operations": ["proposal.vote", "proposal.create"]}`` or ``{"all": true}``.

    The delegate need not be a realm member: an AI connection's principal can hold
    a grant. ``starts_at`` / ``expires_at`` are seconds since the epoch (0 = none).
    """

    __alias__ = "id"
    __version__ = 2

    id = String(max_length=64)
    grantor = String(max_length=64, indexed=True)
    delegate = String(max_length=64, indexed=True)
    scope_json = String(max_length=2048, default="{}")
    status = String(max_length=16, default=STATUS_PENDING)
    label = String(max_length=256, default="")
    description = String(max_length=2048, default="")
    starts_at = Integer(default=0)
    requires_acceptance = Integer(default=1)
    granted_by = String(max_length=64, default="")
    accepted_at = Integer(default=0)
    expires_at = Integer(default=0)
    revoked_at = Integer(default=0)
    revoked_by = String(max_length=64, default="")


class DelegationRequest(Entity, TimestampedMixin):
    """A delegate asking a member for power of attorney, signed by the delegate.

    Only open requests are stored: a request is deleted once the grantor
    approves or declines it, or once it is found past ``request_expires_at``.
    ``starts_at`` / ``expires_at`` are the grant window asked for.
    """

    __alias__ = "id"

    id = String(max_length=64)
    grantor = String(max_length=64, indexed=True)
    delegate = String(max_length=64, indexed=True)
    scope_json = String(max_length=2048, default="{}")
    label = String(max_length=256, default="")
    description = String(max_length=2048, default="")
    starts_at = Integer(default=0)
    expires_at = Integer(default=0)
    request_expires_at = Integer(default=0)
