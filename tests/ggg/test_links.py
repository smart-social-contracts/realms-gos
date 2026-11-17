"""Proposal links (ggg#4, ggg I-GOV-9 to I-GOV-11, realms-gos#515): a proposal replaces a withdrawn one or relates to
others, fixed at creation, and the other side of every link can be read."""

import fake_chain
import ggg_methods as g
import world
from core import op
from ggg import Proposal, ProposalEdge
from kit import call, direct, ok

DAY = 86400


def file(who="alice", links=None, **args):
    return ok("proposal_create", who, title=args.pop("title", "x"), **({"links": links} if links is not None else {}),
              **args)["proposal"]


def withdrawn(who="alice"):
    p = file(who)
    ok("proposal_withdraw", who, proposal=p)
    return p


def replaces(p):
    return {"kind": "replaces", "proposal": p}


def relates_to(p):
    return {"kind": "relates_to", "proposal": p}


def refused(links, kind, who="alice", message=""):
    before = (Proposal.count(), ProposalEdge.count())
    out = call("proposal_create", who, title="x", links=links)
    assert not out["ok"] and out["kind"] == kind and message in out["error"], out
    assert (Proposal.count(), ProposalEdge.count()) == before  # a refused proposal leaves no trace
    return out


def linked_from(p, **page):
    return ok("proposal_linked_from", "alice", proposal=p, **page)


def test_a_proposal_has_no_links_unless_it_states_them():
    world.build()
    p = file()
    assert Proposal[p].links == [] and Proposal[p].replaced_by == ""
    assert ok("proposal_get", "alice", proposal=p)["proposal"]["links"] == []
    assert linked_from(p) == {"ok": True, "links": [], "next": None}
    assert g.ggg_proposal_get({"id": p})["Ok"]["proposal"]["links"] == []
    assert g.ggg_proposal_get({"id": p})["Ok"]["replaced_by"] is None


def test_a_proposal_replaces_a_withdrawn_one_and_the_old_one_points_forward():
    world.build()
    old, related = withdrawn(), file("bob")
    new = file(links=[replaces(old), relates_to(related)])
    assert Proposal[new].links == [replaces(old), relates_to(related)]
    assert Proposal[old].replaced_by == new
    assert ok("proposal_get", "alice", proposal=old)["proposal"]["replaced_by"] == new
    assert linked_from(old)["links"] == [replaces(new)]  # "replaced by"
    assert linked_from(related)["links"] == [relates_to(new)]  # a reference both ways
    detail = g.ggg_proposal_get({"id": old})["Ok"]
    assert detail["replaced_by"] == new and detail["proposal"]["links"] == []
    linked = g.ggg_proposal_get({"id": new})["Ok"]["proposal"]["links"]
    assert linked == [{"kind": {"Replaces": None}, "proposal": old}, {"kind": {"RelatesTo": None}, "proposal": related}]


def test_relates_to_names_any_proposal_in_any_status():
    world.build()
    ok("governance_update", "ctl", voting_window=0)
    open_one, other = file("bob"), file("carla")
    ok("proposal_withdraw", "carla", proposal=other)
    p = file(links=[relates_to(open_one), relates_to(other)])
    assert [link["proposal"] for link in Proposal[p].links] == [open_one, other]
    assert Proposal[open_one].replaced_by == ""


def test_the_links_are_checked_before_anything_is_written():
    world.build()
    pending, mine_withdrawn = file(), withdrawn()
    bobs_withdrawn = withdrawn("bob")
    refused([replaces(pending)], "conflict", message="must be withdrawn first")
    refused([replaces(bobs_withdrawn)], "forbidden", message="the member it was filed for")
    refused([replaces("P-99")], "not_found")
    refused([relates_to("P-99")], "not_found")
    refused([relates_to(pending), relates_to(pending)], "invalid_argument", message="twice")
    refused([relates_to(pending), replaces(pending)], "invalid_argument", message="twice")
    refused([relates_to(pending)] * 2 + [replaces(mine_withdrawn)], "invalid_argument", message="twice")
    refused([relates_to(pending) for _ in range(17)], "invalid_argument", message="at most 16")
    assert direct("proposal_create", "alice", title="x", links=[{"kind": "supersedes", "proposal": pending}])["kind"] == \
        "invalid_argument"
    assert direct("proposal_create", "alice", title="x", links=[{"kind": "replaces"}])["kind"] == "invalid_argument"
    assert direct("proposal_create", "alice", title="x", links="P-1")["kind"] == "invalid_argument"


