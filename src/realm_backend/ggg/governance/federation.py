from ic_python_db import String

from core.db import Json, Row


class FederationMessage(Row):
    __alias__ = "id"
    id = String()
    topic = String(default="")
    source = String(default="")
    body = Json({})
    response = Json({})
    codex = String(default="")  # the codex checksum the sender ran when it sent this (api.quarters.ask)
    compliance = String(default='"unknown"')  # the sender's flag as the receiver knew it, as JSON text (core.compliance)
