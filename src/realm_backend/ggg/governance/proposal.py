from ic_python_db import Integer, ManyToOne, OneToMany, String

from core.db import Json, Row
from core.ratio import ONE, PERCENT


class Proposal(Row):
    __alias__ = "id"
    __version__ = 2  # 2: threshold and quorum in hundred-millionths, not whole percents
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
    threshold = Integer(default=ONE // 2)  # share of the decisive votes the yes votes must exceed
    quorum = Integer(default=0)  # share of the electorate that must vote
    tally = Json({})
    result = Json(None)
    votes = OneToMany("Vote", "proposal")

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        # quorum_percent, the old field, shows a row still in whole percents: a row without it is converted already.
        if "quorum_percent" in obj:
            obj["quorum"] = obj.pop("quorum_percent") * PERCENT
            obj["threshold"] = obj.get("threshold", 50) * PERCENT
        return obj
