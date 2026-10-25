from ic_python_db import Integer, String

from db import Json, Row


class Notification(Row):
    __alias__ = "id"
    id = String()
    recipient = String(indexed=True)  # "User:alice", "Department:health", "Organization:clinic-b"
    topic = String(default="")
    title = String(default="")
    body = String(default="")
    href = String(default="")
    sender = String(default="")
    visibility = String(default="private")
    read_by = Json([])
    at = Integer(default=0)
