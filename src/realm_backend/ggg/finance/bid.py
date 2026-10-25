from ic_python_db import Integer, ManyToOne, OneToMany, String

from core.db import Row


class Bid(Row):
    __alias__ = "id"
    id = String()
    tender = ManyToOne("Tender", "bids")
    vendor = ManyToOne("Organization", "bids")
    submitted_at = Integer(default=0)
    sealed = Integer(default=1)
    payload_ref = String(default="")
    payload_hash = String(default="")
    price = Integer(default=0)  # revealed with the bid
    score = Integer(default=0)
    scores = OneToMany("BidScore", "bid")


class BidScore(Row):
    __alias__ = "id"
    id = String()
    bid = ManyToOne("Bid", "scores")
    evaluator = String(default="")
    criterion = String(default="")
    score = Integer(default=0)
    scored_at = Integer(default=0)
