"""A budget is proposed per fund (GGG D19): the department that owns the fund governs it, its lines are fixed as
``proposed`` when the proposal is filed, and a proposal that ends without adopting them gives them back (realms-gos#588)."""

import fake_chain
import world
from core import op
from ggg import Budget, Department, Fund, Proposal
from kit import call, denied, later, ok
from test_treasury import draft, setup_finance

WEEK = 7 * 86400
HOUR = 3600
FOUR = ("alice", "bob", "dana", "carla")  # more than half of the seven members: enough to carry a realm-wide vote


def roads_budget(**extra):
    """The action of a proposal that carries the budget of the ROADS fund."""
    return {"op": "budget_propose", "args": {"fund": "ROADS", "period": "FY2026", **extra}}


def file_roads_budget(by="carla", department="Infrastructure"):
    """A proposal of ``department`` carrying the budget of ROADS, filed as a member files any proposal."""
    return ok("proposal_create", by, title="Roads budget", department=department, action=roads_budget())["proposal"]


def infrastructure_decides(depts, m=2):
    """Infrastructure, the owner of ROADS, decides m of 3 seats (carla's, alice's and dana's): bob's governed call
    files a proposal for them."""
    for voter in ("alice", "dana"):
        world.seat(f"infrastructure.{voter}", voter, depts["Infrastructure"])
    world.policy(depts["Infrastructure"], m, 3)


def vote(proposal, *voters):
    for voter in voters:
        ok("proposal_vote", voter, proposal=proposal, choice="yes")


def states(*lines):
    return [(Budget[b].status, Budget[b].proposal) for b in lines]


# --- by fund --------------------------------------------------------------------------------------

def test_a_budget_proposes_the_draft_lines_of_the_fund_it_names_and_leaves_the_others():
    setup_finance()
    capital, upkeep = draft("ROADS", "capital", 300), draft("ROADS", "upkeep", 20)
    salaries = draft("ROOT", "salaries", 500)
    out = ok("budget_propose", "bob", fund="ROADS", period="FY2026")
    assert (out["budgets"], out["expense"], out["revenue"]) == ([capital, upkeep], 320, 0)
    assert states(capital, upkeep, salaries) == [("adopted", ""), ("adopted", ""), ("draft", "")]
    assert ok("budget_propose", "bob", fund="ROOT", period="FY2026")["budgets"] == [salaries]
    assert Budget[salaries].status == "adopted"


def test_a_budget_that_names_no_fund_proposes_every_funds_draft_lines():
    setup_finance()
    lines = [draft("ROADS", "capital", 300), draft("ROOT", "salaries", 500)]
    assert ok("budget_propose", "bob", period="FY2026")["budgets"] == lines


def test_a_fund_without_draft_lines_has_nothing_to_propose_and_an_unknown_fund_is_not_found():
    setup_finance()
    salaries = draft("ROOT", "salaries", 500)
    out = call("budget_propose", "bob", fund="ROADS", period="FY2026")
    assert out["kind"] == "conflict" and "no draft budget lines of fund ROADS" in out["error"]
    assert call("budget_propose", "bob", fund="NOWHERE", period="FY2026")["kind"] == "not_found"
    assert states(salaries) == [("draft", "")] and Proposal.count() == 0


def test_the_totals_of_a_budget_are_those_of_the_lines_it_proposes():
    setup_finance()
    draft("ROOT", "property_tax", 900, kind="revenue")
    draft("ROADS", "capital", 300)
    draft("ROADS", "upkeep", 20)
    totals = op.ops()["budget_propose"]["derived"]
    assert totals({"period": "FY2026", "fund": "ROADS"}) == {"revenue": 0, "expense": 320}
    assert totals({"period": "FY2026", "fund": "ROOT"}) == {"revenue": 900, "expense": 0}
    assert totals({"period": "FY2026"}) == {"revenue": 900, "expense": 320}


def test_lines_adopt_in_draft_order_so_a_later_line_amends_an_earlier_one_and_inherits_its_spending():
    setup_finance()
    old = draft("ROADS", "capital", 100)
    ok("budget_propose", "bob", fund="ROADS", period="FY2026")
    Budget[old].actual = 60
    first, second = draft("ROADS", "capital", 300), draft("ROADS", "capital", 400)
    ok("budget_propose", "bob", fund="ROADS", period="FY2026")
    assert [Budget[b].status for b in (old, first, second)] == ["amended", "amended", "adopted"]
    assert Budget[second].actual == 60


def test_the_ledgers_fee_is_still_not_a_category_to_budget():
    setup_finance()
    out = call("budget_draft", "bob", fund="ROADS", period="FY2026", category="ledger_fee", kind="expense", planned=1)
    assert out["kind"] == "invalid_argument" and Budget.count() == 0


# --- the department that owns the fund decides ----------------------------------------------------

