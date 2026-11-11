"""The layered codex runtime on the real wasm (docs/codices/layered-codex.md §9.3, realms-gos#488): the chapters of the
active codex run in the codex subinterpreter, loaded once from ``ChapterSource`` and warmed again after an upgrade;
around a GGG operation the guards deny first, the hooks' effects apply after, and the failures count per chapter and
method. §5.2's working-day count starts from the realm's local day, on its own time zone. Nothing installs a layered
codex yet, so the rows are written through the controller's ``__shell__`` and activated through the runtime, as the
install task will (D2). The costs the design asks for (§9.3) are printed.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from test_layered_codex import PREVIOUS_WASM, RATE_LIMIT, ok, sha256

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src", "realm_backend"))
from realms.legislation.calendar import Calendar  # noqa: E402
from realms.legislation.periods import working_days  # noqa: E402

TOWN = "x/town"
MADRID = ZoneInfo("Europe/Madrid")
CALENDAR = {"weekend": ["sat", "sun"], "years": {
    str(year): [{"date": f"{year}-01-01", "name": {"es": "Año Nuevo"}}, {"date": f"{year}-10-12", "name": {"es": "Fiesta Nacional"}},
                {"date": f"{year}-12-25", "name": {"es": "Navidad"}}] for year in range(2020, 2032)}}  # the replica's clock starts in 2021
EU_DP = '''
from realms.legislation import above, extension

class DataProtection(above("data_protection")):
    @extension(legislation="GDPR Art. 6")
    def check_proposal_create(self, args):
        if args.title == "consent":
            return self.deny("consent is not a basis between public authorities")
'''
ES_DP = '''
from realms.legislation import above, extension

class DataProtection(above("data_protection")):
    @extension(legislation="Ley Orgánica 3/2018 Art. 8")
    def check_proposal_create(self, args):
        if args.title == "task":
            return self.deny("name the legislation that sets this public task")
'''
TOWN_DP = '''
from ggg import Member
from realms.legislation import above

class DataProtection(above("data_protection")):
    def check_proposal_create(self, args):
        if args.title == "outsider" and Member.get(args.description) is None:
            return self.deny("a recipient outside the realm needs the Administration")
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
        if args.action is None or args.action.op != "codex_install" or "checksum" not in args.action.args:
            return None  # only this realm's own change of legislation
        earliest = self.legislation.procedure.working_days_after(self.local_day(self.now()), 15)
        if args.effective_at is None or self.local_day(args.effective_at) < earliest:
            return self.deny("an ordinance takes effect at the earliest 15 working days after the vote closes (Art. 65.2)")

    @extension(legislation="Ley 7/1985 Art. 65.2")
    def on_proposal_create(self, event):
        event.order = ["es"]
        self.ggg.notification_send(recipient="User:" + event.user, topic="ordinance", title="Comunicació d'acord",
                                   body=event.result["proposal"])
'''
TOWN_LOCAL_REGIME = '''
from realms.legislation import above

class LocalRegime(above("local_regime")):
    def on_proposal_create(self, event):
        self.ggg.notification_send(recipient="User:" + event.user, topic="ordinance",
                                   title="Nova ordenança:" + ",".join(event.order), body=event.result["proposal"])
'''
TOWN_REALM = '''
from realms.legislation import above

class Realm(above("realm")):
    def on_codex_activate(self, event):
        self.ggg.notification_send(recipient="User:%(founder)s", topic="codex", title="activated", body=event.args["codex"])

    def compute_realm_join(self, user, home_zone, quarters):
        return max(quarters, key=lambda q: q.index).id if quarters else None
'''
TOWN_WATER = '''
from realms.legislation import above

class Water(above("water")):
    def check_proposal_create(self, args):
        if args.title == "break the water":
            raise ValueError("no water here")
        if args.title == "spin":
            while True:
                pass
'''
TOWN_REGISTER = '''
from realms.legislation import above

class Register(above("register")):
    def on_proposal_create(self, event):
        if event.args["title"] == "break the register":
            return 1 // 0
        if event.args["title"] == "loop":
            n = 0
            while True:
                n += 1
'''
ALPHA = '''
from realms.legislation import above

class Alpha(above("alpha")):
    uses = ["beta"]

    def check_proposal_create(self, args):
        if args.title == "probe self":
            return self.deny(self.legislation.beta.give_self()._secret())
        if args.title == "probe undeclared":
            return self.deny(str(self.legislation.beta.legislation.gamma))
'''
BETA = '''
from realms.legislation import above

class Beta(above("beta")):
    uses = ["gamma"]

    def give_self(self):
        return self

    def _secret(self):
        return "the secret"
'''
GAMMA = "from realms.legislation import above\nclass Gamma(above('gamma')):\n    pass\n"
DENY_ALL = '''
from realms.legislation import above

class Procedure(above("procedure")):
    def check_proposal_create(self, args):
        return self.deny("nothing may be proposed")
'''
INSTALL = '''
import json
from core import layered_codex as store, legislation
from core.op import run
from ggg import LayeredCodex
chapters, calendars, layers, previous = %(chapters)r, %(calendars)r, %(layers)r, %(previous)r
entries, paths = {}, {}
for source, domain, text in chapters:
    entries[f"{source}/{domain}"] = {"source": source, "sha256": store.store_chapter(store.checksum(text), text).sha256}
    paths.setdefault(domain, []).append(source)
for source, text in calendars:
    entries[f"{source}/calendar"] = {"source": source, "sha256": store.store_chapter(store.checksum(text), text).sha256}
cid = store.codex_id(layers, paths, entries)
for other in LayeredCodex.load_some(1, max(LayeredCodex.max_id(), 1)):
    if other.status == "active":
        other.status = "inactive"
codex = LayeredCodex[cid] or LayeredCodex(id=cid, layers=layers, layer_paths=paths, chapters=entries, installed_at=1)
codex.status, codex.previous = "active", previous
before = ic.performance_counter(0)
legislation.load(codex)
loaded = ic.performance_counter(0)
run(legislation.activate(codex))
print(json.dumps({"codex": cid, "load": loaded - before, "activate": ic.performance_counter(0) - loaded,
                  "failures": codex.failures}))
'''
MEASURE = '''
import json
from core import chain, legislation
t0 = ic.performance_counter(0)
guards = legislation._call("guard", op="proposal_create", args={"title": "measure", "action": None}, now=chain.now(), approvals=[])
t1 = ic.performance_counter(0)
event = {"user": %(founder)r, "args": {"title": "measure"}, "result": {"proposal": "P-0"}, "at": chain.now()}
hooks = [legislation._call("hook", op="proposal_create", domain=d["domain"], event=event, now=chain.now(), approvals=[])
         for d in legislation._call("hook_domains", op="proposal_create")]
t2 = ic.performance_counter(0)
print(json.dumps({"guard": t1 - t0, "hook": t2 - t1, "guards": len(guards), "hooks": len(hooks),
                  "effects": sum(len(h.get("effects", [])) for h in hooks)}))
'''
BUILD = '''
import json
from core import layered_codex as store, legislation
chapters, calendars = %(chapters)r, %(calendars)r
sources = {store.checksum(t): t for *_, t in chapters} | {store.checksum(t): t for _, t in calendars}
plan = {"realm": %(realm)r, "layers": %(layers)r,
        "chapters": [{"source": s, "domain": d, "sha256": store.checksum(t)} for s, d, t in chapters],
        "calendars": [{"source": s, "sha256": store.checksum(t)} for s, t in calendars]}
before = ic.performance_counter(0)
out = legislation.build(plan, sources)
print(json.dumps({"cost": ic.performance_counter(0) - before, "errors": out["errors"], "domains": sorted(out["views"])}))
'''
LOAD = '''
import json
from core import legislation, sandbox
from core.op import ops
from ggg import LayeredCodex
codex = LayeredCodex[%(codex)r]
plan, texts = legislation._plan_of(codex), legislation._texts(codex)
program, _ = sandbox.module_source(legislation.PROGRAM)
library = {**sandbox.module_sources(legislation.LIBRARY), legislation.FACTS: sandbox.module_source(legislation.FACTS)}
program += f"\\nLIBRARY = {library!r}\\n"
t0 = ic.performance_counter(0)
box = sandbox.Resident(program, legislation._answer, legislation.ACTIONS)
t1 = ic.performance_counter(0)
box.call("setup", operations=sorted(ops()), layers=plan["layers"], realm=plan["realm"],
         calendars=[[c["source"], legislation._calendar(texts, c)] for c in plan["calendars"]], entities=sorted(legislation.VIEWS), money=legislation.MONEY)
t2 = ic.performance_counter(0)
for c in plan["chapters"]:
    box.call("load", source=c["source"], domain=c["domain"], text=texts[c["sha256"]])
t3 = ic.performance_counter(0)
box.call("finish")
t4 = ic.performance_counter(0)
box.close()
print(json.dumps({"spawn": t1 - t0, "library": t2 - t1, "chapters": t3 - t2, "checks": t4 - t3, "count": len(plan["chapters"])}))
'''
STATE = '''
import json
from core import legislation
print(json.dumps({"codex": legislation._state["codex"] if isinstance(legislation._state["codex"], str) else None,
                  "loaded": legislation._state["box"] is not None}))
'''
# Guard calls straight into the running sandbox, which ``_call`` would replace once spent: how many it answered, and
# the error of the first it did not, with what that call and an answered one cost on average.
SPEND = '''
import json
from core import chain, legislation, sandbox
guard = {"op": "proposal_create", "args": {"title": "spend", "action": None}, "now": chain.now(), "approvals": []}
calls, error, failed, start = 0, None, 0, ic.performance_counter(0)
for _ in range(%(count)d):
    t = ic.performance_counter(0)
    try:
        legislation._ask("guard", guard)
    except sandbox.Spent as e:
        error, failed = str(e), ic.performance_counter(0) - t
        break
    calls += 1
print(json.dumps({"calls": calls, "error": error, "failed": failed,
                  "answered": (ic.performance_counter(0) - start - failed) // max(calls, 1)}))
'''
RELOAD = '''
import json
from core import legislation
from ggg import LayeredCodex
t = ic.performance_counter(0)
legislation.warm(LayeredCodex[legislation._state["codex"]])
print(json.dumps({"cost": ic.performance_counter(0) - t, "loaded": legislation._state["box"] is not None}))
'''
# The meter's two ways of ending a call, from a sandbox of its own with a small budget: a loop trips it, and a loop
# inside an ``except`` body keeps running past it until the grace margin ends it. Then a call after each, and an
# error of the program's own.
METER = '''
import json
from core import sandbox
program = """
def spin():
    while True:
        pass

