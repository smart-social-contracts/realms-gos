import hashlib

from ic_python_db import Integer, OneToMany, String

from core.db import Json, Row


class FiscalPeriod(Row):
    __alias__ = "id"
    id = String()
    name = String(default="")
    starts_at = Integer(default=0)
    ends_at = Integer(default=0)
    status = String(default="open")  # open | closed | archived
    # Running statements of its ledger entries, and a sha256 chained over them in id order.
    statements = Json({"accounts": {}, "fund_balances": {}, "debit": 0, "credit": 0}, max_length=1 << 20)
    source_hash = String(default=hashlib.sha256(b"").hexdigest())
    budgets = OneToMany("Budget", "period")
    ledger_entries = OneToMany("LedgerEntry", "period")
    reports = OneToMany("FinancialReport", "period")
