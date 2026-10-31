from ic_python_db import Integer, ManyToOne, String

from core.db import Row


class LedgerEntry(Row):
    __alias__ = "id"
    id = String()
    transaction = String(indexed=True)
    kind = String(default="")  # asset | liability | equity | revenue | expense
    category = String(default="")
    debit = Integer(default=0)
    credit = Integer(default=0)
    symbol = String(default="")
    fund = ManyToOne("Fund", "ledger_entries")
    period = ManyToOne("FiscalPeriod", "ledger_entries")
    transfer = ManyToOne("Transfer", "ledger_entries")
    invoice = ManyToOne("Invoice", "ledger_entries")
    user = String(default="")
    organization = ManyToOne("Organization", "ledger_entries")
    contract = ManyToOne("Contract", "ledger_entries")
    case = ManyToOne("Case", "ledger_entries")
    penalty = ManyToOne("Penalty", "ledger_entries")
    appeal = ManyToOne("Appeal", "ledger_entries")
    at = Integer(default=0)
    description = String(default="")
