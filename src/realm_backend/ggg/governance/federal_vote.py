from ic_python_db import Integer, ManyToOne, OneToMany, String

from core.db import Json, Row


class FederalVote(Row):
    __alias__ = "id"
    id = String()
    origin_quarter = String(default="")
    action = Json({})
    rule = Json({"kind": "majority_of_quarters"})
    hash = String(default="")
    deadline = Integer(default=0)
    status = String(default="open")  # open | passed | failed | cancelled
    tally = Json({})
    legs = OneToMany("FederalVoteLeg", "vote")


class FederalVoteLeg(Row):
    __alias__ = "id"
    id = String()
    vote = ManyToOne("FederalVote", "legs")
    quarter = String(default="")
    proposal_id = String(default="")
    outcome = String(default="")
    yes = Integer(default=0)
    no = Integer(default=0)
    abstain = Integer(default=0)
    eligible = Integer(default=0)
    status = String(default="open")
    hash = String(default="")
