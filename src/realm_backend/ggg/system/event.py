from ic_python_db import Integer, String

from db import Json, Row


class Event(Row):
    __alias__ = "id"
    id = String()
    op = String(default="")
    user = String(default="")
    by = String(default="")
    origin = String(default="")
    args = Json({})
    result = Json({})
    at = Integer(default=0)
