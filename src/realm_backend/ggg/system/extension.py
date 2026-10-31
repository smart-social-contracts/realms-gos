from ic_python_db import String

from core.db import Json, Row


class Extension(Row):
    __alias__ = "id"
    id = String()  # name
    version = String(default="")
    status = String(default="installed")
    operations = Json([])
    access = Json({"profiles": ["member"], "departments": []})
    sidebar = Json({})
    manifest = Json({})
    source = String(default="")
