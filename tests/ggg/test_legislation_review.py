"""What the review of the runtime (realms-gos#579) found and the runtime now refuses, each probe kept as a test: the
entities and fields a chapter may read, the secrets kept out of what crosses, a governed effect filed as a proposal,
effects on proposals and federal votes about a reserved operation, a codex that does not load for any reason, reads
metered per call, the guards on a governed call's filing, default arguments, chapter instances handed across domains,
effects stopping at the first failure, and the audit order."""

import pytest

from api import codex as codex_api
from core import auth, legislation, op
from core.db import Fail
from ggg import Department, Event, FederalVote, Human, Identity, Land, LayeredCodex, Notification, Proposal
from kit import call, ok, rows
import world
from test_legislation_runtime import LOG, TOWN, TOWN_REALM, activate, layered, town  # noqa: F401
from test_legislation_runtime import fresh_realm  # noqa: F401  the autouse fixture


@op.op("treasury.manage", secret=("code",))
def t_poke(c, args):
    """A plain write any chapter may guard or hook; its ``code`` is a secret, in and out."""
    return {"done": args.get("what", ""), "code": "SHOWN-ONCE"}


def register(body):
    return "from realms.legislation import above\n\nclass Register(above(\"register\")):\n" + body


def notices(title):
    return [n.body for n in Notification.load_some(1, 200) if n.title == title]


def failures():
    return ok("codex_status", query=True)["failures"]


# ── 1. The view: an allowlist of entities and fields ────────────────────────────────────────────────────────────

SNOOP = '''
from ggg import Human, Identity, Member, Notification
from realms.legislation import above

class Register(above("register")):
    def check_t_poke(self, args):
        what = args.what
        if what == "birthday":
            return self.deny(Human.get("h-alice").date_of_birth)
        if what == "identity ref":
            return self.deny(Identity.get("i-1").ref)
        if what == "notification body":
            return self.deny(Notification.get("1").body)
        if what == "find secret":
            return self.deny(str(len(Member.find("private_data", "x"))))
        if what == "allowed":
            human = Human.get("h-alice")
            return self.deny(f"{human.user}:{len(Member.find('status', 'active'))}:{Identity.get('i-1').kind}")
'''


def test_the_view_shows_the_allowed_fields_of_the_allowed_entities_and_nothing_else():
    Human(id="h-alice", user="alice", date_of_birth="1980-02-03", h3_index="8928308280fffff")
    Identity(id="i-1", human=Human["h-alice"], kind="passport", issuer="ES", ref="vault:1", status="verified")
    Notification(id="1", recipient="User:alice", title="private", body="for alice only")
    activate(layered({(TOWN, "register"): SNOOP}, layers=[TOWN]))
    assert call("t_poke", what="allowed")["error"] == "alice:7:passport"
    assert call("t_poke", what="birthday")["error"].endswith("AttributeError: Human has no field 'date_of_birth'")
    assert call("t_poke", what="identity ref")["error"].endswith("AttributeError: Identity has no field 'ref'")
    assert call("t_poke", what="notification body")["error"].endswith("AttributeError: Notification has no field 'body'")
    assert call("t_poke", what="find secret")["error"].endswith("rpc failed: a chapter does not read Member.private_data")
    for entity in ("KeyEnvelope", "Vote", "Delegation", "DelegationRequest", "PaymentAccount", "Event", "Invite", "Codex",
                   "ChapterSource", "InstallJob", "Extension", "Bid", "Forward", "FederationMessage"):
        assert entity not in legislation.VIEWS
    for entity, field in (("Human", "date_of_birth"), ("Human", "h3_index"), ("Identity", "ref"), ("Document", "ref"),
                          ("Document", "hash"), ("Notification", "body"), ("Notification", "read_by"), ("User", "private_data"),
                          ("Realm", "manifest_data"), ("Proposal", "votes"), ("Land", "registered_by")):
        assert field not in legislation.VIEWS[entity]


@pytest.mark.parametrize("entity", ["KeyEnvelope", "Vote", "Delegation", "PaymentAccount"])
def test_an_entity_outside_the_view_cannot_be_imported(entity):
    text = f"from ggg import {entity}\nfrom realms.legislation import above\nclass Register(above('register')):\n    pass\n"
    out = legislation.build({"realm": TOWN, "layers": [TOWN], "calendars": [],
                             "chapters": [{"source": TOWN, "domain": "register", "sha256": "sha256:x"}]}, {"sha256:x": text})
    assert out["errors"] == [f"x/town/register.py:1: ImportError: cannot import name '{entity}' from 'ggg' (unknown location)"]


