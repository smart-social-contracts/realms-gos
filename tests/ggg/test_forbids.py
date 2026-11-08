"""The generated forbids in force (docs/codices/layered-codex.md §9.4, realms-gos#489), in process: the Cedar a layered
codex's forbids become is parsed once when the codex becomes the running one and judges the operation's facts, each
element of a list a forbid ranges over, and the replay of an accepted proposal. tests/deployed/test_forbids.py runs the
same on the real wasm and measures what it costs."""

import pytest

import world
from core import auth, forbids, layered_codex as store, legislation, localtime, sandbox
from core.db import Fail
from ggg import Budget, LayeredCodex, Proposal, TaxPolicy
from kit import call, denied, ok, rows
from realms.legislation import RESERVED
from realms.legislation.cedar import FactSchema
from test_budget_proposal import file_roads_budget, vote
from test_legislation_runtime import activate, layered
from test_treasury import draft, setup_finance

SPAIN, VALENCIA = "eu/es", "eu/es/vc/46250-valencia"
SPAIN_TAX = """
from realms.legislation import F, S, above, forbid, overridable, pct, when

class Tax(above("tax")):
    capital = overridable(False, legislation="TRLRHL Art. 72.3")
    surface_public_transport = overridable(False, legislation="TRLRHL Art. 72.3")

    ibi_rate_required = forbid("tax_policy_propose",
        F.code.in_(["ibi_urban", "ibi_rustic"]) & F.rate.is_none(),
        each="taxes", legislation="TRLRHL Art. 72")

    ibi_urban_range = forbid("tax_policy_propose",
        (F.code == "ibi_urban") & F.rate.is_some() & ~F.rate.between(pct("0.40"), pct("1.30")),
        each="taxes", legislation="TRLRHL Art. 72.1, 72.3")

    ibi_urban_cap = forbid("tax_policy_propose",
        (F.code == "ibi_urban") & F.rate.is_some() & (F.rate > pct("1.10")
            + when(S.capital, pct("0.07")) + when(S.surface_public_transport, pct("0.07"))),
        each="taxes", legislation="TRLRHL Art. 72.3")
"""
VALENCIA_TAX = """
from realms.legislation import above

class Tax(above("tax")):
    capital = True
    surface_public_transport = True
"""
CAP = 1_240_000  # 1.24 %: 1.10 % and 0.07 % for a capital and for surface public transport
BUDGET_CAP = """
from realms.legislation import F, above, forbid

class Treasury(above("treasury")):
    expense_cap = forbid("budget_propose", F.expense > 1000, legislation="a budget spends at most 1000")
"""


def entry(code, rate=None, subject="Land"):
    return {"code": code, "kind": "tax", "subject": subject, **({} if rate is None else {"rate": rate}), "facts": {}}


def drafted(*entries):
    return {"document": {"symbol": "REALM"}, "taxes": list(entries)}


def codex_of(chapters, **fields):
    return activate(layered(chapters, layers=list(dict.fromkeys(source for source, _ in chapters)), **fields))


def valencia():
    return codex_of({(SPAIN, "tax"): SPAIN_TAX, (VALENCIA, "tax"): VALENCIA_TAX})


class Requests:
    """The Cedar requests the realm makes: (action, facts) of each, in order."""

    def __init__(self, monkeypatch):
        self.asked, self.loads, decide, load = [], [], auth.decide, auth.load

        def counting(principal, action, resource, entities, context):
            self.asked.append((action.split("::")[-1].strip('"'), context.get("facts")))
            return decide(principal, action, resource, entities, context)

        def loading(schema, policies):
            self.loads.append(policies)
            return load(schema, policies)

        monkeypatch.setattr(auth, "decide", counting)
        monkeypatch.setattr(auth, "load", loading)

    def during(self, *call_args, **call_kwargs):
        before = len(self.asked)
        out = call(*call_args, **call_kwargs)
        return out, self.asked[before:]


@pytest.fixture(autouse=True)
def fresh_realm():
    legislation._state.update(codex=legislation.UNKNOWN, box=None, failing=False)
    localtime.use("UTC")
    yield
    legislation.load(None)


@pytest.fixture
def agora():
    """Agora with Bob as Finance treasurer and an open FY2026: the departments."""
    return setup_finance()


