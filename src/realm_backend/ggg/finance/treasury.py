from ic_python_db import OneToOne, String

from core.db import Json, Row


class Treasury(Row):
    __alias__ = "id"
    id = String(default="1")
    config = Json({"epoch": "monthly", "anchor_month": 1, "source_fund": "ROOT", "auto_allocate": False})
    realm = OneToOne("Realm", "treasury")
