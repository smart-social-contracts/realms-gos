from ic_python_db import Integer, ManyToOne, OneToMany, String

from core.db import Json, Row


class Tender(Row):
    __alias__ = "id"
    id = String()
    title = String(default="")
    description = String(default="")
    requester = ManyToOne("Department", "tenders")
    status = String(default="draft")  # draft | open | closed | evaluation | awarded | contracted | cancelled
    opens_at = Integer(default=0)
    closes_at = Integer(default=0)
    rubric = Json({})  # {"price": 60, "quality": 40}
    winner = String(default="")
    contract = String(default="")
    bids = OneToMany("Bid", "tender")
