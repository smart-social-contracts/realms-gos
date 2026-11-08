"""The runtime of a layered codex (docs/codices/layered-codex.md §9.3, realms-gos#488), in process: the chapters run
in the sandbox's fallback namespace (core.sandbox.Resident without ``_basilisk_sandbox``) through the same program the
canister runs. Guards before an operation, hooks after it, ``compute_`` answers during it; a raising guard denies,
a raising hook never fails the caller, both count; reserved operations run no chapter; a revert reloads without
``on_codex_activate``; what crosses a domain boundary is plain data (#543). tests/deployed/test_legislation_runtime.py
runs the same on the real wasm."""

import json

import pytest

from api import codex as codex_api
from api import proposals
from core import layered_codex as store
from core import legislation, localtime, op

from ggg import Codex, LayeredCodex, Quarter, Realm
from kit import call, ok, rows
import world

LOG = []  # what the chapters' effects wrote, through the realm's operations


@op.op("treasury.manage")
def t_act(c, args):
    """A governed-by-nothing write the chapters guard and hook."""
    return {"done": args.get("what", "")}


@op.op("treasury.manage", governed=proposals.realm_wide)
def t_approved(c, args):
    """A write the root department decides directly, so its approval is carried."""
    return {"done": "approved"}


@op.op("treasury.manage")
def t_log(c, args):
    """The chapters' effect: records what they wrote and who they were."""
    LOG.append((args["text"], c.origin))
    return {"logged": args["text"]}


@op.op("realm.data_view", read=True)
def t_day(c, args):
    return {}


# ── The chapters: realm x/town, syncing from Spain, which syncs from the EU ──────────────────────────────────────

EU_DP = '''
from realms.legislation import above, extension

class DataProtection(above("data_protection")):
    @extension(legislation="GDPR Art. 6")
    def check_t_act(self, args):
        if args.basis == "consent":
            return self.deny("consent is not a basis between public authorities")
'''
ES_DP = '''
from realms.legislation import above, extension

class DataProtection(above("data_protection")):
    @extension(legislation="Ley Orgánica 3/2018 Art. 8")
    def check_t_act(self, args):
        if args.basis == "task" and args.legislation is None:
            return self.deny("name the legislation that sets this public task")
'''
TOWN_DP = '''
from ggg import Member
from realms.legislation import above

class DataProtection(above("data_protection")):
    def check_t_act(self, args):
        if args.recipient is not None and Member.get(args.recipient) is None:
            return self.deny("a recipient outside the realm needs the Administration")

    def check_t_approved(self, args):
        if not self.approved_by("Administration"):
            return self.deny("the Administration decides this")
'''
ES_PROCEDURE = '''
from realms.legislation import above, fixed, working_days

class Procedure(above("procedure")):
    @fixed(legislation="Ley 39/2015 Art. 30")
    def working_days_after(self, day, n):
        return self.calendar.add(day, working_days(n))
'''
ES_LOCAL_REGIME = '''
from realms.legislation import above, extension

class LocalRegime(above("local_regime")):
    uses = ["procedure"]

    @extension(legislation="Ley 7/1985 Art. 65.2")
    def check_proposal_create(self, args):
        if args.action is None or args.action.op != "t_act":
            return None
        earliest = self.legislation.procedure.working_days_after(self.local_day(self.now()), 15)
        if args.effective_at is None or self.local_day(args.effective_at) < earliest:
            return self.deny("an ordinance takes effect at the earliest 15 working days after the vote closes (Art. 65.2)")

    @extension(legislation="Ley 7/1985 Art. 65.2")
    def on_t_act(self, event):
        event.order = ["es"]
        self.ggg.t_log(text="es:" + event.result["done"])
'''
TOWN_LOCAL_REGIME = '''
from realms.legislation import above

class LocalRegime(above("local_regime")):
    def on_t_act(self, event):
        self.ggg.t_log(text="town:" + ",".join(event.order) + ":" + event.user)
'''
TOWN_REALM = '''
from realms.legislation import above

class Realm(above("realm")):
    def on_codex_activate(self, event):
        self.ggg.t_log(text="activated " + event.args["codex"][:13])

    def compute_realm_join(self, user, quarters):
        return max(quarters, key=lambda q: q.index).id if quarters else None

    def compute_t_day(self, at):
        return self.local_day(at)
'''
BROKEN_HOOK = '''
from realms.legislation import above

class Register(above("register")):
    def on_t_act(self, event):
        if event.args.get("what") == "break the register":
            return 1 // 0
'''
BROKEN_GUARD = '''
from realms.legislation import above

class Water(above("water")):
    def check_t_act(self, args):
        if args.what == "break the water":
            raise ValueError("no water here")
        if args.what == "a view":
            return self.legislation
'''
ALPHA = '''
from realms.legislation import above

class Alpha(above("alpha")):
    uses = ["beta"]

    def check_t_act(self, args):
        if args.what == "probe self":
            return self.deny(self.legislation.beta.give_self()._secret())
        if args.what == "probe undeclared":
            return self.deny(str(self.legislation.beta.legislation.gamma))
        if args.what == "probe data":
            return self.deny(self.legislation.beta.give_self().name())
'''
BETA = '''
from realms.legislation import above

class Beta(above("beta")):
    uses = ["gamma"]

    def give_self(self):
        return self

    def name(self):
        return "beta:" + self.legislation.gamma.name()

    def _secret(self):
        return "the secret"
'''
GAMMA = '''
from realms.legislation import above

class Gamma(above("gamma")):
    def name(self):
        return "gamma"
'''
DENY_ALL = '''
from realms.legislation import above

class Procedure(above("procedure")):
    def check_proposal_create(self, args):
        return self.deny("nothing may be proposed")

    def check_proposal_vote(self, args):
        return self.deny("nothing may be voted")
'''
CALENDAR = json.dumps({"weekend": ["sat", "sun"], "years": {
    "2026": [{"date": "2026-10-12", "name": {"es": "Fiesta Nacional"}}, {"date": "2026-12-25", "name": {"es": "Navidad"}}],
    "2027": [{"date": "2027-01-01", "name": {"es": "Año Nuevo"}}]}})