@pytest.fixture
def requests(monkeypatch):
    return Requests(monkeypatch)


# ── The facts ────────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_fact_schema_the_realm_bundles_is_one_the_library_reads():
    from ggg_facts import FACTS

    schema = FactSchema(FACTS)
    taxes = schema.actions["tax_policy_propose.taxes"]
    assert sorted(taxes.facts) == ["amount", "code", "kind", "rate", "subject"]  # TaxEntry.facts is a Value: no fact
    assert (taxes.facts["rate"].type, taxes.facts["rate"].optional, taxes.facts["rate"].range) == ("Long", True, (0, 10**10))
    assert taxes.also == ("tax_policy_draft.taxes",)
    assert schema.actions["tax_policy_draft.taxes"].shape() == taxes.shape()
    revenue = schema.actions["budget_propose"].facts["revenue"]  # derived by the realm, so a fact like any other
    assert (revenue.type, revenue.optional) == ("Long", False)
    assert schema.actions["procedure_define"].facts["decision_deadline"].type == "Period"
    assert "proposal_create" in schema.actions and "document_share" in schema.actions


def test_facts_are_cedar_values():
    assert forbids.cedar_value("String", {"Cast": None}, "x") == "Cast"  # a variant's tag
    assert forbids.cedar_value("Set<String>", ["b", "a", "b"], "x") == ["a", "b"]
    assert forbids.cedar_value("Period", {"count": 3, "unit": {"Month": None}}, "x") == {"count": 3, "unit": "Month"}
    for kind, value in (("Long", True), ("Long", 2**63), ("Long", "5"), ("Bool", 1), ("String", 5), ("Set<String>", "ab")):
        with pytest.raises(Fail):
            forbids.cedar_value(kind, value, "x")


def test_targets_name_the_actions_the_policies_forbid_and_refuse_a_reserved_operation():
    facts = {"tax_policy_propose.taxes": {}, "tax_policy_draft.taxes": {}, "codex_revert": {}}
    two = 'forbid (principal,\n        action in [Realm::Action::"tax_policy_propose.taxes",\n' \
          '                   Realm::Action::"tax_policy_draft.taxes"],\n        resource)\n  when { true };\n'
    assert forbids.targets(two, facts, RESERVED) == {"tax_policy_draft": ["tax_policy_draft.taxes"],
                                                     "tax_policy_propose": ["tax_policy_propose.taxes"]}
    reserved = 'forbid (principal, action == Realm::Action::"codex_revert", resource)\n  when { true };\n'
    with pytest.raises(Fail, match="no chapter may forbid codex_revert"):
        forbids.targets(reserved, facts, RESERVED)
    with pytest.raises(Fail, match="names no action"):
        forbids.targets("forbid (principal, action, resource)\n  when { true };\n", facts, RESERVED)
    with pytest.raises(Fail, match="has no facts"):
        forbids.targets(two.replace("tax_policy_draft.taxes", "nowhere"), facts, RESERVED)
    assert forbids.targets("", facts, RESERVED) == {}


# ── What the legislation never sees ──────────────────────────────────────────────────────────────────────────────


def secret_rule(operation, expression, each=""):
    return ("from realms.legislation import F, above, forbid\nclass Realm(above('realm')):\n"
            f"    rule = forbid('{operation}', {expression}, {f'each={each!r}, ' if each else ''}legislation='x')\n")


def test_a_forbid_cannot_read_what_an_operation_keeps_from_the_legislation(agora):
    """realm_join's `code`, invite_create's `code` and request_decide's `reply` are text, so Cedar could compare them:
    their `secret=` keeps them out of the facts, and a forbid that names one is refused when the codex is built."""
    for operation, fact in (("realm_join", "code"), ("invite_create", "code"), ("request_decide", "reply")):
        chapters = {("x/town", "realm"): secret_rule(operation, f"F.{fact}.is_some()")}
        plan = {"realm": "x/town", "layers": ["x/town"], "calendars": [],
                "chapters": [{"source": s, "domain": d, "sha256": store.checksum(t)} for (s, d), t in chapters.items()]}
        out = legislation.build(plan, {store.checksum(t): t for t in chapters.values()})
        assert out["errors"] and f"{operation} has no fact {fact}" in out["errors"][0], out["errors"]
    keys = {("x/town", "realm"): secret_rule("crypto_grant", "F.principal == 'x'", each="keys")}  # the list is secret
    out = legislation.build({"realm": "x/town", "layers": ["x/town"], "calendars": [], "chapters": [
        {"source": "x/town", "domain": "realm", "sha256": store.checksum(keys[("x/town", "realm")])}]},
        {store.checksum(keys[("x/town", "realm")]): keys[("x/town", "realm")]})
    assert out["errors"] and "crypto_grant.keys" in out["errors"][0], out["errors"]


