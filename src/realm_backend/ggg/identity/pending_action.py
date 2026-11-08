from ic_python_db import Integer, String

from core.db import Json, Row


class PendingAction(Row):
    """A call in its confirm form that a delegate prepared under a grant and the grantor has yet to approve
    (core.pending). The call (``method`` and ``args``) never changes after filing."""
    __alias__ = "id"
    id = String()  # AC-<n>
    grantor = String(indexed=True)
    delegate = String(indexed=True)
    grant = String(default="")
    method = String(default="")  # the operation
    args = Json({})
    hash = String(default="")  # seals the record: the approval must send it back
    summary = Json([])  # [{"label", "value"}], the realm's own words about the call
    risk = String(default="")
    note = String(default="")  # the delegate's own text
    # pending | approved (a read, waiting to be collected) | approving | done | failed | declined | withdrawn | expired | void
    status = String(default="pending")
    filed_at = Integer(default=0)
    expires_at = Integer(default=0)
    decided_at = Integer(default=0)
    result = Json(None)  # the reply of the call once it ran, without its secrets
    open_link = String(indexed=True)  # "grantor|delegate" while the action waits for the grantor, then None
