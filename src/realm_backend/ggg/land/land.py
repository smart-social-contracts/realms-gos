from ic_python_db import Integer, OneToMany, String

from core.db import Json, Row


class Land(Row):
    __alias__ = "id"
    id = String()
    x = Integer(default=0)
    y = Integer(default=0)
    width = Integer(default=1)
    height = Integer(default=1)
    cell = String(default="", indexed=True)  # spatial index: f"{x // CELL}:{y // CELL}", CELL in api/assets.py
    kind = String(default="unassigned")
    owner = String(default="", indexed=True)  # "User:alice" or "Organization:buildco"
    status = String(default="active")  # active | disputed | transferred | revoked
    token_id = String(default="")
    assessed_value = Integer()  # what the assessor set, in the accounting currency; None before the first assessment
    area = Integer()  # in square metres, set with the value
    assessed_at = Integer(default=0)
    assessment_ref = String(default="")  # the entry in the assessor's register
    registered_by = String(default="")
    metadata = Json({})
    zones = OneToMany("Zone", "land")

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        # ``assessed_value`` and ``area`` have no default: a parcel registered before they existed was never assessed,
        # which is what a missing value means. ic-python-db rejects an upgrade that adds a field without a default to
        # an entity with no migrate().
        return obj