def test_a_forbid_on_an_operation_with_a_secret_reads_the_facts_that_are_left(agora, requests):
    codex_of({("x/town", "realm"): secret_rule("realm_join", "F.nickname.is_some() & (F.nickname == 'root')")})
    assert auth.live["forbids"]["types"]["realm_join"].keys() == {"nickname", "language", "home_zone"}
    out, asked = requests.during("realm_join", "dana", code="the invite code", nickname="root")
    assert "forbidden by the legislation" in out["error"]
    assert asked[-1] == ("realm_join", {"nickname": "root"})  # the code never reached Cedar
    _, asked = requests.during("realm_join", "dana", code="the invite code", nickname="dana")
    assert asked[-1] == ("realm_join", {"nickname": "dana"})  # asked, and not refused by this rule


# ── Draft and propose ────────────────────────────────────────────────────────────────────────────────────────────


def test_an_entry_the_forbids_refuse_is_refused_at_draft_and_at_propose(agora):
    saved = ok("tax_policy_draft", "bob", **drafted(entry("ibi_urban", CAP + 1)))["policy"]  # before the legislation
    valencia()
    assert "forbidden by the legislation: taxes[0]" in denied("tax_policy_propose", "bob", policy=saved)
    assert TaxPolicy[saved].status == "draft"
    for refused in (entry("ibi_urban", CAP + 1), entry("ibi_urban"), entry("ibi_urban", 399_999), entry("ibi_rustic")):
        message = denied("tax_policy_draft", "bob", **drafted(entry("water", 5), refused))
        assert "forbidden by the legislation: taxes[1]" in message
    assert TaxPolicy.count() == 1  # nothing was saved


def test_an_entry_within_the_cap_is_saved_and_adopted(agora):
    valencia()
    policy = ok("tax_policy_draft", "bob", **drafted(entry("ibi_urban", CAP), entry("water", 5)))["policy"]
    assert ok("tax_policy_propose", "bob", policy=policy)["status"] == "adopted"


def test_the_cap_follows_the_realms_settings(agora):
    codex_of({(SPAIN, "tax"): SPAIN_TAX, (VALENCIA, "tax"): "from realms.legislation import above\n"
              "class Tax(above('tax')):\n    capital = True\n"})  # a capital without public transport
    assert "taxes[0]" in denied("tax_policy_draft", "bob", **drafted(entry("ibi_urban", 1_170_001)))
    ok("tax_policy_draft", "bob", **drafted(entry("ibi_urban", 1_170_000)))


def test_one_request_asks_each_element_on_the_action_of_its_list(agora, requests):
    valencia()
    out, asked = requests.during("tax_policy_draft", "bob", **drafted(entry("ibi_urban", CAP), entry("water", 5)))
    assert out["ok"]
    assert asked == [("call", None),
                     ("tax_policy_draft.taxes", {"code": "ibi_urban", "kind": "tax", "subject": "Land", "rate": CAP}),
                     ("tax_policy_draft.taxes", {"code": "water", "kind": "tax", "subject": "Land", "rate": 5})]
    policy = out["policy"]
    out, asked = requests.during("tax_policy_propose", "bob", policy=policy)
    assert [action for action, _ in asked] == ["call", "tax_policy_propose.taxes", "tax_policy_propose.taxes"]
    assert asked[1][1]["code"] == "ibi_urban" and asked[2][1]["rate"] == 5  # the saved policy's entries


def test_an_operation_no_forbid_names_is_asked_once_on_call_as_before(agora, requests):
    valencia()
    out, asked = requests.during("user_list", "bob")
    assert out["ok"] and asked == [("call", None)]


