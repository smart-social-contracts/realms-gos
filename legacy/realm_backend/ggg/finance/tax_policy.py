"""Tax policy entity.

A realm's tax parameters (what is taxed, how, how often) as one versioned
document. The envelope is GGG; the parameters themselves are JSON whose shape
is owned by ``core.taxation`` (``schema_version``), so a new parameter does not
need a new column. A codex reads the adopted version and does the calculation
and invoicing.

Lifecycle: ``draft`` (editable, at most one) → ``proposed`` (frozen while a vote
runs) → ``adopted`` (in force, at most one) → ``superseded``.
"""

import json

from ic_python_db import Entity, Integer, String, TimestampedMixin
from ic_python_logging import get_logger

logger = get_logger("entity.tax_policy")


class TaxPolicyStatus:
    DRAFT = "draft"
    PROPOSED = "proposed"
    ADOPTED = "adopted"
    SUPERSEDED = "superseded"


class TaxPolicy(Entity, TimestampedMixin):
    __alias__ = "id"
    id = String(max_length=32)                 # "TAX-POLICY-3"
    # No default: a default is applied before the row has an id, so it would
    # never reach the field index and find_by("status", ...) would miss it.
    status = String(max_length=16, indexed=True)
    schema_version = Integer(default=1)
    # JSON: {"schema_version": 1, "taxes": [{...}, ...]}
    document = String(max_length=16384)
    description = String(max_length=512)
    proposal_id = String(max_length=64)
    created_by = String(max_length=64)
    adopted_by = String(max_length=64)
    adopted_at = String(max_length=32)

    def __repr__(self):
        return f"TaxPolicy(id={self.id!r}, status={self.status!r})"

    @classmethod
    def first_with_status(cls, status: str):
        page, _ = cls.find_by("status", status, from_id=1, count=1)
        return page[0] if page else None

    @classmethod
    def adopted(cls):
        """The policy in force, or None."""
        return cls.first_with_status(TaxPolicyStatus.ADOPTED)

    def taxes(self) -> list:
        """The parsed tax definitions (empty when the document is unreadable)."""
        try:
            doc = json.loads(self.document or "{}")
        except (ValueError, TypeError):
            logger.warning(f"{self.id}: unreadable tax document")
            return []
        taxes = doc.get("taxes") if isinstance(doc, dict) else None
        return taxes if isinstance(taxes, list) else []