# ── 2. Secrets never cross ──────────────────────────────────────────────────────────────────────────────────────

INVITES = register('''    def on_invite_create(self, event):
        self.ggg.notification_send(recipient="User:mallory", title="invite", body=str(event.result["invite"].get("code")))

    def check_t_poke(self, args):
        if args.what == "secret arg":
            return self.deny(str(args.code))

    def on_t_poke(self, event):
        if event.args.get("what") == "secret result":
            self.ggg.notification_send(recipient="User:mallory", title="result", body=str(event.result.get("code")))
''')


def test_secret_fields_are_stripped_from_what_crosses_into_a_chapter():
    activate(layered({(TOWN, "register"): INVITES}, layers=[TOWN]))
    got = ok("invite_create", "dana")
    assert got["invite"]["code"]  # shown to the caller, once
    assert notices("invite") == ["None"]
    assert call("t_poke", what="secret arg", code="X7K-29Q")["error"] == "None"
    ok("t_poke", what="secret result")
    assert notices("result") == ["None"]


# ── 3. Effects: a governed operation becomes a proposal the realm files; nothing about a reserved operation ──────

POWER = register('''    def on_t_poke(self, event):
        what = event.args.get("what")
        if what == "admin":
            self.ggg.profile_create(profile="root2", allowed_to=["all"])
            self.ggg.role_assign(user="mallory", profile="root2")
        if what is not None and what.startswith("stop:"):
            self.ggg.proposal_stop(proposal=what[5:])
        if what is not None and what.startswith("withdraw:"):
            self.ggg.proposal_withdraw(proposal=what[9:])
        if what == "two":
            self.ggg.t_poke(what="first", code="")
            self.ggg.notification_send(recipient="User:nobody", title="fails", body="")
            self.ggg.t_poke(what="never")
''')


def test_a_governed_effect_is_filed_as_a_proposal_by_the_realm_and_applied_only_by_the_vote():
    world.policy(Department["Administration"], 3, 5)
    activate(layered({(TOWN, "register"): POWER}, layers=[TOWN]))
    ok("t_poke", what="admin")
    assert "all" not in auth.permissions("mallory")
    filed = [p for p in rows(Proposal) if p.action and p.action["op"] in ("profile_create", "role_assign")]
    expected = [("profile_create", "realm", "voting"), ("role_assign", "realm", "voting")]
    assert [(p.action["op"], p.proposer, p.status) for p in filed] == expected
    assert failures() == {}
    for p in filed:
        for voter in world.PEOPLE:
            call("proposal_vote", voter, proposal=p.id, choice="yes")
    assert "all" in auth.permissions("mallory")  # the vote, not the chapter, gave it


def test_an_effect_cannot_stop_or_withdraw_a_proposal_about_a_reserved_operation():
    first = town()
    first.status = "inactive"
    second = activate(layered({(TOWN, "register"): POWER}, layers=[TOWN], previous=first.id))
    pid = ok("proposal_create", "alice", title="revert", effective_at=1790000000 + 30 * 86400,
             action={"op": "codex_revert", "args": {}})["proposal"]
    for voter in world.PEOPLE:
        call("proposal_vote", voter, proposal=pid, choice="yes")
    assert Proposal[pid].status == "execution_scheduled"
    ok("t_poke", what="stop:" + pid)
    assert failures()["x/town/register.py"]["on_t_poke"]["error"] == f"proposal_stop: proposal_stop is about a reserved operation: no chapter may call it"
    ok("t_poke", what="withdraw:" + pid)
    assert failures()["x/town/register.py"]["on_t_poke"]["error"].startswith("proposal_withdraw: proposal_withdraw is about a reserved")
    assert Proposal[pid].status == "execution_scheduled" and LayeredCodex[second.id].status == "active"


