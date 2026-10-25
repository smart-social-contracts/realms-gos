from ic_python_db import Integer, ManyToOne, String

from db import Row


class Vote(Row):
    __alias__ = "id"
    id = String()
    proposal = ManyToOne("Proposal", "votes")
    voter = String(indexed=True)
    cast_by = String(default="")
    choice = String(default="")  # yes | no | abstain
    cast_at = Integer(default=0)