# ── In force once, until the codex goes ──────────────────────────────────────────────────────────────────────────


def test_the_forbids_are_parsed_once_when_the_codex_becomes_the_running_one(agora, requests):
    codex = valencia()
    assert len(requests.loads) == 1 and "ibi_urban_cap" in requests.loads[0]
    for rate in (CAP, CAP + 1, CAP):
        call("tax_policy_draft", "bob", **drafted(entry("ibi_urban", rate)))
    assert len(requests.loads) == 1  # decisions reuse what the engine holds
    legislation.warm(LayeredCodex[codex.id])  # the realm's start parses the set again, once
    assert len(requests.loads) == 2


def test_a_realm_without_a_layered_codex_asks_and_loads_as_it_did(agora, requests):
    assert auth.live["forbids"] is None and auth.live["policies"] == auth.GUARDRAILS + auth.POLICIES
    out, asked = requests.during("tax_policy_draft", "bob", **drafted(entry("ibi_urban", CAP + 1)))
    assert out["ok"] and asked == [("call", None)]
    assert requests.loads == []


def test_a_codex_whose_legislation_forbids_nothing_leaves_the_policies_alone(agora, requests):
    codex_of({("x/town", "procedure"): "from realms.legislation import above\nclass Procedure(above('procedure')):\n    pass\n"})
    assert auth.live["forbids"] is None and requests.loads == []


def test_a_codex_that_goes_takes_its_forbids_with_it(agora, requests):
    valencia()
    assert auth.live["forbids"] is not None
    legislation.load(None)
    assert auth.live["forbids"] is None
    assert ok("tax_policy_draft", "bob", **drafted(entry("ibi_urban", CAP + 1)))["policy"]


def test_a_codex_the_engine_refuses_does_not_load_and_the_running_one_stays(agora, monkeypatch):
    first = valencia()
    running = auth.live["forbids"]

    def refuses(schema, policies):
        raise Fail("the Cedar policies do not validate: no")

    monkeypatch.setattr(auth, "load", refuses)
    other = layered({(SPAIN, "tax"): SPAIN_TAX}, layers=[SPAIN])
    with pytest.raises(Fail, match="do not validate"):
        legislation.load(other)
    assert auth.live["forbids"] is running and legislation._state["codex"] == first.id


def test_a_sandbox_that_fails_generating_the_cedar_does_not_load_the_codex(agora, monkeypatch):
    first = valencia()
    running = auth.live["forbids"]
    call_in_box = sandbox.Resident.call

    def fails_on_cedar(self, fn, **kwargs):
        if fn == "cedar":
            raise RuntimeError("BudgetExceeded")
        return call_in_box(self, fn, **kwargs)

    monkeypatch.setattr(sandbox.Resident, "call", fails_on_cedar)
    with pytest.raises(Fail, match="the codex does not load: BudgetExceeded"):
        legislation.load(layered({(SPAIN, "tax"): SPAIN_TAX}, layers=[SPAIN]))
    assert auth.live["forbids"] is running and legislation._state["codex"] == first.id


def test_build_validates_the_cedar_without_replacing_the_running_policies(agora, monkeypatch):
    valencia()
    running = dict(auth.live)
    chapters = {(SPAIN, "tax"): SPAIN_TAX}
    sources = {store.checksum(t): t for t in chapters.values()}
    plan = {"realm": SPAIN, "layers": [SPAIN], "calendars": [],
            "chapters": [{"source": s, "domain": d, "sha256": store.checksum(t)} for (s, d), t in chapters.items()]}
    out = legislation.build(plan, sources)
    assert out["errors"] == [] and '@id("eu/es/tax/ibi_rate_required")' in out["cedar"]
    assert auth.live == running

    def refuses(schema, policies):
        raise Fail("the Cedar policies do not validate: no")

    monkeypatch.setattr(auth, "load", refuses)
    assert legislation.build(plan, sources)["errors"] == ["the Cedar policies do not validate: no"]


# ── Facts the operation derives, and the replay ──────────────────────────────────────────────────────────────────


