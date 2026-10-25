from ic_python_db import String

from db import Json, Row


class AllocationRule(Row):
    __alias__ = "id"
    id = String()
    source_fund = String(default="ROOT")
    rules = Json({})  # {"WELFARE": 30, "ROADS": 20, "GENERAL": 50}
    status = String(default="draft")  # draft | proposed | adopted | superseded
    proposal = String(default="")
