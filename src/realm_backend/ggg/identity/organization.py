from ic_python_db import OneToMany, String

from core.db import Json, Row


class Organization(Row):
    __alias__ = "id"
    id = String()  # name
    description = String(default="")
    status = String(default="active")  # active | suspended | dissolved
    admins = Json([])
    positions = OneToMany("Position", "organization")
    bids = OneToMany("Bid", "vendor")
    ledger_entries = OneToMany("LedgerEntry", "organization")