TOWN = "x/town"


def step_chapters(count):
    """``count`` domains in a chain, each calling the next: ``step0`` reaches ``step<count-1>``."""
    chapters = {}
    for i in range(count):
        uses = f'    uses = ["step{i + 1}"]\n' if i + 1 < count else ""
        call_next = f"self.legislation.step{i + 1}.depth() + 1" if i + 1 < count else "0"
        chapters[(TOWN, f"step{i}")] = (f"from realms.legislation import above\nclass Step{i}(above('step{i}')):\n{uses}"
                                        f"    def depth(self):\n        return {call_next}\n")
    return chapters


def layered(chapters, calendars=(), layers=None, **fields):
    """A layered codex written through the entities, its texts stored, from ``{(source, domain): text}`` in layer order
    and ``[(source, calendar json)]``; the realm's own layer is the last."""
    layers = layers or list(dict.fromkeys(source for source, _ in chapters))
    paths = {}
    for source, domain in chapters:
        paths.setdefault(domain, []).append(source)
    entries = {}
    for (source, domain), text in chapters.items():
        entries[f"{source}/{domain}"] = {"source": source, "sha256": store.store_chapter(store.checksum(text), text).sha256}
    for source, text in calendars:
        entries[f"{source}/calendar"] = {"source": source, "sha256": store.store_chapter(store.checksum(text), text).sha256}
    return LayeredCodex(id=store.codex_id(layers, paths, entries), layers=layers, layer_paths=paths, chapters=entries,
                        installed_at=1, **fields)


def activate(codex):
    codex.status = "active"
    op.run(legislation.activate(codex))
    return codex


