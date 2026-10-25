from ic_python_db import String

from core.db import Row


class Registry(Row):
    __alias__ = "id"
    id = String()
    description = String(default="")
    principal_id = String(default="")