def test_effects_stop_at_the_first_that_fails():
    activate(layered({(TOWN, "register"): POWER}, layers=[TOWN]))
    before = Event.max_id()
    ok("t_poke", what="two")
    applied = [e.args.get("what") for e in rows(Event) if e.id and int(e.id) > before and e.op == "t_poke"]
    assert applied == ["two", "first"]  # the operation, then the first effect; "never" was not applied
    assert failures()["x/town/register.py"]["on_t_poke"]["error"].startswith("notification_send: unknown User")


def test_the_operations_own_audit_event_precedes_its_effects():
    activate(layered({(TOWN, "register"): POWER}, layers=[TOWN]))
    before = Event.max_id()
    ok("t_poke", what="two")
    events = [(e.op, e.origin, e.args.get("what")) for e in rows(Event) if int(e.id) > before]
    assert events[0] == ("t_poke", "user", "two") and events[1] == ("t_poke", "codex", "first")


# ── 4. Federal votes about a reserved operation ─────────────────────────────────────────────────────────────────

DENY_VOTES = '''
from realms.legislation import above

class Procedure(above("procedure")):
    def check_proposal_create(self, args):
        return self.deny("nothing may be proposed")

    def check_proposal_vote(self, args):
        return self.deny("nothing may be voted")

    def check_federal_vote_propose(self, args):
        return self.deny("no federal vote")
'''


def test_a_federal_vote_whose_action_is_reserved_and_its_leg_run_no_guard():
    activate(layered({(TOWN, "procedure"): DENY_VOTES}, layers=[TOWN]))
    assert call("federal_vote_propose", title="x", action={"op": "profile_create", "args": {}})["error"] == "no federal vote"
    vote = ok("federal_vote_propose", title="revert", action={"op": "codex_revert", "args": {}})["vote"]
    leg = next(leg for leg in FederalVote[vote].legs)
    assert Proposal[leg.proposal_id].action is None  # a leg carries no action of its own
    assert ok("proposal_vote", "alice", proposal=leg.proposal_id, choice="yes")["vote"]
    assert call("proposal_create", "alice", title="other")["error"] == "nothing may be proposed"


# ── 5. A codex that does not load, for any reason ────────────────────────────────────────────────────────────────

DEEP = "from realms.legislation import above\nclass Procedure(above('procedure')):\n    x = " + "-" * 50000 + "1\n"
LOAD_READS = "from ggg import Member\nfrom realms.legislation import above\nclass Procedure(above('procedure')):\n    n = len(Member.each())\n"


def test_whatever_the_sandbox_raises_at_load_is_a_fail_and_warm_records_it():
    first = town()
    broken = layered({(TOWN, "procedure"): DEEP}, layers=[TOWN], previous=first.id)
    with pytest.raises(Fail, match="the codex does not load: sandboxed call raised: (RecursionError|MemoryError)"):
        legislation.load(broken)
    assert legislation._state["codex"] == first.id and legislation._state["box"] is not None
    first.status, broken.status = "inactive", "active"
    legislation.warm()  # post_upgrade: no trap
    assert "Error" in LayeredCodex[broken.id].failures["codex"]["load"]["error"]  # a RecursionError or a MemoryError
    assert call("t_poke", what="x")["error"].startswith("legislation error: the codex sha256:")


def test_the_view_answers_no_read_while_the_codex_loads():
    out = legislation.build({"realm": TOWN, "layers": [TOWN], "calendars": [],
                             "chapters": [{"source": TOWN, "domain": "procedure", "sha256": "sha256:x"}]}, {"sha256:x": LOAD_READS})
    assert out["errors"] == ["x/town/procedure.py:4: GGG's entities are read when a method runs, not while the codex loads"]


# ── 6. Reads are metered per call ───────────────────────────────────────────────────────────────────────────────

READ_LOOP = '''
from ggg import Member
from realms.legislation import above

class Register(above("register")):
    def on_t_poke(self, event):
        if event.args.get("what") == "read loop":
            n = 0
            while n < 5000:
                Member.get("nobody")
                n += 1
'''


def test_a_hook_that_reads_past_the_budget_fails_alone():
    activate(layered({(TOWN, "register"): READ_LOOP}, layers=[TOWN]))
    assert ok("t_poke", what="read loop")["done"] == "read loop"
    error = failures()["x/town/register.py"]["on_t_poke"]["error"]
    assert error == f"the chapters read for more than {legislation.READS:,} instructions in one call: the call stops"
    ok("t_poke", what="fine")
    assert failures() == {}


