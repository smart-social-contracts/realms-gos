from ic_python_db import OneToMany, String

from core.db import Row


class Human(Row):
    __alias__ = "id"
    id = String()
    user = String(default="", indexed=True)
    date_of_birth = String(default="")
    h3_index = String(default="")
    identities = OneToMany("Identity", "human")
