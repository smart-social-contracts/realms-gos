"""The generated forbids in force (docs/codices/layered-codex.md §9.4, realms-gos#489), in process: the Cedar a layered
codex's forbids become is parsed once when the codex becomes the running one and judges the operation's facts, each
element of a list a forbid ranges over, and the replay of an accepted proposal. tests/deployed/test_forbids.py runs the
same on the real wasm and measures what it costs."""

import pytest

import world
from core import auth, forbids, layered_codex as store, legislation, localtime, op, sandbox
from core.db import Fail
from ggg import Budget, FederalVote, LayeredCodex, PaymentAccount, PendingAction, Proposal, TaxPolicy
from kit import call, denied, ok, rows
from realms.legislation import RESERVED
from realms.legislation.cedar import FactSchema
from test_budget_proposal import file_roads_budget, infrastructure_decides, vote
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
    basis = schema.actions["document_share"].facts["basis"]  # §5.4's `F.basis.is_none()`
    assert (basis.type, basis.optional) == ("Record", True) and "facts" not in schema.actions["tax_policy_draft.taxes"].facts


def test_facts_are_cedar_values():
    assert forbids.cedar_value("String", {"Cast": None}, "x") == "Cast"  # a variant's tag
    assert forbids.cedar_value("Set<String>", ["b", "a", "b"], "x") == ["a", "b"]
    assert forbids.cedar_value("Period", {"count": 3, "unit": {"Month": None}}, "x") == {"count": 3, "unit": "Month"}
    assert forbids.cedar_value("Record", {"legal_basis": "consent"}, "x") == {}  # a forbid only tests that it is there
    for kind, value in (("Long", True), ("Long", 2**63), ("Long", "5"), ("Bool", 1), ("String", 5), ("Set<String>", "ab"), ("Record", "x")):
        with pytest.raises(Fail):
            forbids.cedar_value(kind, value, "x")


OPERATIONS = {"tax_policy_propose", "tax_policy_draft", "codex_revert", "a"}


def test_targets_name_the_actions_the_policies_forbid_and_refuse_a_reserved_operation():
    facts = {"tax_policy_propose.taxes": {}, "tax_policy_draft.taxes": {}, "codex_revert": {}}
    two = 'forbid (principal,\n        action in [Realm::Action::"tax_policy_propose.taxes",\n' \
          '                   Realm::Action::"tax_policy_draft.taxes"],\n        resource)\n  when { true };\n'
    assert forbids.targets(two, facts, OPERATIONS, RESERVED) == {"tax_policy_draft": ["tax_policy_draft.taxes"],
                                                                 "tax_policy_propose": ["tax_policy_propose.taxes"]}
    reserved = 'forbid (principal, action == Realm::Action::"codex_revert", resource)\n  when { true };\n'
    with pytest.raises(Fail, match="no chapter may forbid codex_revert"):
        forbids.targets(reserved, facts, OPERATIONS, RESERVED)
    with pytest.raises(Fail, match="not scoped as the generator writes one"):  # it would forbid every operation
        forbids.targets("forbid (principal, action, resource)\n  when { true };\n", facts, OPERATIONS, RESERVED)
    with pytest.raises(Fail, match="has no facts"):
        forbids.targets(two.replace("tax_policy_draft.taxes", "tax_policy_draft.nowhere"), facts, OPERATIONS, RESERVED)
    assert forbids.targets("", facts, OPERATIONS, RESERVED) == {}


