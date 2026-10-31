"""EntityMigration — a forwarding stub left behind when a mobile entity moves.

When a mobile entity (a user, an organization) leaves this canister for
another quarter or realm, it leaves a small ``EntityMigration`` record here
pointing at where it went. Resolving a stale ``realm://`` reference then walks
the chain of these stubs hop-by-hop until it reaches the entity's current home.

Why a per-canister stub instead of a central locator (see issue #156):

* A global ``principal -> location`` index would be one row per entity
  (millions network-wide), which blows the ~few-thousand-entity ceiling of
  ``ic-python-db`` — the very limit quarters exist to respect.
* Each canister therefore stores forwarding stubs **only for entities that
  actually lived here**, bounded by its own population. The chain is
  distributed; no canister holds a global map.

Both ``prev_ref`` and ``next_ref`` are **full** ``realm://`` URIs, so a chain
can span quarters *and* realms transparently. Each stub is signed by the
subject's principal so a malicious canister cannot forge a redirect.

Only mobile entities get chains. Immutable records (Vote, Case, LedgerEntry)
never move and are referenced by plain absolute URIs with no forwarding.
A Case marked ``transferred`` stays on this canister; the dest pointer lives
in ``Case.metadata``, not an EntityMigration stub.
"""

from ic_python_db import Entity, String, TimestampedMixin
from ic_python_logging import get_logger

logger = get_logger("entity.entity_migration")


class EntityMigration(Entity, TimestampedMixin):
    """A signed forwarding pointer for one subject that left this canister.

    There is at most one active stub per ``subject`` on a given canister (a
    later move from the same canister overwrites ``next_ref``).
    """

    __alias__ = "subject"
    subject = String(max_length=128)  # principal or entity id that moved
    entity_type = String(max_length=64, default="User")
    prev_ref = String(max_length=256)  # full realm:// URI it arrived from ("" if origin)
    next_ref = String(max_length=256)  # full realm:// URI it moved to
    moved_at = String(max_length=64)  # ISO timestamp of the move
    signature = String(max_length=512, default="")  # subject-principal signature over next_ref