def test_the_department_that_owns_the_fund_decides_its_budget_by_its_own_seats():
    depts = setup_finance()
    capital, salaries = draft("ROADS", "capital", 300), draft("ROOT", "salaries", 500)
    infrastructure_decides(depts)
    out = ok("budget_propose", "bob", fund="ROADS", period="FY2026")
    p = out["proposal"]
    assert out["requires_vote"] and Proposal[p].department.id == "Infrastructure"
    assert Proposal[p].tally["eligible"] == 3  # the department's seats, not the realm's seven members
    assert "not eligible" in denied("proposal_vote", "mallory", proposal=p, choice="yes")
    vote(p, "carla")
    assert states(capital) == [("proposed", p)]
    vote(p, "alice")
    assert Proposal[p].status == "executed" and states(capital, salaries) == [("adopted", p), ("draft", "")]


def test_the_root_governs_a_budget_that_names_no_fund_and_a_fund_no_department_owns():
    setup_finance()
    Fund(id="RESERVE", name="Reserve")
    reserve, capital = draft("RESERVE", "emergency", 100), draft("ROADS", "capital", 300)
    world.policy(Department["Administration"], 2, 3)
    own = ok("budget_propose", "bob", fund="RESERVE", period="FY2026")["proposal"]
    assert Proposal[own].department.is_root and states(reserve, capital) == [("proposed", own), ("draft", "")]
    whole = ok("budget_propose", "bob", period="FY2026")["proposal"]
    assert Proposal[whole].department.is_root and states(capital) == [("proposed", whole)]


def test_a_budget_is_decided_directly_by_an_owner_whose_policy_is_direct():
    setup_finance()
    capital = draft("ROADS", "capital", 300)
    out = ok("budget_propose", "bob", fund="ROADS", period="FY2026")
    assert "proposal" not in out and states(capital) == [("adopted", "")]


def test_a_proposal_carries_the_budget_of_a_fund_only_from_its_owner_or_above_or_from_the_realm():
    setup_finance()
    capital = draft("ROADS", "capital", 300)
    out = call("proposal_create", "bob", title="x", department="Finance", action=roads_budget())
    assert out["kind"] == "forbidden" and "not governed by Finance" in out["error"]
    out = call("proposal_create", "carla", title="x", department="Infrastructure",
               action={"op": "budget_propose", "args": {"period": "FY2026"}})
    assert out["kind"] == "forbidden" and "only a realm-wide proposal" in out["error"]
    assert Proposal.count() == 0 and states(capital) == [("draft", "")]
    ok("governance_update", "ctl", voting_window=0)
    p = file_roads_budget("alice", department="Administration")  # the realm's vote carries any call
    vote(p, *FOUR)
    assert Proposal[p].status == "executed" and states(capital) == [("adopted", p)]


# --- the lines are fixed when the proposal is filed -----------------------------------------------

def test_the_lines_are_proposed_with_the_proposal_when_it_is_filed_and_a_later_draft_waits():
    setup_finance()
    capital = draft("ROADS", "capital", 300)
    p = file_roads_budget()
    assert states(capital) == [("proposed", p)]
    late = draft("ROADS", "upkeep", 20)  # drafted after the filing: not part of what the vote decides
    vote(p, "carla")
    assert Proposal[p].status == "executed" and states(capital, late) == [("adopted", p), ("draft", "")]
    q = file_roads_budget()
    vote(q, "carla")
    assert states(late) == [("adopted", q)]


def test_a_governed_call_fixes_its_lines_when_it_files_the_proposal():
    depts = setup_finance()
    capital, upkeep = draft("ROADS", "capital", 300), draft("ROADS", "upkeep", 20)
    infrastructure_decides(depts)
    p = ok("budget_propose", "bob", fund="ROADS", period="FY2026")["proposal"]
    assert states(capital, upkeep) == [("proposed", p), ("proposed", p)]


def test_two_proposals_cannot_carry_the_same_line():
    depts = setup_finance()
    capital, salaries = draft("ROADS", "capital", 300), draft("ROOT", "salaries", 500)
    infrastructure_decides(depts)
    p = ok("budget_propose", "bob", fund="ROADS", period="FY2026")["proposal"]
    again = call("budget_propose", "bob", fund="ROADS", period="FY2026")
    assert again["kind"] == "conflict" and "no draft budget lines of fund ROADS" in again["error"]
    carried = call("proposal_create", "carla", title="Roads budget", department="Infrastructure", action=roads_budget())
    assert carried["kind"] == "conflict"
    assert Proposal.count() == 1 and states(capital) == [("proposed", p)]
    # The realm's own budget takes the draft lines that are left, and another fund's goes on.
    assert ok("budget_propose", "bob", period="FY2026")["budgets"] == [salaries]
    assert states(capital) == [("proposed", p)]


