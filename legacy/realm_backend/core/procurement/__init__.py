"""Host-side procurement domain logic (issue #276).

The RFP lifecycle, sealed bidding, scoring and vendor reputation used to live in
the extension's own backend package. It moved here because almost all of it is
authorization: who may publish an RFP, who may see a sealed bid before it is
revealed, who may award. Leaving those decisions in sandboxed code and handing it
generic entity CRUD would have kept the extension in charge of them.

The extension keeps the parts that are genuinely presentation — argument parsing
and response shaping — and calls ``ctx.procurement.*`` for everything else.

Storage is unchanged: the six entities are still namespaced to the extension
(``ext_procurement::Rfp`` and friends), now declared in its manifest instead of
built by ``create_extension_entity_class``, so existing rows keep working.
"""

from core.procurement.constants import (  # noqa: F401
    ENCRYPTION_NONE,
    ENCRYPTION_VETKEYS,
    RFP_STATUSES,
    SEAL_REVEALED,
    SEAL_SEALED,
    VALID_TRANSITIONS,
)
from core.procurement.verbs import READ_VERBS, VERBS  # noqa: F401
