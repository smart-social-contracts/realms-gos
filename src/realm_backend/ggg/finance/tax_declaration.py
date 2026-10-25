from ic_python_db import Integer, String

from core.db import Row


class TaxDeclaration(Row):
    __alias__ = "id"
    id = String()
    declarant = String(indexed=True)
    period = String(default="")
    base = String(default="")
    amount = Integer(default=0)
    status = String(default="filed")  # filed | assessed | settled | disputed
    invoice = String(default="")
