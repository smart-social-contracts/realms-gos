from ic_python_db import ManyToOne, String

from core.db import Json, Row


class Authority(Row):
    __alias__ = "id"
    id = String()
    grantor = ManyToOne("Department", "authorities")
    target = String(default="", indexed=True)  # "Department:x" or "Quarter:canister-id"
    operations = Json([])
