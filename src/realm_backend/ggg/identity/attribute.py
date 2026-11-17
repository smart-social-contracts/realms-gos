from ic_python_db import Integer, String

from core.db import Row


class Attribute(Row):
    """A certified attribute of a person (a disability, a family status). ``kind`` and ``value`` are in clear and
    readable only by the person, the holders of attribute.review and the codex (I-PRV-1)."""

    __alias__ = "id"
    id = String()
    human = String(default="", indexed=True)
    kind = String(default="")
    value = String(default="")
    issuer = String(default="")
    ref = String(default="")
    status = String(default="pending")  # pending | verified | rejected
    verified_at = Integer(default=0)
    expires_at = Integer(default=0)
