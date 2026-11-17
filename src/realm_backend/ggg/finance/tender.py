from ic_python_db import Integer, ManyToOne, OneToMany, String

from core.db import Json, Row
from core.ratio import PERCENT


class Tender(Row):
    __alias__ = "id"
    # 2: rubric weights in hundred-millionths, not whole percents; 3: kind, procedure, notices; 4: lots
    __version__ = 4
    id = String()
    title = String(default="")
    description = String(default="")
    requester = ManyToOne("Department", "tenders")
    kind = String(default="")  # what is tendered: works, supplies, services (the realm's words)
    procedure = String(default="")  # by which procedure: open, restricted, ... (the realm's words)
    estimated_value = Integer()  # in the accounting currency; None when the requester gave none
    status = String(default="draft")  # draft | open | closed | evaluation | awarded | contracted | cancelled
    opens_at = Integer(default=0)
    closes_at = Integer(default=0)
    rubric = Json({})  # {"price": 60_000_000, "quality": 40_000_000}
    winner = String(default="")
    contract = String(default="")
    notices = Json([])  # where the tender was published: {"journal", "published_at", "ref"}, in the order recorded
    # Its parts, each awarded on its own (ggg I-PRC-5): {"id", "title", "estimated_value", "status": open | awarded |
    # void, "winner", "contract"}. Empty for a tender that is not divided, which has its own winner and contract.
    lots = Json([])
    bids = OneToMany("Bid", "tender")

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        obj.setdefault("notices", [])  # a Json field the row lacks would read as the text of its default
        obj.setdefault("lots", [])
        weights = obj.get("rubric") or {}
        # Weights add up to exactly 100 % (100 whole percents, 100 000 000 hundred-millionths): a total of 100 is not
        # converted yet.
        if sum(weights.values()) == 100:
            obj["rubric"] = {criterion: weight * PERCENT for criterion, weight in weights.items()}
        return obj