def spin_in_handler():
    try:
        raise ValueError("caught")
    except ValueError:
        while True:
            pass

def fail():
    raise ValueError("not the meter")

def answer():
    return 1
"""
out = {}
for first in ("spin", "spin_in_handler", "fail"):
    box, out[first] = sandbox.Resident(program, lambda action, kw: None, budget=100_000), []
    for fn in (first, "answer"):
        try:
            out[first].append(box.call(fn))
        except RuntimeError as e:
            out[first].append([type(e).__name__, str(e)])
    box.close()
print(json.dumps(out))
'''
# The running codex's sandbox killed by the meter, as a long-lived one can end: its budget run out in an except body.
# It keeps a ``guard``: the next guarded operation asks the dead sandbox for it, and a sandbox without one fails as a
# missing function, which denies the operation, instead of as a spent budget, which reloads the codex.
KILL = '''
import json
from core import legislation, sandbox
program = """
def guard(**kwargs):
    return []

def spin():
    try:
        raise ValueError()
    except ValueError:
        while True:
            pass
"""
box = sandbox.Resident(program, legislation._answer, budget=100_000)
try:
    box.call("spin")
except RuntimeError as e:
    error = str(e)
legislation._state["box"].close()
legislation._state["box"] = box
print(json.dumps({"error": error}))
'''
# A codex row made active without loading it, as a realm finds one after an upgrade; its chapter cannot load.
BROKEN = '''
import json
from core import layered_codex as store
from ggg import LayeredCodex
text = %(text)r
row = store.store_chapter(store.checksum(text), text)
entries = {"x/town/procedure": {"source": "x/town", "sha256": row.sha256}}
for other in LayeredCodex.load_some(1, max(LayeredCodex.max_id(), 1)):
    if other.status == "active":
        other.status, previous = "inactive", other.id
cid = store.codex_id(["x/town"], {"procedure": ["x/town"]}, entries)
LayeredCodex(id=cid, layers=["x/town"], layer_paths={"procedure": ["x/town"]}, chapters=entries, status="active", previous=previous)
print(json.dumps({"codex": cid}))
'''
DEEP = "from realms.legislation import above\nclass Procedure(above('procedure')):\n    x = " + "-" * 50000 + "1\n"
READ_LOOP = '''
from ggg import Member
from realms.legislation import above

class Register(above("register")):
    def on_proposal_create(self, event):
        if event.args["title"] == "read loop":
            n = 0
            while True:
                Member.get("nobody")
                n += 1
'''


def run(realm, code):
    out = realm.shell(code)
    assert "Traceback" not in out, out
    return json.loads(out[out.index("{"):])


def chapters_of(founder, extra=()):
    """The running example: the EU's and Spain's data protection, Spain's procedure and local regime, the town's own."""
    return [("eu", "data_protection", EU_DP), ("eu/es", "data_protection", ES_DP), ("eu/es", "procedure", ES_PROCEDURE),
            ("eu/es", "local_regime", ES_LOCAL_REGIME), (TOWN, "data_protection", TOWN_DP),
            (TOWN, "local_regime", TOWN_LOCAL_REGIME), (TOWN, "realm", TOWN_REALM % {"founder": founder})] + list(extra)


def install(realm, chapters, layers=("eu", "eu/es", TOWN), calendars=(("eu/es", json.dumps(CALENDAR)),), previous=""):
    return run(realm, INSTALL % {"chapters": chapters, "calendars": list(calendars), "layers": list(layers), "previous": previous})


def proposal(realm, who, title, **more):
    return realm.call("proposal_create", {"title": title, **more}, sender=who)


def titles(realm, who):
    return [n["title"] for n in ok(realm, "notification_list", who, query=True)["notifications"]]


def failures(realm, who):
    return ok(realm, "codex_status", who, query=True)["failures"]


@pytest.fixture(scope="module")
def town(wasm_of, people):
    """A realm in setup on Madrid time, whose founder files proposals; the town's layered codex active."""
    from test_layered_codex import Canister

    realm = Canister(wasm_of("production"), installer=people["installer"])
    founder = people["founder"]
    ok(realm, "setup_step", people["installer"], step="install", network="local", founder=founder)
    ok(realm, "setup_step", founder, step="realm", name="Town", open_registration=True, timezone="Europe/Madrid")
    ok(realm, "setup_step", founder, step="trust_policy", require_marketplace_approval=False)
    ok(realm, "setup_step", founder, step="tokens", accounting_currency="DOM", accounting_currency_decimals=8)
    return realm


