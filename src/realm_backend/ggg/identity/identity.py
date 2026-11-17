from ic_python_db import Integer, ManyToOne, String

from core.db import Row


class Identity(Row):
    __alias__ = "id"
    id = String()
    human = ManyToOne("Human", "identities")
    kind = String(default="")
    category = String(default="")  # a subdivision of the kind, such as a long-term residence permit
    issuer = String(default="")
    ref = String(default="")
    status = String(default="pending")  # pending | verified | rejected
    verified_at = Integer(default=0)
    expires_at = Integer(default=0)
