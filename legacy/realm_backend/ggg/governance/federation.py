"""Federation message layer entities (issue #263).

``FederationMessage`` is the inbox record for the generic inter-quarter
transport: one row per received ``msg_id``, giving idempotency (duplicate
deliveries replay the stored response instead of re-dispatching) and an
audit trail of what the federation asked this canister to do.

``QuarterResident`` is the capital's home-quarter directory: which quarter
canister a principal lives on. User entities are quarter-local by design
(issue #156), so the capital — the federation coordinator — keeps only this
coarse pointer, recorded when a member joins a quarter. Needed to route
per-user federation actions (e.g. a verdict enforced on the obligor's home
quarter) without replicating user records.
"""

from ic_python_db import Entity, String, TimestampedMixin
from ic_python_logging import get_logger

logger = get_logger("entity.federation")


class FederationMessage(Entity, TimestampedMixin):
    __alias__ = "msg_id"
    msg_id = String(max_length=128)
    topic = String(max_length=128)
    source = String(max_length=64)  # sender canister principal
    # Stored copies are for audit/replay only and may be truncated.
    body = String(max_length=4096)
    response = String(max_length=4096)


class QuarterResident(Entity, TimestampedMixin):
    __alias__ = "principal"
    principal = String(max_length=64)
    quarter_canister_id = String(max_length=64)
