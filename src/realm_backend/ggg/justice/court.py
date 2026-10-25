from ic_python_db import ManyToOne, OneToMany, String

from db import Row


class Court(Row):
    __alias__ = "id"
    id = String()
    name = String(default="")
    operator = String(default="")  # "Department:justice" or "Organization:arbitra"
    level = String(default="first_instance")  # first_instance | appellate | supreme | specialized
    jurisdiction = String(default="")
    parent = ManyToOne("Court", "children")
    children = OneToMany("Court", "parent")
    status = String(default="active")
    license = String(default="")
    judges = OneToMany("Judge", "court")
    cases = OneToMany("Case", "court")
    appeals = OneToMany("Appeal", "court")
