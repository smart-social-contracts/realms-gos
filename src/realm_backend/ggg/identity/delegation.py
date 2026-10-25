from ic_python_db import Boolean, Integer, String

from core.db import Json, Row


class Delegation(Row):
    __alias__ = "id"
    id = String()
    grantor = String(indexed=True)
    delegate = String(indexed=True)
    scope = Json({})
    status = String(default="pending")  # pending | active | revoked | expired | declined
    label = String(default="")
    description = String(default="")
    requires_acceptance = Boolean(default=True)
    granted_by = String(default="")
    accepted_at = Integer(default=0)
    starts_at = Integer(default=0)
    expires_at = Integer(default=0)
    revoked_at = Integer(default=0)
    revoked_by = String(default="")


class DelegationRequest(Row):
    __alias__ = "id"
    id = String()
    grantor = String(indexed=True)
    delegate = String(indexed=True)
    scope = Json({})
    label = String(default="")
    description = String(default="")
    starts_at = Integer(default=0)
    expires_at = Integer(default=0)
    request_expires_at = Integer(default=0)