def test_a_activation_loads_the_chapters_once_and_runs_on_codex_activate(town, people):
    founder = people["founder"]
    got = install(town, chapters_of(founder, [(TOWN, "water", TOWN_WATER), (TOWN, "register", TOWN_REGISTER)]))
    assert got["failures"] == {}
    assert titles(town, founder) == ["activated"]
    parts = run(town, LOAD % {"codex": got["codex"]})
    print(f"load: {got['load']:,} instructions (spawn {parts['spawn']:,}, the library imported from text {parts['library']:,}, "
          f"{parts['count']} chapters {parts['chapters']:,}, checks {parts['checks']:,}); activate (hook and effect): {got['activate']:,}")
    assert got["load"] < 2_000_000_000, got


def test_b_guards_deny_down_the_layers_and_hooks_notify_as_the_codex(town, people):
    founder = people["founder"]
    assert proposal(town, founder, "consent")["error"] == "consent is not a basis between public authorities"
    assert proposal(town, founder, "task")["error"] == "name the legislation that sets this public task"
    assert proposal(town, founder, "outsider", description="nobody")["error"] == "a recipient outside the realm needs the Administration"
    pid = ok(town, "proposal_create", founder, title="outsider", description=founder)["proposal"]
    assert titles(town, founder)[-2:] == ["Comunicació d'acord", "Nova ordenança:es"]  # Spain's hook, then the town's
    notice = ok(town, "notification_list", founder, query=True)["notifications"][-1]
    assert notice["body"] == pid
    measured = run(town, MEASURE % {"founder": founder})
    print(f"one guard call ({measured['guards']} domains): {measured['guard']:,} instructions; "
          f"the hooks of one operation ({measured['hooks']} domains, one call each, {measured['effects']} effects queued): "
          f"{measured['hook']:,}")
    assert measured["guard"] < 100_000_000 and measured["hook"] < 100_000_000, measured


