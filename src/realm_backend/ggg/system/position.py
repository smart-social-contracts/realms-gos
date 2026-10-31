from ic_python_db import Boolean, Integer, ManyToOne, OneToMany, String

from core.db import Row


class Position(Row):
    __alias__ = "id"
    id = String()  # key, e.g. "civil-registry.clerk"
    department = ManyToOne("Department", "positions")
    organization = ManyToOne("Organization", "positions")
    title = String(default="")
    profile = ManyToOne("UserProfile", "positions")
    headcount = Integer(default=1)
    salary = Integer(default=0)
    salary_period = String(default="monthly")
    status = String(default="open")  # open | frozen | closed
    inherit_from_capital = Boolean(default=True)
    appointments = OneToMany("Appointment", "position")


class Appointment(Row):
    __alias__ = "id"
    id = String()
    position = ManyToOne("Position", "appointments")
    user = String(indexed=True)
    kind = String(default="substantive")  # substantive | acting
    agent = Boolean(default=False)
    supervisor = String(default="")
    started_at = Integer(default=0)
    ended_at = Integer(default=0)
    status = String(default="active")  # active | ended
    source_quarter = String(default="")