# ── 7. The filing of a governed call is a proposal_create to the legislation ────────────────────────────────────

FILING = '''
from realms.legislation import above

class Procedure(above("procedure")):
    def check_proposal_create(self, args):
        if args.action is not None and args.action.op == "profile_create":
            return self.deny("no new profiles by proposal")

    def on_proposal_create(self, event):
        self.ggg.notification_send(recipient="User:alice", title="filed", body=str(event.result.get("requires_vote")))
'''


def test_a_governed_call_turned_proposal_runs_proposal_creates_guards_and_hooks():
    world.policy(Department["Administration"], 3, 5)
    activate(layered({(TOWN, "procedure"): FILING}, layers=[TOWN]))
    assert call("profile_create", profile="p", allowed_to=[])["error"] == "no new profiles by proposal"
    assert [p for p in rows(Proposal) if p.action and p.action["op"] == "profile_create"] == []
    filed = ok("user_set_status", user="bob", status="suspended")
    assert filed["requires_vote"] and notices("filed") == ["True"]


# ── 8. No default arguments ─────────────────────────────────────────────────────────────────────────────────────

MEMORY = register('''    def check_t_poke(self, args, seen=[]):
        seen.append(args.what)
        return None
''')
LAMBDA_DEFAULT = register('''    def check_t_poke(self, args):
        f = lambda acc=[]: acc
        return None
''')


@pytest.mark.parametrize("text, line", [(MEMORY, 4), (LAMBDA_DEFAULT, 5),
                                        (register("    def check_t_poke(self, args, n=15):\n        pass\n"), 4)])
def test_default_arguments_are_refused_at_load(text, line):
    out = legislation.build({"realm": TOWN, "layers": [TOWN], "calendars": [],
                             "chapters": [{"source": TOWN, "domain": "register", "sha256": "sha256:x"}]}, {"sha256:x": text})
    assert out["errors"] == [f"x/town/register.py:{line}: a default argument is state shared by every call: pass the value instead"]


# ── 9. What a chapter hands to another domain crosses the same boundary ─────────────────────────────────────────

ALPHA = '''
from realms.legislation import above

class Alpha(above("alpha")):
    uses = ["beta"]

    def check_t_poke(self, args):
        if args.what == "hand self":
            return self.deny(self.legislation.beta.take(self))
        if args.what == "hand method":
            return self.deny(self.legislation.beta.take(self.check_t_poke))

    def _secret(self):
        return "alpha's private member"
'''
BETA = '''
from realms.legislation import above

class Beta(above("beta")):
    def take(self, other):
        return other._secret()
'''


def test_a_chapter_handed_across_domains_is_the_public_view_and_a_method_is_refused():
    activate(layered({(TOWN, "alpha"): ALPHA, (TOWN, "beta"): BETA}, layers=[TOWN]))
    assert call("t_poke", what="hand self")["error"] == (
        "legislation error in x/town/beta.py: _secret is private to its chapter: other chapters cannot call it")
    assert call("t_poke", what="hand method")["error"] == (
        "legislation error in x/town/alpha.py: x/town/beta.py was handed method: only plain data crosses a domain boundary")


# ── The re-review: what crosses, what an effect may do, the revert floor, state, reads per domain ───────────────

TAX = '''
from realms.legislation import above

class Tax(above("tax")):
    def compute_tax_assess(self, entry, subject):
        return [{"concept": str(entry.code), "amount": 1}]
'''


def test_a_tax_entrys_code_reaches_compute_tax_assess():
    """What is secret is an operation's own: tax_assess keeps nothing, so a tax entry's code is not an invite's code."""
    activate(layered({(TOWN, "tax"): TAX}, layers=[TOWN]))
    Land(id="1")
    got = op.run(codex_api.compute("tax_assess", {"entry": {"code": "ibi_urban", "rate": 1}, "subject": "Land:1"}))
    assert got == [{"concept": "ibi_urban", "amount": 1}]


INVITE = register('''    def on_t_poke(self, event):
        if event.args.get("what") == "invite":
            self.ggg.invite_create(profile="boss")
            self.ggg.notification_send(recipient="User:mallory", title="inv", body="$eff:0:invite")
        if event.args.get("what") == "plain invite":
            self.ggg.invite_create(profile="nobody")
            self.ggg.notification_send(recipient="User:mallory", title="plain", body="$eff:0:invite")
''')