def town(extra=None, calendars=((f"eu/es", CALENDAR),)):
    """The running example: the EU's and Spain's data protection, Spain's procedure and local regime, the town's own."""
    chapters = {("eu", "data_protection"): EU_DP, ("eu/es", "data_protection"): ES_DP, ("eu/es", "procedure"): ES_PROCEDURE,
                ("eu/es", "local_regime"): ES_LOCAL_REGIME, (TOWN, "data_protection"): TOWN_DP,
                (TOWN, "local_regime"): TOWN_LOCAL_REGIME, (TOWN, "realm"): TOWN_REALM, **(extra or {})}
    return activate(layered(chapters, calendars, layers=["eu", "eu/es", TOWN]))


@pytest.fixture(autouse=True)
def fresh_realm():
    LOG.clear()
    world.build()
    legislation._state.update(codex=legislation.UNKNOWN, box=None, failing=False)
    localtime.use("UTC")
    yield
    legislation.load(None)


def failures():
    return ok("codex_status", query=True)["failures"]


# ── Loading and activation ───────────────────────────────────────────────────────────────────────────────────────


def test_activation_runs_on_codex_activate_once_and_a_revert_does_not():
    first = town()
    assert LOG == [("activated " + first.id[:13], "codex")]
    second = layered({(TOWN, "realm"): TOWN_REALM.replace("activated", "again")}, layers=[TOWN], previous=first.id)
    first.status = "inactive"
    activate(second)
    assert LOG[1:] == [("again " + second.id[:13], "codex")]
    assert ok("codex_revert")["codex"] == first.id
    assert LOG[2:] == []  # no hook on a revert
    assert (LayeredCodex[first.id].status, LayeredCodex[second.id].status) == ("active", "reverted")
    # the first codex is the one running again: its guard answers
    assert "consent is not a basis" in call("t_act", basis="consent")["error"]


def test_a_quarter_does_not_run_on_codex_activate():
    Realm["1"].is_quarter = True
    town()
    assert LOG == []


def test_the_codex_loads_on_first_use_after_a_new_heap_and_a_legacy_install_unloads_it():
    codex = town()
    legislation._state.update(codex=legislation.UNKNOWN, box=None)  # a new heap: nothing loaded yet
    assert "consent is not a basis" in call("t_act", basis="consent")["error"]
    assert legislation._state["codex"] == codex.id
    Realm["1"].require_marketplace_approval = False
    from test_codex import SOURCE, manifest

    ok("codex_install", manifest=manifest(), source=SOURCE)
    assert LayeredCodex[codex.id].status == "inactive" and legislation._state["codex"] is None
    assert ok("t_act", basis="consent")["done"] == ""  # no legislation runs for a legacy codex


def test_a_codex_that_does_not_load_fails_before_the_switch_and_denies_everything_once_it_runs():
    first = town()
    broken = layered({(TOWN, "procedure"): "from realms.legislation import above\nclass Procedure(above('procedure')):\n"
                      "    x = 1.5\n"}, layers=[TOWN], previous=first.id)
    with pytest.raises(Exception, match="floats are not allowed"):
        legislation.load(broken)  # the install task stops here: nothing switched
    assert legislation._state["codex"] == first.id and legislation._state["box"] is not None
    assert "consent is not a basis" in call("t_act", basis="consent")["error"]
    # the realm starts (post_upgrade) with a codex that does not load: recorded, and the realm fails closed
    first.status, broken.status = "inactive", "active"
    legislation.warm()
    assert "floats are not allowed" in LayeredCodex[broken.id].failures["codex"]["load"]["error"]
    error = call("t_act", what="anything")["error"]
    assert error.startswith("legislation error: the codex sha256:") and "is not loaded" in error
    assert not ok("codex_status", query=True)["healthy"]
    assert ok("codex_revert")["codex"] == first.id  # reserved: no chapter, no load needed
    assert ok("t_act", what="anything")["done"] == "anything"


# ── Guards, hooks and compute around an operation ────────────────────────────────────────────────────────────────


