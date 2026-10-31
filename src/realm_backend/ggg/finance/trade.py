from ic_python_db import ManyToOne, String

from core.db import Row


class Trade(Row):
    __alias__ = "id"
    id = String()
    contract = ManyToOne("Contract", "trades")
    leg_a = String(default="")
    leg_b = String(default="")
    status = String(default="pending")