def test_c_spains_guard_counts_working_days_from_the_realms_local_day(town, people):
    founder = people["founder"]
    calendar = Calendar([CALENDAR])
    # To 23:00 UTC on a day whose next day is a working day: the realm, on Madrid time, is already in that next day.
    now = datetime.fromtimestamp(town.now(), timezone.utc)
    day = now.replace(hour=23, minute=0, second=0, microsecond=0) + timedelta(days=1)
    while day.weekday() > 3 or not calendar.is_working_day((day + timedelta(days=1)).strftime("%Y-%m-%d")):
        day += timedelta(days=1)
    town.wait(int(day.timestamp()) - town.now())
    utc_day, madrid_day = day.strftime("%Y-%m-%d"), day.astimezone(MADRID).strftime("%Y-%m-%d")
    assert madrid_day > utc_day
    by_utc, by_madrid = (calendar.add(d, working_days(15)) for d in (utc_day, madrid_day))
    assert by_madrid > by_utc

    def midnight_in_madrid(text):
        return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=MADRID).timestamp())

    ordinance = {"title": "ordinance", "action": {"op": "codex_install", "args": {"url": "https://example.org/c.json", "checksum": sha256("x")}}}
    early = "an ordinance takes effect at the earliest 15 working days after the vote closes (Art. 65.2)"
    assert proposal(town, founder, **ordinance)["error"] == early
    assert proposal(town, founder, effective_at=midnight_in_madrid(by_utc), **ordinance)["error"] == early  # counted on UTC it would pass
    assert ok(town, "proposal_create", founder, effective_at=midnight_in_madrid(by_madrid), **ordinance)["proposal"]


