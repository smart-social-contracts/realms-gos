from ic_python_db import OneToMany, String

from core.db import Json, Row

KINDS = ("company", "party", "union", "association", "other")  # other: none of the four (ggg I-ORG-9)


class Organization(Row):
    __alias__ = "id"
    id = String()  # name
    description = String(default="")
    status = String(default="active")  # active | suspended | dissolved
    kind = String(default="other")  # what it says it is, which the realm does not check; one of KINDS
    admins = Json([])
    positions = OneToMany("Position", "organization")
    bids = OneToMany("Bid", "vendor")
    ledger_entries = OneToMany("LedgerEntry", "organization")