def test_sixteen_links_are_allowed():
    world.build()
    targets = [file("bob") for _ in range(16)]
    p = file(links=[relates_to(t) for t in targets])
    assert len(Proposal[p].links) == 16


def test_a_withdrawn_proposal_is_replaced_once():
    world.build()
    old = withdrawn()
    first = file(links=[replaces(old)])
    refused([replaces(old)], "conflict", message="already replaced")
    assert Proposal[old].replaced_by == first
    assert linked_from(old)["links"] == [replaces(first)]


def test_a_replacement_that_is_withdrawn_too_is_replaced_by_a_new_proposal_not_the_first_one():
    world.build()
    a = withdrawn()
    b = file(links=[replaces(a)])
    ok("proposal_withdraw", "alice", proposal=b)
    refused([replaces(a)], "conflict", message="already replaced")  # A stays replaced by B
    c = file(links=[replaces(b)])
    assert (Proposal[a].replaced_by, Proposal[b].replaced_by, Proposal[c].replaced_by) == (b, c, "")


def test_a_proposal_filed_for_a_member_is_replaced_for_that_member_only():
    world.build()
    request = ok("delegation_request", "claude-conn", grantor="alice", operations=["proposal.create"],
                 expires_in=DAY)["request"]
    ok("delegation_request_decide", "alice", request=request, approve=True)
    old, bobs = withdrawn(), withdrawn("bob")
    refused([replaces(old)], "forbidden", who="bob")  # alice's withdrawn proposal is not bob's to replace
    out = call("proposal_create", "claude-conn", title="x", on_behalf_of="alice", links=[replaces(bobs)])
    assert not out["ok"] and out["kind"] == "forbidden"  # claude files for alice: bob's is not hers either
    made = ok("proposal_create", "claude-conn", title="x", on_behalf_of="alice", links=[replaces(old)])["proposal"]
    assert Proposal[made].proposer == "alice" and Proposal[old].replaced_by == made


def test_nothing_limits_the_links_to_a_proposal_and_they_are_read_a_page_at_a_time_oldest_first():
    world.build()
    target = file()
    linkers = [file("bob", links=[relates_to(target)]) for _ in range(23)]
    seen, cursor, pages = [], None, 0
    while True:
        page = linked_from(target, **({"from": cursor} if cursor else {}), count=5)
        assert page["links"] or page["next"] is None  # never an empty page with more to come
        seen += [link["proposal"] for link in page["links"]]
        pages += 1
        cursor = page["next"]
        if cursor is None:
            break
    assert seen == linkers and pages == 5  # every one once, oldest first


def test_the_ggg_page_and_the_unknown_id():
    world.build()
    target = file()
    for who in ("bob", "carla", "dana"):
        file(who, links=[relates_to(target)])
    first = g.ggg_proposal_linked_from({"id": target, "limit": 2})["Ok"]
    assert [link["proposal"] for link in first["items"]] == ["P-2", "P-3"] and first["next"]
    second = g.ggg_proposal_linked_from({"id": target, "cursor": first["next"], "limit": 2})["Ok"]
    assert [link["proposal"] for link in second["items"]] == ["P-4"] and second["next"] is None
    assert first["items"][0]["kind"] == {"RelatesTo": None}
    assert g.ggg_proposal_linked_from({"id": "P-99"}) == {"Ok": None}


def test_the_ggg_method_takes_links_as_an_option_and_refuses_the_same_way():
    world.build()
    old = withdrawn()
    fake_chain.state["caller"] = "alice"
    args = {"title": "x", "description": "", "department": None, "action": {"Motion": None}, "effective_at": None,
            "on_behalf_of": None}
    absent = op.run(g.ggg_proposal_create({**args, "links": None}))
    assert "Ok" in absent
    made = op.run(g.ggg_proposal_create({**args, "links": [{"kind": {"Replaces": None}, "proposal": old}]}))["Ok"]
    assert Proposal[old].replaced_by == made
    again = op.run(g.ggg_proposal_create({**args, "links": [{"kind": {"Replaces": None}, "proposal": old}]}))
    assert "Conflict" in again["Err"]
    missing = op.run(g.ggg_proposal_create({**args, "links": [{"kind": {"RelatesTo": None}, "proposal": "P-99"}]}))
    assert "NotFound" in missing["Err"]
    many = [{"kind": {"RelatesTo": None}, "proposal": old}] * 2
    assert "InvalidArgument" in op.run(g.ggg_proposal_create({**args, "links": many}))["Err"]
