from ic_python_db import Integer, String

from core.db import Json, Row


class TaxPolicy(Row):
    __alias__ = "id"
    id = String()
    status = String(default="draft", indexed=True)  # draft | proposed | adopted | superseded
    document = Json({})
    description = String(default="")
    proposal = String(default="")
    adopted_at = Integer(default=0)