def test_d_a_raising_guard_denies_naming_the_chapter_and_a_raising_hook_never_fails_the_caller(town, people):
    founder = people["founder"]
    assert proposal(town, founder, "break the water")["error"] == "legislation error in x/town/water.py: ValueError: no water here"
    recorded = failures(town, founder)
    assert recorded["x/town/water.py"]["check_proposal_create"]["count"] == 1
    assert not ok(town, "codex_status", founder, query=True)["healthy"]
    pid = ok(town, "proposal_create", founder, title="break the register")["proposal"]  # the hook fails, the proposal stands
    assert ok(town, "proposal_get", founder, query=True, proposal=pid)["proposal"]["status"] == "voting"
    recorded = failures(town, founder)
    assert recorded["x/town/register.py"]["on_proposal_create"]["error"] == "ZeroDivisionError: integer division or modulo by zero"
    assert "x/town/water.py" not in recorded  # the guard succeeded this time: its entry cleared
    ok(town, "proposal_create", founder, title="fine")
    assert failures(town, founder) == {} and ok(town, "codex_status", founder, query=True)["healthy"]


def test_e_a_hook_that_exhausts_the_budget_fails_alone_and_the_codex_reloads(town, people):
    founder = people["founder"]
    assert ok(town, "proposal_create", founder, title="loop")["proposal"]
    # A spent budget poisons the interpreter; the hook ran one domain per call, so the failure is that chapter's.
    error = failures(town, founder)["x/town/register.py"]["on_proposal_create"]["error"]
    assert "BudgetExceeded" in error, error
    assert run(town, STATE)["loaded"]  # the spent interpreter was replaced
    assert proposal(town, founder, "consent")["error"] == "consent is not a basis between public authorities"
    assert "x/town/register.py" in failures(town, founder)  # the loop still counts until the hook next succeeds
    ok(town, "proposal_create", founder, title="fine again")
    assert failures(town, founder) == {}


def test_e1_both_ways_the_meter_ends_a_call_leave_a_spent_sandbox(town):
    """realms-gos#670: a sandbox whose budget ran out tells so in two texts, and only one names BudgetExceeded."""
    got = run(town, METER)
    tripped = "sandboxed call raised: BudgetExceeded: instruction budget (100000) exhausted"
    killed = "sandboxed call raised: RuntimeError: instruction budget (100000) exhausted; sandbox terminated after grace period"
    assert got["spin"] == [["Spent", tripped], ["Spent", tripped]], got
    assert got["spin_in_handler"] == [["Spent", killed], ["Spent", killed]], got
    assert got["fail"] == [["RuntimeError", "sandboxed call raised: ValueError: not the meter"], 1], got


def test_e2_a_sandbox_spent_over_thousands_of_calls_is_replaced_and_the_next_guarded_operation_runs(town, people, costs):
    """realms-gos#670: the budget is the sandbox's, over all its calls; the call that finds it spent loads the codex
    again and is asked once more."""
    founder = people["founder"]
    calls, got = 0, {"error": None}
    while got["error"] is None:
        got = run(town, SPEND % {"count": 1000})
        calls += got["calls"]
        assert calls < 100_000, "the sandbox's budget is never spent"
    print(f"the sandbox was spent after {calls:,} guard calls ({got['answered']:,} instructions each); the call that "
          f"found it spent cost {got['failed']:,}: {got['error']}")
    assert calls > 1000 and "instruction budget (10000000) exhausted" in got["error"], got
    assert run(town, STATE)["loaded"]  # the spent sandbox is still the running one: the next call replaces it
    assert ok(town, "proposal_create", founder, title="after the sandbox was spent")["proposal"]
    assert failures(town, founder) == {}
    reload = run(town, RELOAD)
    assert reload["loaded"], reload
    costs.report("a guard call into the codex sandbox (proposal_create)", got["answered"])
    costs.report("the guard call that finds the codex sandbox spent", got["failed"])
    costs.report("loading the codex again (warm)", reload["cost"])


def test_e3_a_guard_that_alone_spends_the_budget_denies_after_one_more_try_and_the_next_operation_runs(town, people):
    founder = people["founder"]
    error = proposal(town, founder, "spin")["error"]
    assert error.startswith("legislation error: the codex sha256:") and "instruction budget (10000000) exhausted" in error, error
    assert failures(town, founder)["codex"]["check_proposal_create"]["count"] == 1
    assert ok(town, "proposal_create", founder, title="fine after the spin")["proposal"]
    assert failures(town, founder) == {}


def test_e4_a_sandbox_the_meter_killed_is_replaced_by_the_next_guarded_operation(town, people):
    """realms-gos#670: a sandbox whose budget ran out while an except body ran is killed, with a text that names no
    BudgetExceeded. The running codex's sandbox is put in that state, as a chapter cannot get it there on purpose (it
    writes no except body, and the realm's sandbox catches a chapter's error ``as e``, where the meter trips softly);
    then a guarded operation runs."""
    founder = people["founder"]
    got = run(town, KILL)
    assert got["error"].endswith("sandbox terminated after grace period"), got
    assert ok(town, "proposal_create", founder, title="after the kill")["proposal"]
    assert failures(town, founder) == {}


