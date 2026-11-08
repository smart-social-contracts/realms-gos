from ic_python_db import String

from core.db import Json, Row


class Procedure(Row):
    """How applications for one kind of licence are decided; its id is that kind."""

    __alias__ = "id"
    id = String()
    initiated_by = String(default="")
    matter = String(default="")
    decision_deadline = Json(None)  # a Period: {"count", "unit"} with GGG's unit: Day, WorkingDay, Month or Year
    silence = String(default="")  # positive grants, negative refuses; "" when legislation says nothing
    legislation = String(default="")
    facts = Json({})
