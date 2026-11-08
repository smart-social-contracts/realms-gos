from ic_python_db import Integer, ManyToOne, OneToMany, String

from core.db import Json, Row
from core.ratio import PERCENT


class Tender(Row):
    __alias__ = "id"
    __version__ = 2  # 2: rubric weights in hundred-millionths, not whole percents
    id = String()
    title = String(default="")
    description = String(default="")
    requester = ManyToOne("Department", "tenders")
    status = String(default="draft")  # draft | open | closed | evaluation | awarded | contracted | cancelled
    opens_at = Integer(default=0)
    closes_at = Integer(default=0)
    rubric = Json({})  # {"price": 60_000_000, "quality": 40_000_000}
    winner = String(default="")
    contract = String(default="")
    bids = OneToMany("Bid", "tender")

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        weights = obj.get("rubric") or {}
        # Weights add up to exactly 100 % (100 whole percents, 100 000 000 hundred-millionths): a total of 100 is not
        # converted yet.
        if sum(weights.values()) == 100:
            obj["rubric"] = {criterion: weight * PERCENT for criterion, weight in weights.items()}
        return obj