def test_a_proposal_of_a_budget_that_cannot_be_proposed_files_nothing():
    setup_finance()
    huge, small = draft("ROADS", "capital", 2**53 - 1), draft("ROADS", "upkeep", 1)
    unknown = {"op": "budget_propose", "args": {"fund": "ROADS", "period": "FY1999"}}
    for action, kind in ((unknown, "not_found"), (roads_budget(years=0), "invalid_argument"),
                         (roads_budget(), "invalid_argument")):  # the last: a total above the largest amount of money
        out = call("proposal_create", "carla", title="x", department="Infrastructure", action=action)
        assert out["kind"] == kind, out
    assert Proposal.count() == 0 and states(huge, small) == [("draft", "")] * 2


# --- a proposal that ends without adopting its lines gives them back ------------------------------

def given_back(*lines):
    assert states(*lines) == [("draft", "")] * len(lines)


def test_a_rejected_proposal_gives_its_lines_back_and_the_budget_can_be_proposed_again():
    depts = setup_finance()
    capital = draft("ROADS", "capital", 300)
    infrastructure_decides(depts)
    p = ok("budget_propose", "bob", fund="ROADS", period="FY2026")["proposal"]
    ok("proposal_vote", "carla", proposal=p, choice="no")
    later(WEEK + 1)
    assert ok("proposal_finalize", "ctl", proposal=p)["status"] == "rejected"
    given_back(capital)
    again = ok("budget_propose", "bob", fund="ROADS", period="FY2026")["proposal"]
    assert again != p and states(capital) == [("proposed", again)]


def test_a_withdrawn_proposal_gives_its_lines_back():
    depts = setup_finance()
    capital = draft("ROADS", "capital", 300)
    infrastructure_decides(depts)
    p = ok("budget_propose", "bob", fund="ROADS", period="FY2026")["proposal"]
    ok("proposal_withdraw", "bob", proposal=p)
    assert Proposal[p].status == "withdrawn"
    given_back(capital)


def test_an_expired_proposal_gives_its_lines_back():
    setup_finance()
    capital = draft("ROADS", "capital", 300)
    p = ok("proposal_create", "carla", title="Roads budget", department="Infrastructure", action=roads_budget(),
           effective_at=fake_chain.now() + HOUR)["proposal"]
    later(2 * HOUR)  # the vote ends after the time the budget was to take effect
    vote(p, "carla")
    assert Proposal[p].status == "expired"
    given_back(capital)


def test_a_stopped_proposal_gives_its_lines_back():
    setup_finance()
    capital = draft("ROADS", "capital", 300)
    p = ok("proposal_create", "carla", title="Roads budget", department="Infrastructure", action=roads_budget(),
           effective_at=fake_chain.now() + HOUR)["proposal"]
    vote(p, "carla")
    assert Proposal[p].status == "execution_scheduled" and states(capital) == [("proposed", p)]
    ok("proposal_stop", "ctl", proposal=p)
    assert Proposal[p].status == "withdrawn"
    given_back(capital)


def test_a_proposal_that_fails_when_it_runs_gives_its_lines_back():
    depts = setup_finance()
    capital = draft("ROADS", "capital", 300)
    infrastructure_decides(depts)
    p = ok("budget_propose", "bob", fund="ROADS", period="FY2026")["proposal"]
    ok("period_close", "bob", period="FY2026")
    vote(p, "carla", "alice")
    assert Proposal[p].status == "failed" and "closed" in Proposal[p].result["error"]
    given_back(capital)


def test_a_proposal_that_ends_frees_only_its_own_lines():
    setup_finance()
    capital = draft("ROADS", "capital", 300)
    old = file_roads_budget()
    Budget[capital].status, Budget[capital].proposal = "draft", ""  # as the release before this one filed it
    new = file_roads_budget()
    ok("proposal_withdraw", "carla", proposal=old)
    assert states(capital) == [("proposed", new)]


def test_a_proposal_whose_stored_action_names_no_period_still_ends():
    setup_finance()
    p = ok("proposal_create", "carla", title="x", department="Infrastructure")["proposal"]
    Proposal[p].action = {"op": "budget_propose", "args": {}}
    ok("proposal_withdraw", "carla", proposal=p)
    assert Proposal[p].status == "withdrawn"


# --- proposals filed before the lines were fixed --------------------------------------------------

def test_a_proposal_filed_before_lines_were_fixed_to_proposals_adopts_nothing_and_leaves_the_drafts():
    """The release before this one left the lines of a filed proposal as plain drafts. Voted after the upgrade, such a
    proposal fails closed: adopting every draft of the fund would take lines nobody voted on."""
    setup_finance()
    capital = draft("ROADS", "capital", 300)
    p = file_roads_budget()
    Budget[capital].status, Budget[capital].proposal = "draft", ""  # as the release before this one filed it
    late = draft("ROADS", "upkeep", 20)
    vote(p, "carla")
    assert Proposal[p].status == "failed" and "fixed no budget line" in Proposal[p].result["error"]
    given_back(capital, late)
    ok("budget_propose", "bob", fund="ROADS", period="FY2026")
    assert [Budget[b].status for b in (capital, late)] == ["adopted", "adopted"]
