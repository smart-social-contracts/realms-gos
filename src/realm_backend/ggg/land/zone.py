from ic_python_db import Boolean, Integer, ManyToOne, String

from core.db import Json, Row


class Zone(Row):
    __alias__ = "id"
    id = String()  # h3 index
    name = String(default="")
    kind = String(default="unassigned")
    land = ManyToOne("Land", "zones")
    user = String(default="", indexed=True)
    metadata = Json({})
    defined = Boolean(default=False)  # a zone of the realm (zone_define), not a member's home cell
    population = Integer(default=0)  # members whose home_zone this is, kept by api.assets.move_home