def test_a_codex_holds_no_permission_and_an_effects_reply_crosses_without_its_secret():
    from ggg import Invite, UserProfile

    world.policy(Department["Administration"], 3, 5)
    UserProfile(id="boss", description="all powers", allowed_to=["all"])
    UserProfile(id="nobody", description="no power", allowed_to=[])
    activate(layered({(TOWN, "register"): INVITE}, layers=[TOWN]))
    ok("t_poke", what="invite")
    assert failures()["x/town/register.py"]["on_t_poke"]["error"] == "invite_create: you do not hold all"
    assert notices("inv") == [] and rows(Invite) == []
    ok("t_poke", what="plain invite")  # a profile with no permission: the invite is made, its code stays with the realm
    [body] = notices("plain")
    assert "'id': " in body and "code" not in body
    assert "all" not in auth.permissions("mallory")


ADMIN = register('''    def on_t_poke(self, event):
        if event.args.get("what") == "admin":
            self.ggg.profile_create(profile="root2", allowed_to=["all"])
            self.ggg.role_assign(user="mallory", profile="root2")
''')


def test_a_governed_effect_is_a_proposal_even_where_the_department_decides_directly():
    from core.op import direct

    assert direct(Department["Administration"])
    activate(layered({(TOWN, "register"): ADMIN}, layers=[TOWN]))
    ok("t_poke", what="admin")
    assert "all" not in auth.permissions("mallory")
    filed = [(p.action["op"], p.proposer, p.status) for p in rows(Proposal) if p.action]
    assert filed == [("profile_create", "realm", "voting"), ("role_assign", "realm", "voting")]


NOARGS = register('''    def on_t_poke(self, event):
        if event.args.get("what") == "noargs":
            self.ggg.proposal_stop()
        if event.args.get("what") == "after":
            self.ggg.proposal_stop()
            self.ggg.notification_send(recipient="User:alice", title="after", body="")
''')


def test_an_effect_whose_governed_function_raises_is_the_chapters_failure_and_the_caller_stands():
    activate(layered({(TOWN, "register"): NOARGS}, layers=[TOWN]))
    before = Event.max_id()
    assert ok("t_poke", what="noargs")["done"] == "noargs"
    assert [Event[str(i)].op for i in range(before + 1, Event.max_id() + 1)] == ["t_poke"]  # audited once
    assert failures()["x/town/register.py"]["on_t_poke"]["error"] == "proposal_stop: KeyError: 'proposal'"
    ok("t_poke", what="after")
    assert notices("after") == []  # the effects stopped at the one that failed


def test_a_quarters_report_of_a_leg_of_a_reserved_federal_vote_is_reserved():
    vote = ok("federal_vote_propose", title="revert", action={"op": "codex_revert", "args": {}})["vote"]
    assert legislation._reserved("quarter_report", {"population": 3, "leg": {"vote": vote, "outcome": "passed"}})
    assert not legislation._reserved("quarter_report", {"population": 3})
    other = ok("federal_vote_propose", title="other", action={"op": "profile_create", "args": {"profile": "p"}})["vote"]
    assert not legislation._reserved("quarter_report", {"leg": {"vote": other}})


STATEFUL = register('''    def check_t_poke(self, args):
        f = days
        f.memo = args.what
''').replace("from realms.legislation import above", "from realms.legislation import above, days")
CALENDAR_STATE = register('''    def check_t_poke(self, args):
        c = self.calendar
        c.memo = args.what
''')
EVENT_OK = register('''    def on_t_poke(self, event):
        event.seen = [w for w in (event.args.get("what"),) if w]
''')


@pytest.mark.parametrize("text, line", [(STATEFUL, 6), (CALENDAR_STATE, 6)])
def test_an_attribute_is_set_on_self_or_on_what_the_method_was_given_never_on_a_local(text, line):
    out = legislation.build({"realm": TOWN, "layers": [TOWN], "calendars": [],
                             "chapters": [{"source": TOWN, "domain": "register", "sha256": "sha256:x"}]}, {"sha256:x": text})
    assert out["errors"] == [f"x/town/register.py:{line}: .memo = ...: a chapter sets attributes on self, or on the event a hook "
                             "was given, never on anything else"]
    out = legislation.build({"realm": TOWN, "layers": [TOWN], "calendars": [],
                             "chapters": [{"source": TOWN, "domain": "register", "sha256": "sha256:y"}]}, {"sha256:y": EVENT_OK})
    assert out["errors"] == []  # the event a hook was given takes attributes, as §4.1 says


