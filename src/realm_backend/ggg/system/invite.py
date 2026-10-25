from ic_python_db import Boolean, Integer, String

from db import Json, Row


class Invite(Row):
    __alias__ = "id"
    id = String()  # sha256 of the code
    profile = String(default="member")
    department = String(default="")
    position = String(default="")
    max_uses = Integer(default=1)
    uses = Integer(default=0)
    redeemed_by = Json([])
    expires_at = Integer(default=0)
    revoked = Boolean(default=False)
    created_by = String(default="")
