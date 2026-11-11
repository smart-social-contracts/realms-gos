"""Tender lots (ggg I-PRC-5): a tender divided into lots, a bid that prices one lot, each lot awarded on its own, possibly
to different vendors, or left void, and a contract for each awarded lot."""

import pytest

import fake_chain
from api.procurement import seal
from ggg import Contract, Tender
from kit import call, denied, later, ok
from test_procurement import DAY, RUBRIC, agora, bid, tender

LOTS = [{"id": "1", "title": "Bike lanes", "estimated_value": 40_000}, {"id": "2", "title": "Bike racks"},
        {"id": "3", "title": "Signs"}]


def evaluation():
    """T-1 with three lots: BuildCo bids on lots 1 and 2, RoadCorp on lot 1 and 3; all revealed after 30 days."""
    agora()
    t = tender(lots=LOTS)["tender"]
    ok("tender_open", "carla", tender=t)
    prices = {"b1": ("bob", "buildco", "1", 48_000), "b2": ("bob", "buildco", "2", 9_000),
              "b3": ("alice", "roadcorp", "1", 52_000), "b4": ("alice", "roadcorp", "3", 4_000)}
    ids = {key: bid(t, who, vendor, price, key, lot=lot) for key, (who, vendor, lot, price) in prices.items()}
    later(31 * DAY)
    ok("bids_reveal", "carla", tender=t, openings={ids[k]: {"price": prices[k][3], "nonce": k} for k in ids})
    return t, ids


def test_a_tender_names_its_lots():
    agora()
    t = tender(lots=LOTS)["tender"]
    assert [(lot["id"], lot["status"]) for lot in Tender[t].lots] == [("1", "open"), ("2", "open"), ("3", "open")]
    assert Tender[tender()["tender"]].lots == []
    shown = ok("tender_get", "alice", tender=t)["tender"]["lots"]
    assert [(lot["id"], lot["title"], lot["estimated_value"]) for lot in shown] == [("1", "Bike lanes", 40_000),
                                                                                 ("2", "Bike racks", None),
                                                                                 ("3", "Signs", None)]


@pytest.mark.parametrize("lots, why", [
    ([{"id": "1", "title": "a"}, {"id": "1", "title": "b"}], "named once"),
    ([{"id": "", "title": "a"}], "an id and a title"),
    ([{"id": str(i), "title": "t"} for i in range(65)], "at most 64"),
])
def test_lots_out_of_bounds_are_refused_and_leave_no_tender(lots, why):
    agora()
    now = fake_chain.now()
    assert why in denied("tender_create", "carla", title="x", requester="Infrastructure", opens_at=now,
                         closes_at=now + DAY, rubric=RUBRIC, lots=lots)
    assert Tender.count() == 0


def test_a_bid_prices_one_lot_when_the_tender_has_lots_and_none_when_it_has_none():
    agora()
    t, plain = tender(lots=LOTS)["tender"], tender()["tender"]
    for tender_id in (t, plain):
        ok("tender_open", "carla", tender=tender_id)

    def offer(tender_id, **extra):
        return call("bid_submit", "bob", tender=tender_id, vendor="buildco", payload_ref="vetkey:x",
                    payload_hash=seal(1, "n"), **extra)

    assert "has lots" in offer(t)["error"] and "no lot 9" in offer(t, lot="9")["error"]
    assert "no lots" in offer(plain, lot="1")["error"]
    first = offer(t, lot="1")["bid"]
    ok("bid_submit", "bob", tender=t, vendor="buildco", payload_ref="vetkey:y", payload_hash=seal(2, "m"), lot="2")
    assert "already bid" in offer(t, lot="1")["error"] and offer(plain)["ok"]
    assert [b["lot"] for b in ok("tender_get", "alice", tender=t)["tender"]["bids"]] == ["1", "2"] and first