def test_a_forbid_reaches_no_further_than_the_operations_it_names():
    """The realm's own registry decides what an action is, not the sandbox: `call` is the action of every permission
    decision, a forbid of one person is not what the generator writes, and a list of one is not either."""
    facts = {"call": {}, "a": {}, "a.b": {}, "x": {}}
    scope = 'forbid (%s, resource)\n  when { true };\n'
    for head, why in (
        ('principal, action == Realm::Action::"call"', "no operation of this realm"),
        ('principal, action == Realm::Action::"x"', "no operation of this realm"),
        ('principal, action == Realm::Action::"a.b.c"', "no operation of this realm"),
        ('principal, action == Realm::Action::"a."', "no operation of this realm"),
        ('principal == Realm::User::"alice", action == Realm::Action::"a"', "not scoped as the generator writes one"),
        ('principal, action in [Realm::Action::"a"]', "not scoped as the generator writes one"),
        ('principal, action == Realm::Action::"a" || action == Realm::Action::"a.b"', "not scoped"),
        ('principal, action == Realm::Action::"a", resource == Realm::Realm::"1"', "not scoped"),
    ):
        with pytest.raises(Fail, match=why):
            forbids.targets(scope % head, facts, OPERATIONS, RESERVED)
    assert forbids.targets(scope % 'principal, action == Realm::Action::"a.b"', facts, OPERATIONS, RESERVED) == \
        {"a": ["a.b"]}


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
    assert "forbidden by the legislation: taxes[0]: refused by eu/es/tax/ibi_urban_cap" in denied(
        "tax_policy_propose", "bob", policy=saved)
    assert TaxPolicy[saved].status == "draft"
    # a proposal that carries the adoption is refused when it is filed, before any vote, and again if it were not
    files = denied("proposal_create", "bob", title="adopt", action={"op": "tax_policy_propose", "args": {"policy": saved}})
    assert "tax_policy_propose is forbidden by the legislation: taxes[0]: refused by eu/es/tax/ibi_urban_cap" in files
    assert Proposal.count() == 0
    for refused, by in ((entry("ibi_urban", CAP + 1), "eu/es/tax/ibi_urban_cap (TRLRHL Art. 72.3)"),
                        (entry("ibi_urban"), "eu/es/tax/ibi_rate_required (TRLRHL Art. 72)"),
                        (entry("ibi_urban", 399_999), "eu/es/tax/ibi_urban_range (TRLRHL Art. 72.1, 72.3)"),
                        (entry("ibi_rustic"), "eu/es/tax/ibi_rate_required (TRLRHL Art. 72)")):
        message = denied("tax_policy_draft", "bob", **drafted(entry("water", 5), refused))
        assert f"forbidden by the legislation: taxes[1]: refused by {by}" in message  # the forbid that fired, no other
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
    legislation.warm(LayeredCodex[codex.id])  # a reload of the same forbids finds them parsed already
    assert len(requests.loads) == 1
    codex_of({(SPAIN, "tax"): SPAIN_TAX}, previous=codex.id)  # other forbids are parsed, once
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
    assert "forbidden by the legislation: its arguments: refused by x/town/treasury/expense_cap (a budget spends at most 1000)" \
        in out["error"]
    assert asked[-1] == ("budget_propose", {"period": "FY2026", "expense": 1300, "revenue": 0})
    assert {b.status for b in rows(Budget)} == {"draft"}  # nothing was adopted
    draft("ROOT", "salaries", 500)
    assert ok("budget_propose", "bob", fund="ROOT", period="FY2026")["expense"] == 500
    assert requests.asked[-1] == ("budget_propose", {"period": "FY2026", "expense": 500, "revenue": 0})


def test_a_proposal_that_carries_a_call_the_forbids_refuse_is_refused_at_filing_and_fixes_nothing(agora):
    """proposal_create files the action it carries as a governed call files its own: the forbids are asked first, so a
    proposal that could only fail is never put to the vote, and the lines it would fix stay drafts."""
    codex_of({("x/town", "treasury"): BUDGET_CAP})
    lines = [draft("ROADS", "capital", 900), draft("ROADS", "upkeep", 600)]
    message = denied("proposal_create", "carla", title="Roads budget", department="Infrastructure",
                     action={"op": "budget_propose", "args": {"fund": "ROADS", "period": "FY2026"}})
    assert "budget_propose is forbidden by the legislation: its arguments: refused by x/town/treasury/expense_cap" in message
    assert Proposal.count() == 0 and [(Budget[b].status, Budget[b].proposal) for b in lines] == [("draft", ""), ("draft", "")]
    cheaper = draft("ROOT", "salaries", 500)  # another fund's lines, within the cap, are filed
    filed = ok("proposal_create", "carla", title="Salaries", action={"op": "budget_propose",
                                                                       "args": {"fund": "ROOT", "period": "FY2026"}})
    assert Budget[cheaper].status == "proposed" and Budget[cheaper].proposal == filed["proposal"]


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


