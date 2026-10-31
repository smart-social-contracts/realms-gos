from ic_python_db import Integer, ManyToOne, String

from core.db import Row


class Budget(Row):
    __alias__ = "id"
    id = String()
    fund = ManyToOne("Fund", "budgets")
    period = ManyToOne("FiscalPeriod", "budgets")
    category = String(default="")
    slot = String(indexed=True)  # "fund:period:category": the versions of one budget line
    kind = String(default="expense")
    planned = Integer(default=0)
    actual = Integer(default=0)
    status = String(default="draft")  # draft | proposed | adopted | amended | closed
    proposal = String(default="")
