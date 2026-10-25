from ic_python_db import Integer, ManyToOne, String

from db import Json, Row


class License(Row):
    __alias__ = "id"
    id = String()
    kind = String(default="")  # court | business | professional | health | education | ...
    holder = String(indexed=True)  # "Organization:clinic-b" or "User:dr-ana"
    issued_by = ManyToOne("Department", "licenses_issued")
    status = String(default="applied")  # applied | active | rejected | suspended | revoked | expired
    applied_at = Integer(default=0)
    issued_at = Integer(default=0)
    expires_at = Integer(default=0)
    evidence_ref = String(default="")
    conditions = Json({})
