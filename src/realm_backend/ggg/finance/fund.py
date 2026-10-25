from ic_python_db import OneToMany, OneToOne, String

from core.db import Row


class Fund(Row):
    __alias__ = "id"
    id = String()  # code
    name = String(default="")
    kind = String(default="general")
    department = OneToOne("Department", "fund")
    budgets = OneToMany("Budget", "fund")
    ledger_entries = OneToMany("LedgerEntry", "fund")
