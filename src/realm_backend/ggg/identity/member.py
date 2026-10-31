from ic_python_db import Integer, String

from core.db import Row


class Member(Row):
    __alias__ = "id"
    id = String()  # principal
    status = String(default="active", indexed=True)  # active | inactive | suspended | banned
    joined_at = Integer(default=0)