def test_each_lot_is_awarded_on_its_own_to_its_best_bid_and_has_its_own_contract():
    t, ids = evaluation()
    ok("bid_score", "carla", bid=ids["b1"], scores={"price": 9, "quality": 8})
    ok("bid_score", "carla", bid=ids["b3"], scores={"price": 7, "quality": 7})
    ok("bid_score", "carla", bid=ids["b2"], scores={"price": 5, "quality": 5})
    ok("bid_score", "carla", bid=ids["b4"], scores={"price": 6, "quality": 6})
    assert "has lots" in denied("tender_award", "carla", tender=t)
    assert "is not a revealed bid" in denied("tender_award", "carla", tender=t, lot="1", bid=ids["b2"])  # lot 2's bid
    one = ok("tender_award", "carla", tender=t, lot="1")
    assert one["winner"] == ids["b1"]
    k = Contract[one["contract"]]
    assert (k.lot, k.tender, k.parties, k.title) == ("1", t, ["Department:Infrastructure", "Organization:buildco"],
                                                     "Bike lane works: Bike lanes")
    assert Tender[t].status == "evaluation" and Tender[t].winner == "" and Tender[t].contract == ""
    assert "not open for an award" in denied("tender_award", "carla", tender=t, lot="1")
    three = ok("tender_award", "carla", tender=t, lot="3")  # different lots, different vendors
    assert Contract[three["contract"]].parties[1] == "Organization:roadcorp"
    assert Tender[t].lots[0] == {"id": "1", "title": "Bike lanes", "estimated_value": 40_000, "status": "awarded",
                                 "winner": ids["b1"], "contract": one["contract"]}


def test_a_lot_with_no_acceptable_bid_is_left_void_and_the_tender_moves_on_once_no_lot_is_open():
    t, ids = evaluation()
    ok("tender_lot_void", "carla", tender=t, lot="2", reason="no bid within the estimate")
    assert [lot["status"] for lot in Tender[t].lots] == ["open", "void", "open"]
    assert "not open for an award" in denied("tender_award", "carla", tender=t, lot="2")
    assert "no open lot 2" in denied("tender_lot_void", "carla", tender=t, lot="2")
    denied("tender_lot_void", "alice", tender=t, lot="1")
    ok("bid_score", "carla", bid=ids["b1"], scores={"price": 9, "quality": 8})
    ok("tender_award", "carla", tender=t, lot="1")
    assert Tender[t].status == "evaluation"
    ok("tender_lot_void", "carla", tender=t, lot="3")
    assert Tender[t].status == "contracted"  # no lot is open: the awarded one has its contract


def test_a_tender_whose_every_lot_is_void_is_cancelled():
    t, _ = evaluation()
    for lot in ("1", "2", "3"):
        ok("tender_lot_void", "carla", tender=t, lot=lot)
    assert Tender[t].status == "cancelled"


def test_a_lots_contract_is_made_by_its_award_and_a_lot_is_named_only_with_its_tender():
    t, ids = evaluation()
    ok("bid_score", "carla", bid=ids["b1"], scores={"price": 9, "quality": 8})
    made = ok("tender_award", "carla", tender=t, lot="1")["contract"]
    assert "named only with the tender" in call("contract_create", "ctl", title="x", lot="1")["error"]
    assert "has lots" in call("contract_create", "ctl", title="x", tender=t)["error"]
    assert "no lot 9" in call("contract_create", "ctl", title="x", tender=t, lot="9")["error"]
    assert f"with contract {made}" in call("contract_create", "ctl", title="x", tender=t, lot="1")["error"]
    assert "is open" in call("contract_create", "ctl", title="x", tender=t, lot="2")["error"]


def test_a_lot_nobody_bid_on_cannot_be_awarded_only_left_void():
    agora()
    t = tender(lots=LOTS[:1])["tender"]
    ok("tender_open", "carla", tender=t)
    later(31 * DAY)
    ok("bids_reveal", "carla", tender=t, openings={})
    assert "has no evaluated bids" in denied("tender_award", "carla", tender=t, lot="1")
    ok("tender_lot_void", "carla", tender=t, lot="1")
    assert Tender[t].status == "cancelled"
