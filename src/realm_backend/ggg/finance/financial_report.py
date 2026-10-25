from ic_python_db import Integer, ManyToOne, String

from db import Json, Row


class FinancialReport(Row):
    __alias__ = "id"
    id = String()
    kind = String(default="period_close")
    period = ManyToOne("FiscalPeriod", "reports")
    as_of = Integer(default=0)
    statements = Json({})
    source_hash = String(default="")
    issued_by = String(default="")
    supersedes = String(default="")
