"""A proposal's tags (ggg I-GOV-15): the proposer's own words, set when it is filed and replaced while it is a draft,
and what ``proposal_list`` finds proposals by."""

import pytest

import world
from ggg import Proposal
from kit import call, denied, ok


def file(title, **extra):
    return ok("proposal_create", "alice", title=title, **extra)["proposal"]


def listed(**args):
    return [p["id"] for p in ok("proposal_list", "bob", **args)["proposals"]]


def test_a_proposal_carries_the_tags_it_was_filed_with():
    world.build()
    parks = file("Plant trees", tags=["parks", "district-2"])
    assert Proposal[parks].tags == ["parks", "district-2"]
    assert Proposal[file("No tags")].tags == []
    assert ok("proposal_list", "bob")["proposals"][0]["tags"] == ["parks", "district-2"]


@pytest.mark.parametrize("tags, why", [
    (["parks"] * 2, "each named once"),
    ([f"t{i}" for i in range(17)], "at most 16"),
    ([""], "1 to 64 code points"),
    (["x" * 65], "1 to 64 code points"),
    ([" parks"], "no whitespace"),
    (["parks　"], "no whitespace"),
    (["par\x00ks"], "no control character"),
    (["par\x9fks"], "no control character"),
    ("parks", "list of text"),
    ([7], "list of text"),
])
def test_tags_out_of_bounds_are_refused_and_leave_no_proposal(tags, why):
    world.build()
    assert why in call("proposal_create", "alice", title="x", tags=tags)["error"]
    assert Proposal.count() == 0


def test_a_tag_is_counted_in_code_points_and_compared_exactly():
    world.build()
    assert Proposal[file("Valencia", tags=["València" * 8])].tags == ["València" * 8]  # 64 code points, 72 bytes
    both = file("Case", tags=["Parks", "parks"])  # two tags: GGG never folds case or normalises
    assert Proposal[both].tags == ["Parks", "parks"]
    assert listed(tags=["PARKS"]) == []


def test_a_list_finds_the_proposals_carrying_any_of_the_tags_with_its_status():
    world.build()
    parks, roads, none = file("Parks", tags=["parks"]), file("Roads", tags=["roads", "parks"]), file("None")
    ok("proposal_withdraw", "alice", proposal=roads)
    assert listed(tags=["parks"]) == [parks, roads]
    assert listed(tags=["roads", "none"]) == [roads]
    assert listed(tags=["parks"], status="voting") == [parks]
    assert listed(tags=[]) == [parks, roads, none]
    assert "at most 16" in call("proposal_list", "bob", tags=[f"t{i}" for i in range(17)])["error"]


def test_only_a_drafts_tags_are_replaced_and_only_by_its_proposer():
    world.build()
    voting = file("Open", tags=["parks"])
    assert "only a draft" in call("proposal_set_tags", "alice", proposal=voting, tags=["roads"])["error"]
    Proposal[voting].status = "draft"
    assert "not your proposal" in denied("proposal_set_tags", "bob", proposal=voting, tags=["roads"])
    assert "no whitespace" in call("proposal_set_tags", "alice", proposal=voting, tags=[" x"])["error"]
    ok("proposal_set_tags", "alice", proposal=voting, tags=["roads"])
    assert Proposal[voting].tags == ["roads"]
    ok("proposal_set_tags", "alice", proposal=voting, tags=[])
    assert Proposal[voting].tags == []