def test_guards_stack_down_the_layers_and_the_first_denial_refuses_the_operation():
    town()
    assert call("t_act", basis="consent")["error"] == "consent is not a basis between public authorities"
    assert call("t_act", basis="task")["error"] == "name the legislation that sets this public task"
    error = call("t_act", basis="task", legislation="Ley 7/1985", recipient="nobody")["error"]
    assert error == "a recipient outside the realm needs the Administration"
    assert ok("t_act", basis="task", legislation="Ley 7/1985", recipient="alice", what="shared")["done"] == "shared"
    assert call("t_act", basis="consent")["kind"] == "forbidden"


def test_a_direct_governed_call_carries_the_departments_approval():
    town()
    assert ok("t_approved")["done"] == "approved"  # the root department decides directly: approved_by holds


def test_hooks_run_every_layer_top_to_bottom_with_one_event_and_their_effects_apply_as_the_codex():
    town()
    LOG.clear()  # the activation's own effect
    ok("t_act", what="adopted", basis="task", legislation="x")
    assert LOG == [("es:adopted", "codex"), ("town:es:ctl", "codex")]
    assert failures() == {} and ok("codex_status", query=True)["healthy"]


def test_a_raising_guard_denies_naming_the_chapter_and_counts_until_it_next_succeeds():
    town({(TOWN, "water"): BROKEN_GUARD})
    error = call("t_act", what="break the water")["error"]
    assert error == "legislation error in x/town/water.py: ValueError: no water here"
    assert failures() == {"x/town/water.py": {"check_t_act": {"count": 1, "error": "ValueError: no water here", "at": 1790000000}}}
    assert not ok("codex_status", query=True)["healthy"]
    call("t_act", what="break the water")
    assert failures()["x/town/water.py"]["check_t_act"]["count"] == 2
    error = call("t_act", what="a view")["error"]  # a guard returns None or self.deny(reason), nothing else
    assert error.startswith("legislation error in x/town/water.py: check_t_act returned Domains")
    ok("t_act", what="fine")
    assert failures() == {} and ok("codex_status", query=True)["healthy"]


def test_a_raising_hook_never_fails_the_caller_and_counts_against_its_chapter():
    town({(TOWN, "register"): BROKEN_HOOK})
    LOG.clear()
    assert ok("t_act", what="break the register")["done"] == "break the register"
    assert LOG == [("es:break the register", "codex"), ("town:es:ctl", "codex")]  # the other domains' hooks still ran
    recorded = failures()
    assert recorded == {"x/town/register.py": {"on_t_act": {"count": 1, "error": "ZeroDivisionError: integer division or modulo by zero", "at": 1790000000}}}
    ok("t_act", what="fine")
    assert failures() == {}


def test_a_hooks_effect_that_fails_counts_and_a_reserved_or_time_zone_effect_is_refused():
    town({(TOWN, "register"): '''
from realms.legislation import above

class Register(above("register")):
    def on_t_act(self, event):
        if event.args.get("what") == "revert":
            self.ggg.codex_revert()
        if event.args.get("what") == "zone":
            self.ggg.realm_configure(timezone="Europe/Madrid")
        if event.args.get("what") == "unknown op":
            self.ggg.t_act(what="nested")
'''})
    ok("t_act", what="revert")
    assert failures()["x/town/register.py"]["on_t_act"]["error"] == "codex_revert: codex_revert is about a reserved operation: no chapter may call it"
    ok("t_act", what="zone")
    assert failures()["x/town/register.py"]["on_t_act"]["error"] == "realm_configure: a chapter never sets a time zone"
    assert Realm["1"].timezone == "UTC"
    before = len(LOG)
    ok("t_act", what="unknown op")
    assert "on_t_act" not in failures().get("x/town/register.py", {})
    # the nested t_act ran as the codex: no guard and no hook ran again for it
    assert LOG[before:] == [("es:unknown op", "codex"), ("town:es:ctl", "codex")]


