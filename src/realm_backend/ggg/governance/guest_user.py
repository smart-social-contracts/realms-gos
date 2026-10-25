from ic_python_db import String

from db import Json, Row


class GuestUser(Row):
    __alias__ = "id"
    id = String()  # principal
    home_quarter = String(default="")
    permissions = Json([])
