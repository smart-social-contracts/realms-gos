from ic_python_db import Boolean, Database, Integer, ManyToOne, OneToMany, String

from core.db import Row, each


class Transfer(Row):
    __alias__ = "id"
    __version__ = 2  # 2: a transfer records the contract it pays
    id = String()
    sender = String(default="")
    to = String(default="")
    symbol = String(default="")
    amount = Integer(default=0)
    ledger_tx = String(default="")
    invoice = ManyToOne("Invoice", "transfers")
    # scheduled (owed by due_at, no money moved) | sending (ledger call in flight) | pending (call rejected)
    # | completed | failed (ledger refused, fund refunded) | cancelled (a scheduled one, revoked, fund refunded)
    # | unconfirmed (the ledger's answer is unclear: tokens may have moved, fund not refunded)
    status = String(default="sending", indexed=True)
    # the ledger's raw answer, or the call's rejection; empty when the ledger refused and the fund was refunded
    reply = String(default="")
    at = Integer(default=0)
    payroll = String(indexed=True)  # "payroll 2026-11 Infrastructure" on the transfers of a payroll run
    ledger_entries = OneToMany("LedgerEntry", "transfer")
    contract = String(default="", indexed=True)  # the contract it pays: the payments on a contract never exceed its value
    due_at = Integer(default=0)  # when a scheduled transfer falls due; 0 for one paid at once
    budget = String(default="")  # the budget line it is charged to, when the fund's adopted budget governs its spending
    committed = Boolean(default=False)  # whether it drew on that line's committed money, not its available credit

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        # A transfer of the release before recorded its contract only on its ledger entries.
        if not obj.get("contract"):
            from ggg import LedgerEntry

            entries = Database.get_instance().reverse_index_get(cls.get_full_type_name(), obj["_id"], "ledger_entries")
            named = [e.contract.id for e in map(LedgerEntry.load, entries) if e and e.contract]
            obj["contract"] = named[0] if named else ""
        return obj

    @classmethod
    def paid_on(cls, contract):
        """What has been paid on ``contract`` (an id): its transfers that neither failed nor were cancelled, so a
        scheduled one, which is owed, counts (ggg I-FIN-11)."""
        return sum(t.amount for t in each(cls, "contract", contract) if t.status not in ("failed", "cancelled"))
