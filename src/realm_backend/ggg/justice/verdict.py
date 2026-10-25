from ic_python_db import Integer, ManyToOne, OneToMany, String

from db import Row


class Verdict(Row):
    __alias__ = "id"
    id = String()
    case = ManyToOne("Case", "verdicts")
    issued_by = ManyToOne("Judge", "verdicts")
    decision = String(default="")
    reasoning_ref = String(default="")
    status = String(default="draft")  # draft | issued | final | overturned
    contract = String(default="")
    issued_at = Integer(default=0)
    penalties = OneToMany("Penalty", "verdict")
