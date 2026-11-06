from ic_python_db import Integer, ManyToOne, OneToMany, String

from core.db import Json, Row


class Proposal(Row):
    __alias__ = "id"
    id = String()
    title = String(default="")
    description = String(default="")
    proposer = String(default="")
    on_behalf_of = String(default="")
    department = ManyToOne("Department", "proposals")
    action = Json(None)  # {"op": name, "args": {...}} replayed once accepted
    # draft | voting | accepted | execution_scheduled | rejected | expired | executed | failed | withdrawn
    status = String(default="voting", indexed=True)
    opens_at = Integer(default=0)
    deadline = Integer(default=0)
    effective_at = Integer(default=0)  # seconds; once accepted, the action runs then. 0: on acceptance
    threshold = Integer(default=50)  # percent of decisive votes
    quorum_percent = Integer(default=0)
    tally = Json({})
    result = Json(None)
    votes = OneToMany("Vote", "proposal")
