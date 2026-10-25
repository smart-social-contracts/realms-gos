import fake_chain
import world
from ggg import Bid, Contract, Delegation, Department, Event, Organization, Tender
from kit import call, denied, later, ok
from api.procurement import seal

DAY = 86400
RUBRIC = {"price": 60, "quality": 40}


def agora():
    """Infrastructure (Carla) runs tenders; BuildCo (admin Bob) and RoadCorp (admin Alice) bid."""
    depts = world.build()
    Organization(id="roadcorp", description="RoadCorp, contractor", admins=["alice"])
    return depts


def tender(caller="carla", **extra):
    now = fake_chain.now()
    args = dict(title="Bike lane works", requester="Infrastructure", opens_at=now, closes_at=now + 30 * DAY,
                rubric=RUBRIC, **extra)
    return ok("tender_create", caller, **args)


def bid(t, who, vendor, price, nonce, **extra):
    return ok("bid_submit", who, tender=t, vendor=vendor, payload_ref=f"vetkey:{vendor}:{t}",
              payload_hash=seal(price, nonce), **extra)["bid"]


def evaluation():
    """T-1 open, BuildCo bids 48,000 and RoadCorp 52,000, revealed after 30 days."""
    agora()
    t = tender()["tender"]
    ok("tender_open", "carla", tender=t)
    b1, b2 = bid(t, "bob", "buildco", 48000, "n1"), bid(t, "alice", "roadcorp", 52000, "n2")
    later(31 * DAY)
    ok("bids_reveal", "carla", tender=t, openings={b1: {"price": 48000, "nonce": "n1"},
                                                   b2: {"price": 52000, "nonce": "n2"}})
    return t, b1, b2


def test_story_t5_awarded_to_buildco():
    t, b1, b2 = evaluation()
    assert ok("bid_score", "carla", bid=b1, scores={"price": 9, "quality": 8})["score"] == 86
    assert ok("bid_score", "carla", bid=b2, scores={"price": 7, "quality": 7})["score"] == 70
    denied("tender_award", "alice", tender=t)
    out = ok("tender_award", "carla", tender=t)
    assert (out["winner"], out["score"]) == (b1, 86)
    k = Contract[out["contract"]]
    assert k.parties == ["Department:Infrastructure", "Organization:buildco"] and k.status == "active"
    assert k.terms_hash == seal(48000, "n1")
    assert Tender[t].status == "contracted" and Tender[t].winner == b1
    got = ok("tender_get", "alice", tender=t)["tender"]
    assert {b["id"]: b["price"] for b in got["bids"]} == {b1: 48000, b2: 52000}
    assert [x["id"] for x in ok("tender_list", "alice", status="contracted")["tenders"]] == [t]
    denied("tender_list", "eve")


def test_tender_lifecycle_and_authorization():
    world.build()
    denied("tender_create", "alice", title="x", requester="Infrastructure", opens_at=1, closes_at=2, rubric=RUBRIC)
    assert "add up to 100" in denied("tender_create", "carla", title="x", requester="Infrastructure",
                                     opens_at=1, closes_at=2, rubric={"price": 50})
    t = tender()["tender"]
    denied("tender_open", "alice", tender=t)
    ok("tender_open", "carla", tender=t)
    denied("tender_close", "alice", tender=t)
    denied("tender_cancel", "alice", tender=t)
    ok("tender_close", "carla", tender=t)
    assert Tender[t].closes_at == fake_chain.now()
    assert "closed" in denied("tender_cancel", "carla", tender=t)
    t2 = tender()["tender"]
    ok("tender_cancel", "carla", tender=t2)
    denied("tender_get", "eve", tender=t)


def test_tender_ops_are_governed_by_the_requester():
    depts = agora()
    world.policy(depts["Infrastructure"], 2, 3)
    out = tender()
    assert out["requires_vote"] and Tender.count() == 0


