from ic_python_db import Integer, String

from db import Row


class KeyEnvelope(Row):
    __alias__ = "id"
    id = String()  # "<scope>|<principal>"
    scope = String(indexed=True)  # "user:alice:private", "dept:Health:doc:DOC-1", "realm:x"
    principal = String(indexed=True)
    wrapped_dek = String(default="")  # the scope's key, IBE-wrapped for this principal
    granted_by = String(default="")
    at = Integer(default=0)
