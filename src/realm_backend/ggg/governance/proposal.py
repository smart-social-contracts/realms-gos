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
    status = String(default="voting", indexed=True)  # draft | voting | accepted | rejected | expired | executed | failed | withdrawn
    opens_at = Integer(default=0)
    deadline = Integer(default=0)
    threshold = Integer(default=50)  # percent of decisive votes
    quorum_percent = Integer(default=0)
    tally = Json({})
    result = Json(None)
    votes = OneToMany("Vote", "proposal")
