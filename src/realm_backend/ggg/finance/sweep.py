from ic_python_db import Integer, OneToMany, String

from core.db import Json, Row


class Sweep(Row):
    """Money paid in a quarter that moves into the capital's funds (ggg I-FED-5 to I-FED-7). A quarter records one per
    rule and treasury epoch, and its ledger transfer is fixed when it is recorded; the capital keeps the ones its quarters
    report, as ``<quarter>/<id>``."""

    __alias__ = "id"
    id = String()
    quarter = String(default="", indexed=True)
    fund = String(default="")  # the quarter's fund the money leaves
    symbol = String(default="")
    into = String(default="")  # the capital's fund it enters
    rule = String(default="", indexed=True)  # "fund:symbol": the sweep rule it was made under
    to = Json({})  # the capital's account that receives it: {"owner", "subaccount"}
    epoch = Integer(default=0)  # the start of the treasury epoch it belongs to
    amount = Integer(default=0)
    fee = Integer(default=0)
    created_at = Integer(default=0)  # also the created_at_time of its ledger transfer
    block = String(default="")  # the ledger's block of its transfer
    # sending (its transfer is in flight, or its outcome is not known) | sent (the ledger gave a block) | booked (the capital
    # booked it) | failed (the ledger refused it: nothing moved) | needs_check (no answer within the ledger's window)
    status = String(default="sending", indexed=True)
    reason = String(default="")
    reported_at = Integer(default=0)
    ledger_entries = OneToMany("LedgerEntry", "sweep")
