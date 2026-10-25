from ic_python_db import Integer, String

from db import Row


class Quarter(Row):
    __alias__ = "id"
    id = String()  # canister id
    name = String(default="")
    index = Integer(default=0)
    status = String(default="setup")  # setup | active | suspended | splitting | merging
    population = Integer(default=0)
    codex_id = String(default="")
    codex_version = String(default="")
    last_sync = Integer(default=0)
    last_activity_at = Integer(default=0)


class Forward(Row):
    __alias__ = "id"
    id = String()
    subject = String(indexed=True)
    origin = String(default="")
    destination = String(default="")
    moved_at = Integer(default=0)
    signature = String(default="")
