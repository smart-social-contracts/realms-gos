from ic_python_db import OneToMany, OneToOne, String

from core.db import Json, Row


class Fund(Row):
    __alias__ = "id"
    id = String()  # code
    name = String(default="")
    kind = String(default="general")
    department = OneToOne("Department", "fund")
    balances = Json({})  # {symbol: Σ asset debit - credit of its ledger entries}, kept by every booking
    allocation_rule = String(default="")  # its adopted AllocationRule, splitting payments into it
    budgets = OneToMany("Budget", "fund")
    ledger_entries = OneToMany("LedgerEntry", "fund")
