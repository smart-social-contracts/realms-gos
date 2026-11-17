from ic_python_db import Integer, OneToOne, String

from core.db import Json, Row


class Treasury(Row):
    __alias__ = "id"
    id = String(default="1")
    config = Json({"epoch": "monthly", "anchor_month": 1, "source_fund": "ROOT", "auto_allocate": False})
    debt_outstanding = Integer(default=0)  # the sum of the active loans' outstanding
    realm = OneToOne("Realm", "treasury")
