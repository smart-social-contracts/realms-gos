from ic_python_db import Integer, ManyToOne, OneToMany, String

from db import Row


class Transfer(Row):
    __alias__ = "id"
    id = String()
    sender = String(default="")
    to = String(default="")
    symbol = String(default="")
    amount = Integer(default=0)
    ledger_tx = String(default="")
    invoice = ManyToOne("Invoice", "transfers")
    status = String(default="pending")  # pending | completed | failed
    at = Integer(default=0)
    ledger_entries = OneToMany("LedgerEntry", "transfer")
