"""Procurement lifecycle constants.

The status list and the transition map are the definition of the RFP lifecycle,
so they live host-side with the state machine that enforces them.
"""

RFP_STATUSES = (
    "draft",
    "open",
    "closed",
    "evaluation",
    "award",
    "contract_execution",
)

VALID_TRANSITIONS = {
    "draft": {"open"},
    "open": {"closed"},
    "closed": {"evaluation"},
    "evaluation": {"award"},
    "award": {"contract_execution"},
}

SEAL_SEALED = "sealed"
SEAL_REVEALED = "revealed"
ENCRYPTION_NONE = "none"
ENCRYPTION_VETKEYS = "vetkeys"
