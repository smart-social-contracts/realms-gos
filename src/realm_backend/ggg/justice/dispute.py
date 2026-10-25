from ic_python_db import String

from db import Row


class Dispute(Row):
    __alias__ = "id"
    id = String()
    claimant = String(indexed=True)
    respondent = String(default="")
    subject = String(default="")
    title = String(default="")
    description_ref = String(default="")
    status = String(default="filed", indexed=True)  # filed | accepted | rejected | withdrawn
    case = String(default="")
