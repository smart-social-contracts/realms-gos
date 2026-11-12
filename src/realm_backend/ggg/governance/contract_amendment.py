from ic_python_db import Integer, String

from core.db import Json, Row


class ContractAmendment(Row):
    __alias__ = "id"
    id = String()  # "<contract>.<n>"
    contract = String(indexed=True)
    before = Json({})  # the terms it changed: value, split, starts_at, ends_at
    after = Json({})
    proposal = String(default="")  # the one that carried it; empty when the governing department decided directly
    at = Integer(default=0)