# ── Who the call acts for, and the proposal a governed call files ────────────────────────────────────────────────

DELEGATES = """
from realms.legislation import F, above, forbid

class LocalRegime(above("local_regime")):
    no_delegate_votes = forbid("proposal_vote", F.on_behalf_of.is_some(), legislation="members vote themselves")
"""
FILINGS = """
from realms.legislation import F, above, forbid

class LocalRegime(above("local_regime")):
    no_infrastructure_filings = forbid("proposal_create", F.department.is_some() & (F.department == "Infrastructure"),
                                       legislation="Infrastructure files nothing")
"""


def test_a_forbid_reads_the_principal_a_call_acts_for(agora):
    """`on_behalf_of` is one of the operation's arguments as GGG defines them: a rule can refuse a delegate's vote."""
    codex_of({("x/town", "local_regime"): DELEGATES})
    motion = ok("proposal_create", "alice", title="a motion")["proposal"]
    ok("delegation_grant", "alice", delegate="claude-conn", operations=["proposal.vote"], expires_in=86400)
    message = denied("proposal_vote", "claude-conn", proposal=motion, choice="yes", on_behalf_of="alice")
    assert "proposal_vote is forbidden by the legislation: its arguments: refused by " \
           "x/town/local_regime/no_delegate_votes (members vote themselves)" in message
    ok("proposal_vote", "alice", proposal=motion, choice="yes")  # her own vote is her own


def test_a_governed_call_that_becomes_a_proposal_meets_the_forbids_of_proposal_create(agora):
    lines = [draft("ROADS", "capital", 300)]
    infrastructure_decides(agora)  # a vote is needed: bob's call would file a proposal of Infrastructure's
    codex_of({("x/town", "local_regime"): FILINGS})
    message = denied("budget_propose", "bob", fund="ROADS", period="FY2026")
    assert "proposal_create is forbidden by the legislation: its arguments: refused by " \
           "x/town/local_regime/no_infrastructure_filings" in message
    assert Proposal.count() == 0 and Budget[lines[0]].status == "draft"  # nothing was filed, nothing fixed
    root = draft("ROOT", "salaries", 500)  # the realm's own budget is decided directly: no proposal, no forbid
    assert "proposal" not in ok("budget_propose", "bob", fund="ROOT", period="FY2026")
    assert Budget[root].status == "adopted"


def test_a_federal_vote_that_carries_a_call_the_forbids_refuse_is_refused_when_proposed(agora):
    codex_of({("x/town", "local_regime"): """
from realms.legislation import F, above, forbid

class LocalRegime(above("local_regime")):
    low_threshold = forbid("governance_update", F.voting_threshold.is_some() & (F.voting_threshold > 55000000),
                           legislation="at most 55 %")
"""})
    action = {"op": "governance_update", "args": {"voting_threshold": 60_000_000}}
    message = denied("federal_vote_propose", "bob", title="Adopt", action=action)
    assert "governance_update is forbidden by the legislation: its arguments: refused by x/town/local_regime/low_threshold" \
        in message
    assert FederalVote.count() == 0
    action["args"]["voting_threshold"] = 50_000_000
    assert ok("federal_vote_propose", "bob", title="Adopt", action=action)["vote"]


# ── What the realm will load, and what it can say ────────────────────────────────────────────────────────────────

FORBID = 'forbid (principal, action == Realm::Action::"a", resource)\n  when { true };\n'


