from ic_python_db import Integer, ManyToOne, OneToMany, String

from core.db import Row


class Penalty(Row):
    __alias__ = "id"
    id = String()
    verdict = ManyToOne("Verdict", "penalties")
    target = String(default="")
    # fine | restitution | community_service | suspension | revocation | injunction | freeze | termination
    kind = String(default="fine")
    amount = Integer(default=0)
    symbol = String(default="")
    status = String(default="pending")  # pending | executed | waived
    due_at = Integer(default=0)
    executed_at = Integer(default=0)
    invoice = String(default="")
    ledger_entries = OneToMany("LedgerEntry", "penalty")
