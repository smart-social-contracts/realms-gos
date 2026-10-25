from ic_python_db import Integer, OneToMany, String

from db import Row


class FiscalPeriod(Row):
    __alias__ = "id"
    id = String()
    name = String(default="")
    starts_at = Integer(default=0)
    ends_at = Integer(default=0)
    status = String(default="open")  # open | closed | archived
    budgets = OneToMany("Budget", "period")
    ledger_entries = OneToMany("LedgerEntry", "period")
    reports = OneToMany("FinancialReport", "period")