def test_the_realm_loads_forbids_and_nothing_else_from_the_sandbox():
    facts = {"a": {}}

    def targets(text):
        return forbids.targets(text, facts, OPERATIONS, RESERVED)

    assert targets("@id(\"x\")\n" + FORBID) == {"a": ["a"]}
    # what is inside a string is not code, whatever it says; an escaped quote does not close it
    quoted = '@legislation("a permit (principal, action, resource); \\" forbid (// /* ")\n@id("x \\\\")\n' + FORBID
    assert targets(quoted) == {"a": ["a"]}
    assert targets('@legislation("Ley Orgánica 3/2018 \\u{1f600} \\n")\n' + FORBID) == {"a": ["a"]}  # text is free
    smuggles = (
        ("permit (principal, action, resource);\n", "permits"),
        ("@id(\"p\")\npermit (principal, action == Realm::Action::\"a\", resource) when { context.public };\n", "permits"),
        ("forbid(principal, action, resource);\n", "not a forbid written as"),  # a head the scan of heads would miss
        ("//\nforbid (principal, action, resource) when { true }\n;", "never writes"),
        ("\nforbid (principal, action, resource) when { true }\n;\n", "not scoped"),
        # a comment ends at `\n` and at `\r`, and a quote in one opens a string for any reader that skips comments
        ('// "\rpermit (principal, action, resource);\r// "\n', "control character|never writes"),  # the review's
        ('// "\npermit (principal, action, resource);\n// "\n', "control character|never writes"),
        ("\rpermit (principal, action, resource);\r", "never writes"),
        ("\r\npermit (principal, action, resource);\r\n", "never writes"),
        ("/* permit (principal, action, resource); */", "never writes"),
        ('/* " */ permit (principal, action, resource); /* " */', "never writes"),
        ("// permit (principal, action, resource);\n", "never writes"),
        ("#permit (principal, action, resource);\n", "never writes"),
        ("\u2028permit (principal, action, resource);\n", "never writes"),
        ("\u0085permit (principal, action, resource);\n", "never writes"),
        ("\\\"permit (principal, action, resource);\n", "never writes|does not close"),
        ("\tpermit (principal, action, resource);\n", "never writes"),
        ("\x00permit (principal, action, resource);\n", "never writes"),
        ('@id("u\n")\n', "control character"),
        ('@id("u\r")\n', "control character"),
        ('@id("u\x7f")\n', "control character"),
        ('@id("u)\n', "does not close"),
    )
    for smuggled, why in smuggles:
        with pytest.raises(Fail, match=why):
            targets(FORBID + smuggled)
        with pytest.raises(Fail, match=why):
            targets(smuggled + FORBID)  # first or last, it makes no difference


def test_the_comment_that_hides_a_permit_from_a_scan_is_real_to_the_engine():
    """Cedar ends a `//` comment at `\\r` as at `\\n`, so a quote in one that is read as the start of a string hides the
    `permit` after it: the engine grants what no scan that follows only `\\n` sees. The realm refuses such text."""
    import cedarpy

    hidden = FORBID + '// "\rpermit (principal, action, resource);\r// "\n'
    request = {"principal": 'Realm::User::"u"', "action": 'Realm::Action::"other"', "resource": 'Realm::Realm::"1"',
               "context": {}}
    assert cedarpy.is_authorized(request, hidden, []).decision == cedarpy.Decision.Allow  # the permit works
    assert cedarpy.is_authorized(request, FORBID, []).decision == cedarpy.Decision.Deny
    with pytest.raises(Fail):
        forbids.targets(hidden, {"a": {}}, OPERATIONS, RESERVED)