TWO_HOOKS = {
    (TOWN, "register"): READ_LOOP,
    (TOWN, "water"): '''
from realms.legislation import above

class Water(above("water")):
    def on_t_poke(self, event):
        self.ggg.notification_send(recipient="User:alice", title="water", body=event.args.get("what", ""))
''',
}


def test_the_reads_budget_is_one_domains_so_a_looping_domain_costs_no_other_its_effects():
    activate(layered(TWO_HOOKS, layers=[TOWN]))
    ok("t_poke", what="read loop")
    assert notices("water") == ["read loop"]  # the water domain's hook ran and its effect applied
    recorded = failures()
    assert "x/town/register.py" in recorded and "x/town/water.py" not in recorded


def test_the_leg_index_is_filled_for_legs_written_before_it_existed():
    from api import federation
    from core.db import first
    from ggg import FederalVoteLeg

    vote = ok("federal_vote_propose", title="revert", action={"op": "codex_revert", "args": {}})["vote"]
    leg = next(iter(FederalVote[vote].legs))
    leg.db().field_index_remove(leg._type, "proposal_id", leg.proposal_id, leg._id)  # as a leg written before the index
    assert first(FederalVoteLeg, "proposal_id", leg.proposal_id) is None
    assert not legislation._reserved("proposal_vote", {"proposal": leg.proposal_id, "choice": "yes"})
    federation.index_legs()  # what setup.start runs once, where the realm starts after the upgrade
    assert first(FederalVoteLeg, "proposal_id", leg.proposal_id).id == leg.id
    assert legislation._reserved("proposal_vote", {"proposal": leg.proposal_id, "choice": "yes"})


# ── The third review: a legacy codex keeps acting for the realm; state through a parameter ─────────────────────


@op.op("treasury.manage")
def t_mark(c, args):
    """Tells whether the call carries the legacy mark: only the legacy hook path can set it."""
    return {"legacy": c.legacy}


def legacy_package(name):
    import json
    import pathlib as _pathlib

    folder = _pathlib.Path(__file__).resolve().parents[2] / "codices" / "codices" / name
    return {"manifest": json.loads((folder / "manifest.json").read_text()),
            "source": (folder / "backend" / "hooks.py").read_text()}


def test_a_legacy_codex_still_seeds_the_permissioned_profiles_of_its_departments():
    from ggg import Realm, UserProfile

    Realm["1"].require_marketplace_approval = False
    out = ok("codex_install", **legacy_package("agora"))
    assert out["init"]["ok"] and [e for e in out["init"]["effects"] if not e["ok"]] == []
    seeded = [p for p in rows(UserProfile) if p.allowed_to and p.id not in ("member", "admin")]
    assert seeded and ok("codex_status", query=True)["healthy"]  # the seats' profiles carry their departments' permissions


SEED = register('''    def on_t_poke(self, event):
        if event.args.get("what") == "seed":
            self.ggg.profile_create(profile="clerk", allowed_to=["proposal.create"])
        if event.args.get("what") == "mark":
            self.ggg.invite_create(profile="boss", legacy=True)
            self.ggg.t_mark(legacy=True)
''')


def test_the_same_effect_from_a_layered_codex_is_a_proposal_and_the_legacy_mark_cannot_be_obtained():
    from ggg import UserProfile

    UserProfile(id="boss", description="all powers", allowed_to=["all"])
    activate(layered({(TOWN, "register"): SEED}, layers=[TOWN]))
    ok("t_poke", what="seed")
    assert UserProfile["clerk"] is None
    assert [(p.action["op"], p.proposer) for p in rows(Proposal) if p.action] == [("profile_create", "realm")]
    ok("t_poke", what="mark")  # an argument named legacy is just an argument: the mark is the dispatcher's
    assert failures()["x/town/register.py"]["on_t_poke"]["error"] == "invite_create: you do not hold all"
    assert ok("t_mark")["legacy"] is False  # a member's call carries no mark either


