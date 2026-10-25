from ic_python_db import Integer, String

from db import Row


class Token(Row):
    __alias__ = "id"
    id = String()  # symbol
    ledger = String(default="")
    indexer = String(default="")
    decimals = Integer(default=8)
    fee = Integer(default=0)