def test_f_what_crosses_a_domain_boundary_is_plain_data_or_the_public_view(town, people):
    founder = people["founder"]
    install(town, chapters_of(founder, [(TOWN, "alpha", ALPHA), (TOWN, "beta", BETA), (TOWN, "gamma", GAMMA)]))
    assert proposal(town, founder, "probe self")["error"] == (
        "legislation error in x/town/alpha.py: _secret is private to its chapter: other chapters cannot call it")
    assert proposal(town, founder, "probe undeclared")["error"] == (
        "legislation error in x/town/alpha.py: legislation is not a public member of x/town/beta.py: other chapters reach its members only")


def test_g_build_refuses_a_chain_deeper_than_eight_and_reports_the_valencia_example(town, people):
    founder = people["founder"]
    steps = []
    for i in range(10):
        uses = f'    uses = ["step{i + 1}"]\n' if i < 9 else ""
        steps.append((TOWN, f"step{i}", f"from realms.legislation import above\nclass Step{i}(above('step{i}')):\n{uses}    pass\n"))
    got = run(town, BUILD % {"chapters": steps, "calendars": [], "realm": TOWN, "layers": [TOWN]})
    assert got["errors"][0].startswith("uses chain step0 → step1") and "calls between domains stop at depth 8" in got["errors"][0]
    got = run(town, BUILD % {"chapters": chapters_of(founder), "calendars": [("eu/es", json.dumps(CALENDAR))],
                             "realm": TOWN, "layers": ["eu", "eu/es", TOWN]})
    assert got["errors"] == [] and got["domains"] == ["data_protection", "local_regime", "procedure", "realm"]
    print(f"one build of the example (7 chapters, a calendar, the views): {got['cost']:,} instructions")
    assert got["cost"] < 2_000_000_000, got


def test_h_a_codex_that_refuses_every_proposal_cannot_block_a_revert_which_runs_no_hook(town, people):
    founder = people["founder"]
    previous = run(town, STATE)["codex"]
    before = titles(town, founder)
    install(town, [(TOWN, "procedure", DENY_ALL)], layers=[TOWN], calendars=(), previous=previous)
    assert proposal(town, founder, "anything")["error"] == "nothing may be proposed"
    pid = ok(town, "proposal_create", founder, title="revert", action={"op": "codex_revert", "args": {"codex": previous}})["proposal"]
    ok(town, "proposal_vote", founder, proposal=pid, choice="yes")  # the only member: the vote ends and the action runs
    assert ok(town, "proposal_get", founder, query=True, proposal=pid)["proposal"]["status"] == "executed"
    assert ok(town, "codex_status", founder, query=True)["codex"]["id"] == previous
    assert run(town, STATE) == {"codex": previous, "loaded": True}
    assert titles(town, founder) == before  # no on_codex_activate on a revert
    assert proposal(town, founder, "consent")["error"] == "consent is not a basis between public authorities"


def test_i_the_codex_is_warm_after_an_upgrade(town, people):
    founder = people["founder"]
    active = run(town, STATE)["codex"]
    town.wait(RATE_LIMIT)
    town.upgrade()
    assert run(town, STATE) == {"codex": active, "loaded": True}  # loaded in post_upgrade, before any call
    assert proposal(town, founder, "consent")["error"] == "consent is not a basis between public authorities"


def test_j_a_hook_that_loops_on_reads_fails_alone_and_the_caller_succeeds(town, people):
    founder = people["founder"]
    install(town, chapters_of(founder, [(TOWN, "register", READ_LOOP)]))
    assert ok(town, "proposal_create", founder, title="read loop")["proposal"]
    error = failures(town, founder)["x/town/register.py"]["on_proposal_create"]["error"]
    assert error.startswith("the chapters read for more than") and error.endswith("instructions in one call: the call stops")
    ok(town, "proposal_create", founder, title="reads fine")
    assert failures(town, founder) == {}


def test_k_an_upgrade_with_a_codex_that_cannot_load_records_it_and_the_realm_fails_closed(town, people):
    founder = people["founder"]
    broken = run(town, BROKEN % {"text": DEEP})["codex"]
    town.wait(RATE_LIMIT)
    town.upgrade()  # post_upgrade warms the codex: it does not load, and the upgrade still succeeds
    status = ok(town, "codex_status", founder, query=True)
    assert status["codex"]["id"] == broken and not status["healthy"]
    assert status["failures"]["codex"]["load"]["error"].startswith("the codex does not load: sandboxed call raised: MemoryError")
    assert run(town, STATE) == {"codex": broken, "loaded": False}
    assert proposal(town, founder, "anything")["error"].startswith("legislation error: the codex sha256:")
    assert ok(town, "codex_revert", people["ctl"])["codex"]  # reserved: no chapter runs, the realm is back
    assert ok(town, "codex_status", founder, query=True)["healthy"]


