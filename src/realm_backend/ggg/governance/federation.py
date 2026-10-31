from ic_python_db import String

from core.db import Json, Row


class FederationMessage(Row):
    __alias__ = "id"
    id = String()
    topic = String(default="")
    source = String(default="")
    body = Json({})
    response = Json({})
