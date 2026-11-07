from ic_python_db import Integer, ManyToOne, OneToMany, String

from core.db import Json, Row
from core.ratio import upgrade_call, upgrade_federal_rule


class FederalVote(Row):
    __alias__ = "id"
    __version__ = 2  # 2: the action's arguments and the rule's threshold in hundred-millionths
    id = String()
    origin_quarter = String(default="")
    action = Json({})
    rule = Json({"kind": "majority_of_quarters"})
    hash = String(default="")
    deadline = Integer(default=0)
    status = String(default="open")  # open | passed | failed | cancelled
    tally = Json({})
    legs = OneToMany("FederalVoteLeg", "vote")

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        # The hash was made of the action and the rule as they were published, and stays so: the quarters answer with it.
        if obj.get("action"):
            obj["action"] = upgrade_call(obj["action"])
        if obj.get("rule"):
            obj["rule"] = upgrade_federal_rule(obj["rule"])
        return obj


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
