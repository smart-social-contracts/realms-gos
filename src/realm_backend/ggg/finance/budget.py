from ic_python_db import Integer, ManyToOne, String

from core.db import Row

KINDS = ("revenue", "expense")


class Budget(Row):
    __alias__ = "id"
    __version__ = 2  # 2: kind is revenue or expense; it was any text
    id = String()
    fund = ManyToOne("Fund", "budgets")
    period = ManyToOne("FiscalPeriod", "budgets")
    category = String(default="")
    slot = String(indexed=True)  # "fund:period:category": the versions of one budget line
    kind = String(default="expense")  # revenue | expense
    planned = Integer(default=0)
    actual = Integer(default=0)
    # draft | proposed | adopted | amended | closed; extended: copied from the previous period by budget_extend.
    # Amended is replaced by a newer version, adopted or extended; closed is closed with its period.
    status = String(default="draft")
    proposal = String(default="")

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        if obj.get("kind") not in KINDS:
            obj["kind"] = "expense"
        return obj
