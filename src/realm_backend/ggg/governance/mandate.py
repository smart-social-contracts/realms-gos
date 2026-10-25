from ic_python_db import Integer, ManyToOne, OneToMany, String

from core.db import Json, Row


class Mandate(Row):
    __alias__ = "id"
    id = String()
    title = String(default="")
    scope = Json({})
    executor = ManyToOne("Department", "mandates")
    proposal = String(default="")
    status = String(default="active")  # active | completed | revoked | expired
    expires_at = Integer(default=0)
    contracts = OneToMany("Contract", "mandate")
