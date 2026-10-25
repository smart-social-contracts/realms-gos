from ic_python_db import ManyToMany, OneToMany, String

from core.db import Json, Row


class UserProfile(Row):
    __alias__ = "id"
    id = String()  # name
    description = String(default="")
    allowed_to = Json([])
    users = ManyToMany(["User"], "profiles")
    permissions = ManyToMany(["Permission"], "profiles")
    positions = OneToMany("Position", "profile")