# Spain's quota as the design writes it (§5.6) and València's bonus, which reads the verified large-family title with find().
ES_TAX = '''
from realms.legislation import above, fixed, overridable

class Tax(above("tax")):
    @fixed(legislation="TRLRHL Arts. 61, 65, 71 and 74")
    def compute_tax_assess(self, entry, subject):
        if entry.code != "ibi_urban":
            return []
        gross = subject.assessed_value.times(entry.rate)            # nearest cent, half away from zero
        net = gross - gross.times(self.ibi_bonus(subject))
        items = [{"concept": entry.code, "amount": gross, "legislation": "TRLRHL Art. 71.1"}]
        if net < gross:
            items.append({"concept": entry.code + "_bonus", "amount": net - gross, "legislation": "TRLRHL Art. 74"})
        return items

    @overridable(legislation="TRLRHL Art. 74")
    def ibi_bonus(self, land):
        return 0
'''
VALENCIA_TAX = '''
from ggg import Attribute, Human
from realms.legislation import above, pct

class Tax(above("tax")):
    large_family_bonus = {"general": pct("60"), "special": pct("90")}

    def ibi_bonus(self, land):
        kind, _, user = land.owner.partition(":")
        for human in (Human.find("user", user) if kind == "User" else ()):
            for attribute in Attribute.find("human", human.id):
                if attribute.kind == "large_family" and attribute.status == "verified":
                    return self.large_family_bonus.get(attribute.value, 0)
        return 0
'''
UNIT = 10**8  # DOM has 8 decimals: a cent is 10**6 of the smallest unit
HALF_A_CENT = 23 * UNIT + 45 * 10**6  # 23.45: at 10 % it is 2.345, exactly between 2.34 and 2.35
IBI = {"code": "ibi_urban", "kind": "tax", "subject": "Land", "rate": 10_000_000, "facts": {}}  # 10 %
COMPUTE = '''
import json
from core import chain, legislation

def compute():
    before = ic.performance_counter(0)
    answer = legislation.compute("tax_assess", decimals=8, entry=%(entry)r, subject=%(subject)r)
    return answer, ic.performance_counter(0) - before

compute()  # the first call of a heap compiles the modules it reaches: not what a compute costs
answer, cost = compute()
print(json.dumps({"answer": answer, "cost": cost}))
'''


@pytest.fixture(scope="module")
def agora(wasm_of, people):
    """A launched realm on DOM, whose founder holds a verified large-family title, with two parcels of 23.45 DOM and
    a tax on land at 10 % under the layered codex of Spain and València."""
    from test_layered_codex import Canister

    realm = Canister(wasm_of("production"), installer=people["installer"])
    ctl, founder, alice = people["ctl"], people["founder"], people["alice"]
    ok(realm, "setup_step", people["installer"], step="install", network="local", founder=founder)
    ok(realm, "setup_step", founder, step="realm", name="Agora", open_registration=True)
    ok(realm, "setup_step", founder, step="trust_policy", require_marketplace_approval=False)
    ok(realm, "setup_step", founder, step="tokens", accounting_currency="DOM", accounting_currency_decimals=8)
    ok(realm, "setup_launch", founder)
    ok(realm, "token_register", ctl, symbol="DOM", ledger=people["installer"], decimals=8, fee=10_000)
    ok(realm, "realm_join", alice)
    for owner, x in ((founder, 0), (alice, 1)):
        parcel = ok(realm, "land_register", ctl, owner=owner, x=x, y=0)["land"]
        ok(realm, "land_assess", ctl, land=parcel, value=HALF_A_CENT, area=120, reference="CAT-17")
    title = ok(realm, "attribute_submit", founder, kind="large_family", value="general", ref="vault:title")["attribute"]
    ok(realm, "attribute_review", ctl, attribute=title, status="verified")
    ok(realm, "tax_policy_draft", ctl, document={"symbol": "DOM"}, taxes=[IBI])
    ok(realm, "tax_policy_propose", ctl, policy="TAX-POLICY-1")
    install(realm, [("eu/es", "tax", ES_TAX), (TOWN, "tax", VALENCIA_TAX)], layers=("eu/es", TOWN), calendars=())
    return realm


