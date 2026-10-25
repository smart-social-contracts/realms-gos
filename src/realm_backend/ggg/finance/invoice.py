from ic_python_db import Integer, OneToMany, String

from db import Json, Row


class Invoice(Row):
    __alias__ = "id"
    id = String()
    payer = String(indexed=True)  # "User:alice" or "Organization:clinic-b"
    amount = Integer(default=0)
    symbol = String(default="")
    due_at = Integer(default=0)
    status = String(default="pending", indexed=True)  # pending | paid | overdue | cancelled
    reason = String(default="")
    paid_at = Integer(default=0)
    payment = Json({})
    transfers = OneToMany("Transfer", "invoice")
    ledger_entries = OneToMany("LedgerEntry", "invoice")
