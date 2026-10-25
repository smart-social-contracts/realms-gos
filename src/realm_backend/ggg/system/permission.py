from ic_python_db import ManyToMany, String

from db import Row


class Permission(Row):
    __alias__ = "id"
    id = String()  # the permission key, e.g. "transfer.create"
    description = String(default="")
    category = String(default="")
    scope = String(default="")
    users = ManyToMany(["User"], "permissions")
    profiles = ManyToMany(["UserProfile"], "permissions")
    departments = ManyToMany(["Department"], "permissions")
