from ic_python_db import Integer, String

from core.db import Json, Row


class Event(Row):
    __alias__ = "id"
    id = String()
    op = String(default="")
    user = String(default="")
    by = String(default="")
    origin = String(default="")
    grant = String(indexed=True)  # the power of attorney a delegate acted under, if one did
    prepared_by = String(default="")  # the delegate that prepared the act, when the member approved it (origin confirmed)
    args = Json({})
    result = Json({})
    at = Integer(default=0)

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        # ``grant`` has no default: an event written before it existed has none, which is what a missing value means.
        # ic-python-db rejects an upgrade that adds a field without a default to an entity with no migrate().
        return obj
