from ic_python_db import Integer, ManyToOne, String

from core.db import Row


class Identity(Row):
    __alias__ = "id"
    id = String()
    human = ManyToOne("Human", "identities")
    kind = String(default="")
    issuer = String(default="")
    ref = String(default="")
    status = String(default="pending")  # pending | verified | rejected
    verified_at = Integer(default=0)
