from ic_python_db import Integer, String

from core.db import Json, Row


class Quarter(Row):
    __alias__ = "id"
    id = String()  # canister id
    name = String(default="")
    index = Integer(default=0)
    status = String(default="setup", indexed=True)  # setup | active | suspended | splitting | merging
    population = Integer(default=0)
    codex_id = String(default="")
    codex_version = String(default="")
    compliance = String(default='"unknown"')  # the flag as JSON text (core.compliance)
    last_sync = Integer(default=0)
    last_activity_at = Integer(default=0)
    zones = Json({})  # zone id -> members living there, as the quarter last reported (api.quarters.record)
    told_population = Integer(default=0)  # the population its row of the directory last carried to the quarters (announce)


class Forward(Row):
    __alias__ = "id"
    id = String()
    subject = String(indexed=True)
    origin = String(default="")
    destination = String(default="")
    moved_at = Integer(default=0)
    signature = String(default="")
    codex = String(default="")  # the checksum of the codex this quarter ran when the member moved
    compliance = String(default='"unknown"')  # the destination's flag as this quarter knew it, as JSON text


class SyncChange(Row):
    """A slot of the log of what changed on a capital, which its quarters read at their next sync (core.sync_log). The
    change numbered ``n`` is kept in slot ``n % KEPT`` and the number after it replaces it, so that the log never grows and
    nothing is deleted; the row ``head`` holds the number and time of the last change and the ``epoch`` of the log."""

    __alias__ = "id"
    id = String()  # ``head``, or the name of a slot (core.sync_log)
    n = Integer(default=0)  # the number of the change this slot holds
    epoch = Integer(default=0)  # ``head`` only: the start of the capital the log was last read in (core.sync_log.restart)
    part = String(default="")  # quarter | zone | settings | sweep
    key = String(default="")  # the quarter or zone it is about
    at = Integer(default=0)  # when: with its number, what tells a quarter that this log is the one it read
