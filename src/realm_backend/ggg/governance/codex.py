from ic_python_db import Integer, String

from db import Json, Row


class Codex(Row):
    __alias__ = "id"
    id = String()
    version = String(default="")
    checksum = String(default="")
    status = String(default="active")  # active | inactive | reverted
    manifest = Json({})
    capabilities = Json([])
    source = String(default="")
    installed_at = Integer(default=0)
    previous = String(default="")