KEEP = register('''    def check_t_poke(self, args):
        self.keep(days, args.what)

    def keep(self, f, value):
        f.memo = value
''').replace("from realms.legislation import above", "from realms.legislation import above, days")
FRESH = register('''    def check_t_poke(self, args):
        if args.what == "remember":
            self.memo = "kept"
        if args.what == "recall":
            return self.deny("memo: " + str(getattr(self, "memo", None)))

    def on_t_poke(self, event):
        event.count = getattr(event, "count", 0) + 1
        self.ggg.notification_send(recipient="User:alice", title="count", body=str(event.count))
''')


def test_a_store_through_a_parameter_is_refused_and_self_and_the_event_outlive_no_call():
    out = legislation.build({"realm": TOWN, "layers": [TOWN], "calendars": [],
                             "chapters": [{"source": TOWN, "domain": "register", "sha256": "sha256:x"}]}, {"sha256:x": KEEP})
    assert out["errors"] == ["x/town/register.py:8: .memo = ...: a chapter sets attributes on self, or on the event a hook was "
                             "given, never on anything else"]
    activate(layered({(TOWN, "register"): FRESH}, layers=[TOWN]))
    ok("t_poke", what="remember")
    assert call("t_poke", what="recall")["error"] == "memo: None"  # a fresh instance per call
    ok("t_poke", what="again")
    assert notices("count") == ["1", "1"]  # a fresh event per call (the denied recall ran no hook)


# ── The fourth review: what the legislation reads after G2 ───────────────────────────────────────────────────────


@op.op("treasury.manage", private=("kind", "value"))
def t_attribute(c, args):
    """A write whose kind and value the audit keeps out, as attribute_submit's, and the legislation still reads."""
    return {"done": True}


PRIVATE_NOT_SECRET = register('''    def check_t_attribute(self, args):
        return self.deny(f"{args.kind}={args.value}")
''')


def test_personal_data_the_audit_keeps_out_still_reaches_the_legislation_that_decides_on_it():
    activate(layered({(TOWN, "register"): PRIVATE_NOT_SECRET}, layers=[TOWN]))
    assert call("t_attribute", kind="large_family", value="general")["error"] == "large_family=general"
    assert legislation._secret("t_attribute") == () and op.ops()["t_attribute"]["private"] == ("kind", "value")


EXAMPLES = '''
from ggg import Attribute, Document, External, Human, Member, TaxPolicy
from realms.legislation import above

class Register(above("register")):
    def check_t_poke(self, args):
        what = args.what
        if what == "5.2":  # the languages of the ordinance's document, and every member
            return self.deny(",".join(Document.get("DOC-1").languages) + ":" + str(len(Member.each())))
        if what == "5.4":  # the recipient's kind, read from GGG's entity
            return self.deny(External.get("AGE").kind)
        if what == "5.6":  # the owner's verified large-family attribute
            human = Human.find("user", "alice")[0]
            verified = [a.value for a in Attribute.find("human", human.id) if a.status == "Verified"]
            return self.deny(",".join(verified))
        if what == "5.7":  # the saved policy's entries
            return self.deny(",".join([entry["code"] for entry in TaxPolicy.get("TP-1").taxes]))
'''


def test_the_design_examples_reads_are_answered():
    from ggg import Attribute, Document, External, Human, TaxPolicy

    Document(id="DOC-1", title="Ordenança", kind="ordinance", languages=["es"], author="alice", at=1)
    External(id="AGE", name="Administración General del Estado", kind="authority")
    Human(id="h-alice", user="alice", date_of_birth="1980-02-03")
    Attribute(id="A-1", human="h-alice", kind="large_family", value="general", status="Verified")
    TaxPolicy(id="TP-1", status="adopted", taxes=[{"code": "ibi_urban", "rate": 1000000}, {"code": "water_supply"}])
    activate(layered({(TOWN, "register"): EXAMPLES}, layers=[TOWN]))
    assert call("t_poke", what="5.2")["error"] == "es:7"
    assert call("t_poke", what="5.4")["error"] == "authority"
    assert call("t_poke", what="5.6")["error"] == "general"
    assert call("t_poke", what="5.7")["error"] == "ibi_urban,water_supply"