def test_a_forbid_reads_the_facts_the_operation_derives(agora, requests):
    codex_of({("x/town", "treasury"): BUDGET_CAP})
    draft("ROADS", "capital", 600)
    draft("ROADS", "upkeep", 700)
    out, asked = requests.during("budget_propose", "bob", fund="ROADS", period="FY2026")
    assert "forbidden by the legislation: its arguments" in out["error"]
    assert asked[-1] == ("budget_propose", {"period": "FY2026", "expense": 1300, "revenue": 0})
    assert {b.status for b in rows(Budget)} == {"draft"}  # nothing was adopted
    draft("ROOT", "salaries", 500)
    assert ok("budget_propose", "bob", fund="ROOT", period="FY2026")["expense"] == 500
    assert requests.asked[-1] == ("budget_propose", {"period": "FY2026", "expense": 500, "revenue": 0})


def test_a_forbid_installed_between_filing_and_execution_refuses_the_execution_and_frees_the_lines(agora):
    lines = [draft("ROADS", "capital", 900), draft("ROADS", "upkeep", 600)]
    filed = file_roads_budget()
    draft("ROADS", "extra", 100)  # a draft after the filing: the next proposal's
    assert [Budget[b].status for b in lines] == ["proposed", "proposed"]
    codex_of({("x/town", "treasury"): BUDGET_CAP})  # the legislation changes while the vote is open
    vote(filed, "carla")
    p = Proposal[filed]
    assert p.status == "failed"
    assert "forbidden by the legislation: its arguments" in p.result["error"] and p.result["denied"]
    # the derived totals at the replay are those of the lines the proposal fixed (1500), not of the drafts (100)
    assert [(Budget[b].status, Budget[b].proposal) for b in lines] == [("draft", ""), ("draft", "")]


def test_a_replay_the_forbids_allow_runs_and_a_legacy_realms_replay_is_untouched(agora, requests):
    draft("ROADS", "capital", 300)
    filed = file_roads_budget()
    vote(filed, "carla")
    assert Proposal[filed].status == "executed"  # no layered codex: the replay asks Cedar nothing
    assert {action for action, _ in requests.asked} <= {"call"} and auth.live["forbids"] is None

    draft("ROADS", "capital", 200)
    codex_of({("x/town", "treasury"): BUDGET_CAP})
    second = file_roads_budget()
    before = len(requests.asked)
    vote(second, "carla")
    assert Proposal[second].status == "executed"
    replay = requests.asked[before:]
    assert ("budget_propose", {"period": "FY2026", "expense": 200, "revenue": 0}) in replay


PROPOSALS = """
from realms.legislation import F, above, forbid

class LocalRegime(above("local_regime")):
    no_blocked = forbid("proposal_create", F.title == "blocked", legislation="x")
    no_yes = forbid("proposal_vote", F.choice == "Yes", legislation="x")
"""


def test_a_reserved_operation_and_a_proposal_about_one_meet_no_forbid(agora, requests):
    """§10.4: no chapter's forbid runs on a reserved operation, nor on the proposal_create, the vote or the execution
    of a proposal whose action is one. The same forbids on proposal_create and proposal_vote hold for any other."""
    codex_of({("x/town", "local_regime"): PROPOSALS, (SPAIN, "tax"): SPAIN_TAX})
    assert set(auth.live["forbids"]["targeted"]) >= {"proposal_create", "proposal_vote"}
    assert "forbidden by the legislation" in denied("proposal_create", "alice", title="blocked")
    other = ok("proposal_create", "alice", title="a motion")["proposal"]
    assert "forbidden by the legislation" in denied("proposal_vote", "bob", proposal=other, choice="yes")

    revert = {"op": "codex_revert", "args": {}}
    blocked = ok("proposal_create", "alice", title="blocked", action=revert)["proposal"]  # filing: no forbid
    before = len(requests.asked)
    for voter in world.PEOPLE:  # voting and executing: none either
        call("proposal_vote", voter, proposal=blocked, choice="yes")
    assert Proposal[blocked].status in ("executed", "failed")
    assert {action for action, _ in requests.asked[before:]} == {"call"}
    out, asked = requests.during("codex_revert", "ctl")  # the operation itself
    assert [action for action, _ in asked] == ["call"] and out["kind"] != "forbidden"
