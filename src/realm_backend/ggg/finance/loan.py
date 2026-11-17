from ic_python_db import Integer, String

from core.db import Json, Row


class Loan(Row):
    __alias__ = "id"
    id = String()
    lender = String(default="")  # an External (outside bodies)
    amount = Integer(default=0)
    symbol = String(default="")  # the accounting currency
    term = Json({})  # a Period: {"count", "unit"}
    rate = Integer(default=0)
    fund = String(default="")  # the fund the borrowed money is for
    outstanding = Integer(default=0)
    status = String(default="active", indexed=True)  # active | repaid
    facts = Json({})
