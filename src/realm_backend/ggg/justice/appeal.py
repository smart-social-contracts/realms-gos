from ic_python_db import ManyToOne, OneToMany, String

from db import Row


class Appeal(Row):
    __alias__ = "id"
    id = String()
    case = ManyToOne("Case", "appeals")
    verdict = String(default="")
    court = ManyToOne("Court", "appeals")
    appellant = String(default="")
    grounds_ref = String(default="")
    status = String(default="filed")  # filed | under_review | granted | denied | withdrawn
    decision = String(default="")
    new_verdict = String(default="")
    ledger_entries = OneToMany("LedgerEntry", "appeal")
