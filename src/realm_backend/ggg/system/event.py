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

    @classmethod
    def forget(cls, event_id):
        """Delete event ``event_id``, if the log holds it: what Entity.delete does, for a row with no relations.

        The audit log does this at every update once it is full. Entity.delete would look through the relations of every
        entity class for a counter to clean up (about 17 M instructions with this realm's 93 classes) and load the row
        first (2 M more), when an event has no relations, no sorted index and no hook: what is left is its field indexes,
        its row, its alias and the count, which this does from the stored row. tests/ggg/test_audit_log.py holds it to
        what Entity.delete leaves in storage, and fails when an event gains a relation, a sorted index or a hook."""
        db, kind = cls.db(), cls.get_full_type_name()
        stored = db.load(kind, event_id)
        if stored is None:
            return
        for field in cls._indexed_properties():
            if stored.get(field) is not None:
                db.field_index_remove(kind, field, str(stored[field]), event_id)
        db.delete(kind, event_id)
        db.delete(cls._alias_key(), stored[cls.__alias__])
        db.unregister_entity(kind, event_id)
        count = f"{kind}_count"
        db.save("_system", count, str(max(int(db.load("_system", count) or 0) - 1, 0)))
