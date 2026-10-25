from ic_python_db import ManyToOne, String

from core.db import Json, Row


class Zone(Row):
    __alias__ = "id"
    id = String()  # h3 index
    name = String(default="")
    kind = String(default="unassigned")
    land = ManyToOne("Land", "zones")
    user = String(default="", indexed=True)
    metadata = Json({})
