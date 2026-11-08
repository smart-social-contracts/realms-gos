from ic_python_db import Integer, String

from core.db import Json, Row


class Notification(Row):
    __alias__ = "id"
    __version__ = 2  # 2: translations
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
    translations = Json([])  # [{"language", "title", "body"}]: each reader gets the one in their language

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        obj.setdefault("translations", [])  # a Json field the row lacks would read as the text of its default
        return obj