def test_m_compute_tax_assess_gives_the_ibi_with_ggg_s_rounding_and_the_bonus(agora, people, costs):
    """Spain's quota on the wasm, and what one compute costs (host reads of the parcel and of the title included)."""
    ctl, founder, alice = people["ctl"], people["founder"], people["alice"]
    assert ok(agora, "tax_assess", ctl, period="2026")["count"] == 2

    def items(who):
        [invoice] = ok(agora, "my_invoices", who, query=True)["invoices"]
        assert sum(i["amount"] for i in invoice["items"]) == invoice["amount"]
        return [(i["concept"], i["amount"]) for i in invoice["items"]]

    # 2.345 is a half: 2.35, where banker's rounding would give 2.34. The bonus is 60 % of that: 1.41.
    assert items(alice) == [("ibi_urban", 235 * 10**6)]
    assert items(founder) == [("ibi_urban", 235 * 10**6), ("ibi_urban_bonus", -141 * 10**6)]
    assert failures(agora, founder) == {}
    for entry, subject, what, answer in (
            (IBI, "Land:L-1", "with the bonus (a find() on Human and one on Attribute)", ("ibi_urban", 235 * 10**6)),
            (IBI, "Land:L-2", "without a title", ("ibi_urban", 235 * 10**6)),
            ({**IBI, "code": "water"}, "Land:L-1", "of a tax the chapter leaves to the realm: [] after reading the parcel", None)):
        got = run(agora, COMPUTE % {"entry": entry, "subject": subject})
        costs.report(f"compute_tax_assess {what}", got["cost"])
        print(f"compute_tax_assess {what}: {got['cost']:,} instructions")
        shown = [(i["concept"], i["amount"]) for i in got["answer"]][:1]  # the gross, before any bonus
        assert shown == ([answer] if answer else []), got
        assert got["cost"] < 50_000_000, got


FLOATS = {
    "a float": ("return [{\"concept\": \"x\", \"amount\": 0.5}]", "0.5: no floats in a chapter"),
    "float()": ("return float(subject.area)", "float: no floats in a chapter"),
    "a division": ("return subject.area / 3", "no `/` in a chapter"),
    "a division in place": ("n = subject.area\n        n /= 3\n        return n", "no `/` in a chapter"),
    "a power": ("return subject.area ** 2", "no `**` or pow() in a chapter"),
    "a power in place": ("n = subject.area\n        n **= 2\n        return n", "no `**` or pow() in a chapter"),
    "pow()": ("return pow(subject.area, 2)", "pow: no floats in a chapter"),
    "a local named float": ("float = 3\n        return float", "float: no floats in a chapter"),
}


@pytest.mark.parametrize("form", sorted(FLOATS))
def test_n_a_chapter_with_float_arithmetic_does_not_build(town, form):
    """The subset check the realm runs on its own interpreter's bytecode (3.13: BINARY_OP 11 and 24 are / and /=, 8 and 21
    are ** and **=), and the names it binds (a local is in co_varnames, not co_names)."""
    body, reason = FLOATS[form]
    text = f"from realms.legislation import above\nclass Tax(above('tax')):\n    def compute_tax_assess(self, entry, subject):\n        {body}\n"
    got = run(town, BUILD % {"chapters": [(TOWN, "tax", text)], "calendars": [], "realm": TOWN, "layers": [TOWN]})
    [error] = got["errors"]
    assert reason in error, got


RESERVED_LEG = '''
import json
from core import legislation
from core.db import first
from ggg import FederalVote, FederalVoteLeg
leg = next(iter(FederalVote[%(vote)r].legs))
print(json.dumps({"proposal": leg.proposal_id, "indexed": first(FederalVoteLeg, "proposal_id", leg.proposal_id) is not None,
                  "reserved": legislation.is_reserved("proposal_vote", {"proposal": leg.proposal_id, "choice": "yes"})}))
'''


@pytest.mark.skipif(not PREVIOUS_WASM, reason="REALM_PREVIOUS_WASM names no wasm")
def test_l_a_federal_revert_in_flight_across_the_upgrade_keeps_its_leg_reserved(wasm_of, people):
    """The leg of a federal vote written before FederalVoteLeg.proposal_id had its index is found by it after the
    upgrade (setup.start indexes the legs once), so no chapter guards the vote on it."""
    from test_layered_codex import new_realm

    founder = people["founder"]
    realm = new_realm(PREVIOUS_WASM, people)
    vote = ok(realm, "federal_vote_propose", founder, title="revert", action={"op": "codex_revert", "args": {}})["vote"]
    realm.wait(RATE_LIMIT)
    realm.upgrade(wasm_of("production"))
    got = run(realm, RESERVED_LEG % {"vote": vote})
    assert got["indexed"] and got["reserved"], got
    install(realm, [(TOWN, "procedure", DENY_ALL)], layers=[TOWN], calendars=())
    assert realm.call("proposal_create", {"title": "x"}, sender=founder)["error"] == "nothing may be proposed"
    assert ok(realm, "proposal_vote", founder, proposal=got["proposal"], choice="yes")["vote"]  # the leg: no guard