def test_bids_from_representatives_only_and_by_delegation():
    agora()
    t = tender()["tender"]
    ok("tender_open", "carla", tender=t)
    denied("bid_submit", "eve", tender=t, vendor="buildco", payload_ref="r", payload_hash=seal(1, "n"))
    assert "represent" in denied("bid_submit", "dr-ana", tender=t, vendor="buildco", payload_ref="r",
                                 payload_hash=seal(1, "n"))
    assert "sha256" in denied("bid_submit", "bob", tender=t, vendor="buildco", payload_ref="r", payload_hash="48000")
    Delegation(id="d1", grantor="bob", delegate="claude-conn", status="active",
               scope={"operations": ["bid.submit"]}, expires_at=fake_chain.now() + DAY)
    b = bid(t, "claude-conn", "buildco", 48000, "n1", on_behalf_of="bob")
    assert "already bid" in denied("bid_submit", "bob", tender=t, vendor="buildco", payload_ref="r",
                                   payload_hash=seal(1, "n"))
    assert "represent" in denied("bid_withdraw", "alice", bid=b)
    ok("bid_withdraw", "bob", bid=b)
    assert Bid[b] is None
    Organization["roadcorp"].status = "suspended"
    assert "suspended" in denied("bid_submit", "alice", tender=t, vendor="roadcorp", payload_ref="r",
                                 payload_hash=seal(1, "n"))


def test_I_PRC_1_bids_stay_sealed_until_reveal():
    agora()
    t = tender()["tender"]
    ok("tender_open", "carla", tender=t)
    b = bid(t, "bob", "buildco", 48000, "n1")
    shown = ok("tender_get", "carla", tender=t)["tender"]["bids"][0]
    assert shown["sealed"] and "payload_ref" not in shown and "price" not in shown
    assert Bid[b].price == 0
    assert "sealed until" in denied("bids_reveal", "carla", tender=t)
    assert "not open for evaluation" in denied("bid_score", "carla", bid=b, scores={"price": 9})
    later(31 * DAY)
    denied("bids_reveal", "alice", tender=t)
    assert "does not match" in denied("bids_reveal", "carla", tender=t, openings={b: {"price": 40000, "nonce": "n1"}})
    assert Bid[b].sealed
    ok("bids_reveal", "carla", tender=t, openings={b: {"price": 48000, "nonce": "n1"}})
    shown = ok("tender_get", "carla", tender=t)["tender"]["bids"][0]
    assert shown["price"] == 48000 and shown["payload_ref"] == f"vetkey:buildco:{t}"


def test_I_PRC_2_evaluator_not_from_the_vendor():
    t, b1, b2 = evaluation()
    world.seat("infrastructure.evaluator", "bob", Department["Infrastructure"], "bid.evaluate")
    assert "party" in denied("bid_score", "bob", bid=b1, scores={"price": 9})
    ok("bid_score", "bob", bid=b2, scores={"price": 7})
    denied("bid_score", "alice", bid=b1, scores={"price": 9})
    assert "rubric" in denied("bid_score", "carla", bid=b1, scores={"speed": 9})
    assert "rubric" in denied("bid_score", "carla", bid=b1, scores={"price": 11})


def test_I_PRC_3_award_to_best_or_justify():
    t, b1, b2 = evaluation()
    ok("bid_score", "carla", bid=b1, scores={"price": 9, "quality": 8})
    ok("bid_score", "carla", bid=b2, scores={"price": 7, "quality": 7})
    assert "justify" in denied("tender_award", "carla", tender=t, bid=b2)
    why = "BuildCo lost its road-works licence"
    out = ok("tender_award", "carla", tender=t, bid=b2, justification=why)
    assert out["winner"] == b2
    award = [e for e in Event.load_some(1, 500) if e.op == "tender_award"][-1]
    assert award.args["justification"] == why and award.result["justification"] == why


def test_I_PRC_4_bids_only_while_open():
    agora()
    now = fake_chain.now()
    t = ok("tender_create", "carla", title="x", requester="Infrastructure", opens_at=now + DAY,
           closes_at=now + 30 * DAY, rubric=RUBRIC)["tender"]
    assert "does not accept" in call("bid_submit", "bob", tender=t, vendor="buildco", payload_ref="r",
                                     payload_hash=seal(1, "n"))["error"]
    ok("tender_open", "carla", tender=t)
    assert "does not accept" in denied("bid_submit", "bob", tender=t, vendor="buildco", payload_ref="r",
                                       payload_hash=seal(1, "n"))
    later(DAY)
    b = bid(t, "bob", "buildco", 1, "n")
    later(30 * DAY)
    assert "does not accept" in denied("bid_submit", "alice", tender=t, vendor="roadcorp", payload_ref="r",
                                       payload_hash=seal(1, "n"))
    assert "closed" in denied("bid_withdraw", "bob", bid=b)