def test_compute_answers_through_the_seam_and_places_a_member_in_a_quarter():
    town()
    assert op.run(codex_api.compute("tax_assess", {"entry": {"code": "ibi"}, "subject": "Land:1"})) is None  # no chapter
    assert op.run(codex_api.compute("t_day", {"at": 1790000000})) == "2026-09-21"
    Quarter(id="q-low", name="low", index=1, status="active", population=3)
    Quarter(id="q-high", name="high", index=2, status="active", population=7)
    assert ok("join_targets", "alice", query=True)["default"] == "q-high"  # the codex prefers the higher index


def test_a_compute_that_fails_fails_the_operation_asking():
    town({(TOWN, "water"): '''
from realms.legislation import above

class Water(above("water")):
    def compute_t_day(self, at):
        return 1
'''})
    with pytest.raises(Exception, match="compute_t_day is defined in realm, water: one domain answers"):
        op.run(codex_api.compute("t_day", {"at": 1}))
    assert failures()["x/town/realm.py"]["compute_t_day"]["count"] == 1


def test_the_local_day_is_the_realms_own():
    town()
    Realm["1"].timezone = "Europe/Madrid"
    localtime.use("Europe/Madrid")
    late = 1790000000 - 1790000000 % 86400 + 23 * 3600  # 23:00 UTC on 21 September 2026: already the 22nd in Madrid
    assert op.run(codex_api.compute("t_day", {"at": late})) == "2026-09-22"
    localtime.use("UTC")
    assert op.run(codex_api.compute("t_day", {"at": late})) == "2026-09-21"


def test_spains_guard_counts_working_days_from_the_realms_local_day():
    town()
    now = 1790000000  # Monday 21 September 2026, 17:33 UTC
    args = {"title": "ordinance", "action": {"op": "t_act", "args": {}}}
    error = call("proposal_create", **args)["error"]
    assert error.startswith("an ordinance takes effect at the earliest 15 working days")
    assert call("proposal_create", effective_at=now + 20 * 86400, **args)["error"] == error  # 11 October: too early
    # 15 working days after 21 September, over the 12 October holiday, is 13 October
    assert "error" in call("proposal_create", effective_at=now + 21 * 86400, **args)  # the 12th, a holiday
    assert ok("proposal_create", effective_at=now + 22 * 86400, **args)["proposal"]  # the 13th


# ── Across domains (#543) and the depth limit ───────────────────────────────────────────────────────────────────


def test_what_crosses_a_domain_boundary_is_plain_data_or_the_public_view():
    town({(TOWN, "alpha"): ALPHA, (TOWN, "beta"): BETA, (TOWN, "gamma"): GAMMA})
    assert call("t_act", what="probe data")["error"] == "beta:gamma"  # alpha reaches beta's public members
    error = call("t_act", what="probe self")["error"]
    assert error == "legislation error in x/town/alpha.py: _secret is private to its chapter: other chapters cannot call it"
    error = call("t_act", what="probe undeclared")["error"]
    assert error == ("legislation error in x/town/alpha.py: legislation is not a public member of x/town/beta.py: "
                     "other chapters reach its members only")


def test_a_uses_chain_deeper_than_eight_is_refused_at_build_and_eight_calls_run():
    sources = {}
    for (source, domain), text in step_chapters(10).items():
        sources[store.checksum(text)] = text
    plan = {"realm": TOWN, "layers": [TOWN], "calendars": [],
            "chapters": [{"source": TOWN, "domain": f"step{i}", "sha256": store.checksum(step_chapters(10)[(TOWN, f"step{i}")])}
                         for i in range(10)]}
    out = legislation.build(plan, sources)
    assert out["views"] == {} and out["cedar"] is None
    assert out["errors"][0].startswith("uses chain step0 → step1 → step2 → step3 → step4 → step5 → step6 → step7 → step8 → step9 "
                                       "is 9 calls deep: calls between domains stop at depth 8")
    codex = layered({**step_chapters(8), (TOWN, "realm"): '''
from realms.legislation import above

class Realm(above("realm")):
    uses = ["step0"]

    def compute_t_day(self, at):
        return self.legislation.step0.depth()
'''}, layers=[TOWN])
    activate(codex)
    assert op.run(codex_api.compute("t_day", {"at": 0})) == 7  # realm → step0 → … → step7: eight calls between domains


