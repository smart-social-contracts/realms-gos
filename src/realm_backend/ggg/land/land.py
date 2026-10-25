from ic_python_db import Integer, OneToMany, String

from core.db import Json, Row


class Land(Row):
    __alias__ = "id"
    id = String()
    x = Integer(default=0)
    y = Integer(default=0)
    width = Integer(default=1)
    height = Integer(default=1)
    cell = String(default="", indexed=True)  # spatial index: f"{x // CELL}:{y // CELL}", CELL in api/assets.py
    kind = String(default="unassigned")
    owner = String(default="", indexed=True)  # "User:alice" or "Organization:buildco"
    status = String(default="active")  # active | disputed | transferred | revoked
    token_id = String(default="")
    registered_by = String(default="")
    metadata = Json({})
    zones = OneToMany("Zone", "land")
