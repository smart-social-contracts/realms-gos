from ic_python_db import Integer, ManyToOne, OneToMany, String

from core.db import Row, each


class Transfer(Row):
    __alias__ = "id"
    id = String()
    sender = String(default="")
    to = String(default="")
    symbol = String(default="")
    amount = Integer(default=0)
    ledger_tx = String(default="")
    invoice = ManyToOne("Invoice", "transfers")
    # sending (ledger call in flight) | pending (call rejected) | completed | failed (ledger refused, fund refunded)
    # | unconfirmed (the ledger's answer is unclear: tokens may have moved, fund not refunded)
    status = String(default="sending")
    # the ledger's raw answer, or the call's rejection; empty when the ledger refused and the fund was refunded
    reply = String(default="")
    at = Integer(default=0)
    payroll = String(indexed=True)  # "payroll 2026-11 Infrastructure" on the transfers of a payroll run
    ledger_entries = OneToMany("LedgerEntry", "transfer")
    contract = String(default="", indexed=True)  # the contract it pays: the payments on a contract never exceed its value

    @classmethod
    def paid_on(cls, contract):
        """What has been paid on ``contract`` (an id): its transfers that neither failed nor were cancelled, so a
        scheduled one, which is owed, counts (ggg I-FIN-11)."""
        return sum(t.amount for t in each(cls, "contract", contract) if t.status not in ("failed", "cancelled"))