# ── Reserved operations (§10.4) ─────────────────────────────────────────────────────────────────────────────────


def test_a_codex_whose_guards_refuse_every_proposal_cannot_block_a_revert():
    first = town()
    first.status = "inactive"
    blocker = activate(layered({(TOWN, "procedure"): DENY_ALL}, layers=[TOWN], previous=first.id))
    assert call("proposal_create", "alice", title="x", action={"op": "t_act", "args": {}})["error"] == "nothing may be proposed"
    pid = ok("proposal_create", "alice", title="revert", action={"op": "codex_revert", "args": {}})["proposal"]
    for voter in world.PEOPLE:
        ok("proposal_vote", voter, proposal=pid, choice="yes")  # no guard on a vote about a reserved operation either
    assert ok("proposal_get", query=True, proposal=pid)["proposal"]["status"] == "executed"
    assert (LayeredCodex[first.id].status, LayeredCodex[blocker.id].status) == ("active", "reverted")


def test_build_returns_the_views_the_errors_and_the_cedar():
    chapters = {("eu/es", "procedure"): ES_PROCEDURE, (TOWN, "procedure"): "from realms.legislation import above\n"
                "class Procedure(above('procedure')):\n    def working_days_after(self, day, n):\n        pass\n"}
    sources = {store.checksum(t): t for t in chapters.values()} | {store.checksum(CALENDAR): CALENDAR}
    plan = {"realm": TOWN, "layers": ["eu/es", TOWN], "calendars": [{"source": "eu/es", "sha256": store.checksum(CALENDAR)}],
            "chapters": [{"source": s, "domain": d, "sha256": store.checksum(t)} for (s, d), t in chapters.items()]}
    out = legislation.build(plan, sources)
    assert out["errors"] == ["x/town/procedure.py:3: working_days_after is fixed by eu/es/procedure.py (Ley 39/2015 Art. 30)"]
    plan["chapters"] = plan["chapters"][:1]
    out = legislation.build(plan, sources)
    assert out["errors"] == [] and list(out["views"]) == ["procedure"]
    assert [m["name"] for m in out["views"]["procedure"]["members"]] == ["working_days_after"]
    forbid = ("from realms.legislation import F, above, forbid\nclass Tax(above('tax')):\n"
              "    cap = forbid('tax_policy_propose', (F.code == 'ibi_urban') & F.rate.is_some() & (F.rate > 1100000), each='taxes', legislation='TRLRHL Art. 72.3')\n")
    out = legislation.build({"realm": TOWN, "layers": [TOWN], "calendars": [],
                             "chapters": [{"source": TOWN, "domain": "tax", "sha256": store.checksum(forbid)}]},
                            {store.checksum(forbid): forbid})
    assert out["errors"] == [] and '@id("x/town/tax/cap")' in out["cedar"] and "context.facts.rate > 1100000" in out["cedar"]


def test_the_reserved_list_is_the_librarys():
    from realms.legislation import RESERVED

    assert legislation.RESERVED == RESERVED


def test_a_legacy_codex_is_untouched():
    """The legacy hook path: a legacy codex's hooks run as before and no layered runtime takes part."""
    Realm["1"].require_marketplace_approval = False
    from test_codex import SOURCE, manifest

    ok("codex_install", manifest=manifest(), source=SOURCE)
    assert isinstance(codex_api.active(), Codex)
    out = op.run(codex_api.hook("act"))
    assert out["ok"] and [e["op"] for e in out["effects"]] == ["t_note", "t_note", "proposal_open", "t_note"]
    assert ok("codex_status", query=True)["failures"] == {}
    assert rows(LayeredCodex) == []
