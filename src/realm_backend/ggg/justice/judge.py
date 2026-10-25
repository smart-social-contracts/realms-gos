from ic_python_db import Integer, ManyToMany, ManyToOne, OneToMany, String

from core.db import Row


class Judge(Row):
    __alias__ = "id"
    id = String()
    user = String(indexed=True)
    court = ManyToOne("Court", "judges")
    specialization = String(default="")
    status = String(default="active")
    appointed_at = Integer(default=0)
    cases = ManyToMany(["Case"], "judges")
    verdicts = OneToMany("Verdict", "issued_by")
