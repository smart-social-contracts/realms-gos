from ic_python_db import Boolean, Integer, ManyToOne, OneToMany, OneToOne, String

from db import Json, Row


class Treasury(Row):
    __alias__ = "id"
    id = String(default="1")
    config = Json({"epoch": "monthly", "anchor_month": 1, "source_fund": "ROOT", "auto_allocate": False})
    realm = OneToOne("Realm", "treasury")


class Token(Row):
    __alias__ = "id"
    id = String()  # symbol
    ledger = String(default="")
    indexer = String(default="")
    decimals = Integer(default=8)
    fee = Integer(default=0)


class NFTToken(Row):
    __alias__ = "id"
    id = String()  # symbol
    canister = String(default="")
    name = String(default="")
    description = String(default="")
    transferable = Boolean(default=True)
    required_license = String(default="")
    supply_cap = Integer(default=0)
    total_supply = Integer(default=0)


class Fund(Row):
    __alias__ = "id"
    id = String()  # code
    name = String(default="")
    kind = String(default="general")
    department = OneToOne("Department", "fund")
    budgets = OneToMany("Budget", "fund")
    ledger_entries = OneToMany("LedgerEntry", "fund")


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


class Budget(Row):
    __alias__ = "id"
    id = String()
    fund = ManyToOne("Fund", "budgets")
    period = ManyToOne("FiscalPeriod", "budgets")
    category = String(default="")
    kind = String(default="expense")
    planned = Integer(default=0)
    actual = Integer(default=0)
    status = String(default="draft")  # draft | proposed | adopted | amended | closed
    proposal = String(default="")


class AllocationRule(Row):
    __alias__ = "id"
    id = String()
    source_fund = String(default="ROOT")
    rules = Json({})  # {"WELFARE": 30, "ROADS": 20, "GENERAL": 50}
    status = String(default="draft")  # draft | proposed | adopted | superseded
    proposal = String(default="")


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


class Trade(Row):
    __alias__ = "id"
    id = String()
    contract = ManyToOne("Contract", "trades")
    leg_a = String(default="")
    leg_b = String(default="")
    status = String(default="pending")


class PaymentAccount(Row):
    __alias__ = "id"
    id = String()
    owner = String(indexed=True)
    network = String(default="")
    address = String(default="")
    symbol = String(default="")
    label = String(default="")
    verified = Boolean(default=False)


class FinancialReport(Row):
    __alias__ = "id"
    id = String()
    kind = String(default="period_close")
    period = ManyToOne("FiscalPeriod", "reports")
    as_of = Integer(default=0)
    statements = Json({})
    source_hash = String(default="")
    issued_by = String(default="")
    supersedes = String(default="")


class TaxPolicy(Row):
    __alias__ = "id"
    id = String()
    status = String(default="draft", indexed=True)  # draft | proposed | adopted | superseded
    document = Json({})
    description = String(default="")
    proposal = String(default="")
    adopted_at = Integer(default=0)


class TaxDeclaration(Row):
    __alias__ = "id"
    id = String()
    declarant = String(indexed=True)
    period = String(default="")
    base = String(default="")
    amount = Integer(default=0)
    status = String(default="filed")  # filed | assessed | settled | disputed
    invoice = String(default="")