def test_a_codex_whose_generated_cedar_smuggles_a_permit_is_not_loaded_nor_built(agora, monkeypatch):
    first = valencia()
    running = dict(auth.live)
    call_in_box = sandbox.Resident.call

    def smuggles(self, fn, **kwargs):
        reply = call_in_box(self, fn, **kwargs)
        if fn == "cedar" and reply["cedar"]:
            reply["cedar"] += "permit (principal, action, resource);\n"
        return reply

    monkeypatch.setattr(sandbox.Resident, "call", smuggles)
    other = layered({(SPAIN, "tax"): SPAIN_TAX}, layers=[SPAIN])
    with pytest.raises(Fail, match="permits"):
        legislation.load(other)
    chapters = {(SPAIN, "tax"): SPAIN_TAX}
    plan = {"realm": SPAIN, "layers": [SPAIN], "calendars": [],
            "chapters": [{"source": s, "domain": d, "sha256": store.checksum(t)} for (s, d), t in chapters.items()]}
    built = legislation.build(plan, {store.checksum(t): t for t in chapters.values()})
    assert any("permits" in e for e in built["errors"])
    assert auth.live == running and legislation._state["codex"] == first.id


def test_the_reason_is_a_courtesy_and_the_sandbox_is_not_trusted_with_it(agora, monkeypatch):
    valencia()
    answers = [RuntimeError("boom"), Fail("not loaded"), None, "x", {"id": "a"}, [{"id": 1, "legislation": "c"}],
               [{"id": "a", "legislation": 3}], ["a"], [{"legislation": "c"}]]
    for answer in answers:
        def ask(fn, **kwargs):
            if isinstance(answer, Exception):
                raise answer
            return answer

        with monkeypatch.context() as m:
            m.setattr(legislation, "_call", ask)
            assert legislation.firing("tax_policy_draft.taxes", {}) == []
            message = denied("tax_policy_draft", "bob", **drafted(entry("ibi_urban", CAP + 1)))
        assert message.endswith("tax_policy_draft is forbidden by the legislation: taxes[0]"), (answer, message)
    many = [{"id": "x" * 500, "legislation": None}] * 20
    with monkeypatch.context() as m:
        m.setattr(legislation, "_call", lambda fn, **kwargs: many)
        found = legislation.firing("tax_policy_draft.taxes", {})
    assert len(found) == 8 and found[0] == {"id": "x" * 200, "legislation": ""}


# ── A codex that does not load, and the lists the forbids judge ──────────────────────────────────────────────────

READS = """
from realms.legislation import F, above, forbid

class Realm(above("realm")):
    no_big_pages = forbid("user_list", F.limit.is_some() & (F.limit > 100), legislation="pages of at most 100")
"""


def test_while_a_codex_does_not_load_a_read_its_forbids_would_judge_is_refused(agora, monkeypatch):
    row = codex_of({(SPAIN, "tax"): SPAIN_TAX, ("x/town", "realm"): READS})
    assert row.forbids == ["tax_policy_draft", "tax_policy_propose", "user_list"]  # kept for the day it does not load
    assert "forbidden by the legislation" in denied("user_list", "dana", limit=101)
    ok("user_list", "dana", limit=100)
    # an upgrade empties the heap, and the codex no longer loads
    legislation._state.update(codex=legislation.UNKNOWN, box=None, failing=False)
    auth.use_forbids(None)

    def boom(plan, texts):
        raise Fail("the library no longer compiles")

    with monkeypatch.context() as m:
        m.setattr(legislation, "_spawn", boom)
        legislation.warm()
        assert "which does not load" in denied("user_list", "dana")  # a read, with no argument that would be refused
        assert "which does not load" in denied("tax_policy_draft", "bob", **drafted(entry("water", 5)))
        ok("codex_status", "ctl")  # what no forbid names still reads
        assert "does not load" not in call("codex_revert", "ctl").get("error", "")  # and a revert is how it ends
    legislation.warm()  # the library compiles again
    ok("user_list", "dana", limit=100)
    assert "forbidden by the legislation" in denied("user_list", "dana", limit=101)


def test_a_list_of_more_than_500_elements_is_refused_where_it_is_given(agora, monkeypatch):
    codex_of({(SPAIN, "tax"): SPAIN_TAX})
    many = [entry(f"tax{i}", 5) for i in range(501)]
    refused = call("tax_policy_draft", "bob", **drafted(*many))
    assert refused["kind"] == "invalid_argument" and "has 501 elements" in refused["error"]
    assert TaxPolicy.count() == 0
    saved = TaxPolicy(id="TAX-POLICY-9", status="draft", document={"symbol": "REALM"}, taxes=many).id  # drafted before
    refused = call("tax_policy_propose", "bob", policy=saved)
    assert refused["kind"] == "invalid_argument" and "has 501 elements" in refused["error"]
    assert forbids.MAX_ELEMENTS == 500
    monkeypatch.setattr(forbids, "MAX_ELEMENTS", 3)  # the limit itself, without 500 requests
    assert "has 4 elements" in denied("tax_policy_draft", "bob", **drafted(*many[:4]))
    assert ok("tax_policy_draft", "bob", **drafted(*many[:3]))["policy"]


NO_ACCOUNTS = """
from realms.legislation import F, above, forbid

class LocalRegime(above("local_regime")):
    no_accounts = forbid("payment_account_add", F.symbol.is_some() & (F.symbol == "REALM"),
                              legislation="no account in REALM")
"""


def test_a_forbid_refuses_a_pending_action_when_it_is_prepared_and_when_it_is_approved(agora):
    from test_pending_actions import ACCOUNT, approved, file, grant

    grant("payout")
    waiting = file()  # prepared before the forbid exists
    codex_of({("x/town", "local_regime"): NO_ACCOUNTS})
    message = denied("payment_account_add", "claude-conn", on_behalf_of="alice", **ACCOUNT)
    assert "payment_account_add is forbidden by the legislation: its arguments: refused by " \
           "x/town/local_regime/no_accounts (no account in REALM)" in message
    assert PendingAction.count() == 1  # a forbidden action is never prepared
    done = approved(waiting)  # the approval runs the call as the member, and the forbid holds then too
    assert done["status"] == "failed" and "refused by x/town/local_regime/no_accounts" in done["result"]["error"]
    assert PaymentAccount.count() == 0


def test_an_approved_call_states_the_member_it_acts_for(agora, monkeypatch):
    """The call a delegate prepares and the member approves is the delegate's: its facts name the member as
    `on_behalf_of` when it is prepared and when it is approved, and the member's own call names no one."""
    from test_pending_actions import approved, grant

    monkeypatch.setitem(op.ops()["proposal_vote"], "tier", "confirm")  # no operation of this release has a fact to read
    motion = ok("proposal_create", "alice", title="a motion")["proposal"]
    grant("vote")
    waiting = ok("proposal_vote", "claude-conn", on_behalf_of="alice", proposal=motion, choice="yes")["pending"]
    codex_of({("x/town", "local_regime"): DELEGATES})
    message = denied("proposal_vote", "claude-conn", on_behalf_of="alice", proposal=motion, choice="yes")
    assert "refused by x/town/local_regime/no_delegate_votes" in message and PendingAction.count() == 1
    done = approved(waiting)
    assert done["status"] == "failed" and "refused by x/town/local_regime/no_delegate_votes" in done["result"]["error"]
    ok("proposal_vote", "alice", proposal=motion, choice="yes")  # her own vote is her own


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
    before = len(requests.asked)
    blocked = ok("proposal_create", "alice", title="blocked", action=revert)["proposal"]  # filing: no forbid
    assert {action for action, _ in requests.asked[before:]} == {"call"}
    for voter in world.PEOPLE:  # voting and executing: none either
        call("proposal_vote", voter, proposal=blocked, choice="yes")
    assert Proposal[blocked].status in ("executed", "failed")
    assert {action for action, _ in requests.asked[before:]} == {"call"}
    out, asked = requests.during("codex_revert", "ctl")  # the operation itself
    assert [action for action, _ in asked] == ["call"] and out["kind"] != "forbidden"
