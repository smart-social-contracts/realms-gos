"""The generated forbids on the real wasm (docs/codices/layered-codex.md §9.4, realms-gos#489): a layered codex's
forbids become Cedar that the realm's engine parses once, when the codex becomes the running one, and that then judges
`tax_policy_draft` and `tax_policy_propose` entry by entry, an accepted proposal's replay, and every call after an
upgrade. A legacy realm decides as before. The costs of §9.4 are measured here and printed at the end of the session.
"""

import pytest
from network import Network
from test_legislation_runtime import BUILD, install, run

SPAIN, TOWN = "eu/es", "x/town"
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
TOWN_TAX = """
from realms.legislation import above

class Tax(above("tax")):
    capital = True
    surface_public_transport = True
"""
CODEX = [(SPAIN, "tax", SPAIN_TAX), (TOWN, "tax", TOWN_TAX)]
CAP = 1_240_000  # 1.24 %: 1.10 % and 0.07 % for a capital and for surface public transport


def entry(code, rate=None):
    return {"code": code, "kind": "tax", "subject": "Land", **({} if rate is None else {"rate": rate}), "facts": {}}


def drafted(*entries):
    return {"document": {"symbol": "DOM"}, "taxes": list(entries)}


def call(realm, op, who, **args):
    return realm.call(op, args, sender=realm.network.people.get(who, who))


def ok(realm, op, who, **args):
    out = call(realm, op, who, **args)
    assert out["ok"], out
    return out


@pytest.fixture(scope="module")
def capital(wasm_of, people):
    return Network(wasm_of("production"), people).realm("capital")


# The cost of what the realm asks of its Cedar engine for one call: the permission decision, and the forbids.
DECISION = '''
import json
from core import auth, chain, op
name, args = %(name)r, %(args)r
spec, me = op.ops()[name], chain.caller()
t0 = ic.performance_counter(0)
auth.authorize(me, me, name, spec, args)
t1 = ic.performance_counter(0)
auth.refuse_forbidden(me, name, spec, args, "")
t2 = ic.performance_counter(0)
print(json.dumps({"permission": t1 - t0, "forbids": t2 - t1, "in_force": auth.live["forbids"] is not None}))
'''
# Where the cost of putting a codex's forbids in force goes: the sandbox (the library, the fact schema, the chapters),
# the Cedar it generates and hands over, and the realm's engine parsing and validating it.
LOAD = '''
import json
from core import auth, legislation
from ggg import LayeredCodex
codex = LayeredCodex[%(codex)r]
plan, texts = legislation._plan_of(codex), legislation._texts(codex)
t0 = ic.performance_counter(0)
box = legislation._spawn(plan, texts)
t1 = ic.performance_counter(0)
generated = legislation._generated(box)
auth.use_forbids(None)  # the same forbids again are not parsed again: start from the realm's own policies
t2 = ic.performance_counter(0)
auth.use_forbids(generated)
t3 = ic.performance_counter(0)
box.close()
print(json.dumps({"sandbox": t1 - t0, "generate": t2 - t1, "engine": t3 - t2,
                  "policies": len(generated["policies"]) if generated else 0,
                  "schema": len(generated["schema"]) if generated else 0}))
'''
# What the schema of every operation, instead of those the forbids name, would cost (§9.4): handing it over from the
# sandbox, and the engine parsing and validating it with the same policies.
FULL = '''
import json
from core import auth, sandbox
from ggg_facts import FACTS
from realms.legislation import cedar as generator
schema = generator.schema(generator.FactSchema(FACTS), (), auth.CONTEXT)
policies = auth.live["policies"]
t0 = ic.performance_counter(0)
sandbox.plain({"schema": schema})
t1 = ic.performance_counter(0)
auth.load(schema, policies)
t2 = ic.performance_counter(0)
auth.load(auth.live["schema"], policies)
print(json.dumps({"hand_over": t1 - t0, "engine": t2 - t1, "schema": len(schema)}))
'''
# What a refusal costs, with and without naming the forbid that fired (one more call to the sandbox, only then).
REFUSAL = '''
import json
from core import auth, chain, legislation, op
name, args = %(name)r, %(args)r
spec, me = op.ops()[name], chain.caller()


def refused():
    before = ic.performance_counter(0)
    try:
        auth.refuse_forbidden(me, name, spec, args, "")
        message = ""
    except auth.Denied as e:
        message = str(e)
    return ic.performance_counter(0) - before, message


named, message = refused()
real, legislation.firing = legislation.firing, lambda action, facts: []
unnamed, _ = refused()
legislation.firing = real
print(json.dumps({"named": named, "unnamed": unnamed, "message": message}))
'''
# The boundary on the wasm: a policy set the sandbox hands over is read as the grammar the generator writes. The text of
# a citation may say "permit"; a `//` comment that ends at `\\r`, a permit, a forbid of `call` or of one person may not.
BOUNDARY = '''
import json
from core import forbids
forbid = 'forbid (principal, action == Realm::Action::"a", resource)\\n  when { true };\\n'
cases = {
    "string": '@legislation("a permit (x); // /*")\\n' + forbid,
    "review": forbid + '// "\\rpermit (principal, action, resource);\\r// "\\n',
    "crlf": forbid + "\\r\\npermit (principal, action, resource);\\r\\n",
    "block": forbid + "/* permit (principal, action, resource); */",
    "plain": forbid + "permit (principal, action, resource);\\n",
    "call": 'forbid (principal, action == Realm::Action::"call", resource)\\n  when { true };\\n',
    "person": 'forbid (principal == Realm::User::"alice", action == Realm::Action::"a", resource)\\n  when { true };\\n',
}
got = {}
for name, text in cases.items():
    try:
        got[name] = forbids.targets(text, {"a": {}, "call": {}}, {"a"}, ())
    except Exception as e:
        got[name] = str(e)
print(json.dumps(got))
'''
IN_FORCE = '''
from core import auth
print(auth.live["forbids"] is not None, sorted(auth.live["forbids"]["targeted"]) if auth.live["forbids"] else [])
'''


def measure(realm, name, args):
    return run(realm, DECISION % {"name": name, "args": args})


def test_a_a_legacy_realm_decides_as_before(capital, costs):
    legacy = ok(capital, "tax_policy_draft", "ctl", **drafted(entry("ibi_urban", CAP + 1)))["policy"]
    ok(capital, "tax_policy_draft", "ctl", **drafted(entry("ibi_urban")))  # no rate: nothing forbids it
    assert capital.shell(IN_FORCE).strip() == "False []"
    got = measure(capital, "tax_policy_draft", drafted(entry("ibi_urban", CAP), entry("water", 5)))
    assert got["in_force"] is False and got["forbids"] < 1_000_000  # nothing is asked
    costs.report("a permission decision, a realm with no layered codex", got["permission"])
    print(f"legacy realm: permission decision {got['permission']:,}, forbids check {got['forbids']:,} instructions")
    capital.legacy = legacy


def test_b_the_install_checks_the_generated_cedar_without_touching_the_policies_in_force(capital, costs):
    built = run(capital, BUILD % {"chapters": CODEX, "calendars": [], "realm": TOWN, "layers": [SPAIN, TOWN]})
    assert built["errors"] == [] and built["domains"] == ["tax"]
    assert capital.shell(IN_FORCE).strip() == "False []"  # step 5 validated them; the switch puts them in force
    costs.report("install step 5 (build) of a codex with forbids, Cedar validated", built["cost"])
    print(f"install step 5, build of a codex with forbids: {built['cost']:,} instructions")


def test_c_the_forbids_are_parsed_once_when_the_codex_becomes_the_running_one(capital, costs):
    # a proposal to adopt an over-cap draft, filed while the realm had no forbids, and one vote in
    filed = ok(capital, "proposal_create", "founder", title="adopt",
               action={"op": "tax_policy_propose", "args": {"policy": capital.legacy}})["proposal"]
    ok(capital, "proposal_vote", "alice", proposal=filed, choice="yes")
    got = install(capital, CODEX, layers=(SPAIN, TOWN), calendars=())
    assert got["failures"] == {}
    assert capital.shell(IN_FORCE).strip() == "True ['tax_policy_draft', 'tax_policy_propose']"
    stored = capital.shell(f"from ggg import LayeredCodex\nprint(LayeredCodex[{got['codex']!r}].forbids)").strip()
    assert stored == "['tax_policy_draft', 'tax_policy_propose']"  # kept for a day the codex does not load
    boundary = run(capital, BOUNDARY)
    assert boundary["string"] == {"a": ["a"]}, boundary
    for refused in ("review", "crlf", "block", "plain", "call", "person"):
        assert isinstance(boundary[refused], str) and "generated" in boundary[refused], (refused, boundary)
    capital.filed = filed
    costs.report("install a codex with forbids: load (sandbox, generated Cedar, engine) and activate", got["load"] + got["activate"])
    parts = run(capital, LOAD % {"codex": got["codex"]})
    assert parts["sandbox"] + parts["generate"] + parts["engine"] < 3_000_000_000, parts
    print(f"load of a codex with forbids: sandbox {parts['sandbox']:,}, generated Cedar and schema handed over "
          f"{parts['generate']:,} ({parts['policies']} + {parts['schema']} characters), engine parse and validation "
          f"{parts['engine']:,}")
    for what, count in (("sandbox, spawn to finish", "sandbox"), ("Cedar generation and handover", "generate"),
                        ("Cedar engine: parse and validate", "engine")):
        costs.report(f"load of a codex with forbids: {what}", parts[count])
    full = run(capital, FULL)
    costs.report("schema of every operation: handed over from the sandbox", full["hand_over"])
    costs.report("schema of every operation: engine parse and validation", full["engine"])
    print(f"the schema of every operation ({full['schema']:,} characters): handed over {full['hand_over']:,}, "
          f"engine {full['engine']:,}")


def test_d_the_entries_the_forbids_refuse_are_refused_at_draft_and_at_propose(capital, costs):
    for refused in (entry("ibi_urban", CAP + 1), entry("ibi_urban"), entry("ibi_urban", 399_999), entry("ibi_rustic")):
        out = call(capital, "tax_policy_draft", "ctl", **drafted(entry("water", 5), refused))
        assert not out["ok"] and "tax_policy_draft is forbidden by the legislation: taxes[1]" in out["error"], out
    saved = ok(capital, "tax_policy_draft", "ctl", **drafted(entry("ibi_urban", CAP), entry("water", 5)))["policy"]
    refused = call(capital, "tax_policy_propose", "ctl", policy=capital.legacy)  # saved before the legislation
    assert not refused["ok"] and "tax_policy_propose is forbidden by the legislation: taxes[0]" in refused["error"]
    assert "refused by eu/es/tax/ibi_urban_cap (TRLRHL Art. 72.3)" in refused["error"]
    files = call(capital, "proposal_create", "founder", title="adopt",  # the proposal that would carry it is refused too
                 action={"op": "tax_policy_propose", "args": {"policy": capital.legacy}})
    assert not files["ok"] and "taxes[0]: refused by eu/es/tax/ibi_urban_cap" in files["error"], files
    capital.saved = saved
    assert ok(capital, "tax_policy_propose", "ctl", policy=saved)["status"] == "adopted"


def test_e_a_decision_reuses_what_the_engine_holds(capital, costs):
    """Each decision costs a few million instructions and no more after the first: nothing parses again."""
    load = costs.samples["load of a codex with forbids: Cedar engine: parse and validate"][-1]
    counts = []
    for n in (1, 2, 5, 5, 5, 20):
        got = measure(capital, "tax_policy_draft", drafted(*[entry(f"tax{i}", 5) for i in range(n)]))
        counts.append((n, got["permission"], got["forbids"]))
    print("(entries, permission decision, forbids check):", ", ".join(f"({n}, {p:,}, {f:,})" for n, p, f in counts))
    again = [f for n, _, f in counts if n == 5]
    assert max(again) - min(again) < 0.05 * min(again), again  # the same cost every time
    assert counts[0][2] < load / 10, (counts, load)  # one request is far below one parse of the set
    for n, permission, forbids in counts:
        costs.report(f"forbids check of a draft of {n} entries (a request per entry)", forbids)
    costs.report("a permission decision with the forbids in force", counts[0][1])
    saved = measure(capital, "tax_policy_propose", {"policy": capital.saved})  # the saved policy's two entries
    assert saved["in_force"] and 0 < saved["forbids"] < load
    costs.report("forbids check of a proposal of a saved policy of 2 entries", saved["forbids"])


def test_f_naming_the_forbid_costs_something_only_when_a_request_is_refused(capital, costs):
    refused = run(capital, REFUSAL % {"name": "tax_policy_draft",
                                      "args": drafted(entry("ibi_urban", CAP), entry("ibi_urban", CAP + 1))})
    assert refused["message"].endswith("taxes[1]: refused by eu/es/tax/ibi_urban_cap (TRLRHL Art. 72.3)"), refused
    extra = refused["named"] - refused["unnamed"]
    print(f"a refused draft of 2 entries: {refused['named']:,} instructions, of which naming the forbid {extra:,}")
    assert 0 < extra < 50_000_000, refused
    costs.report("a refused draft of 2 entries, forbid named", refused["named"])
    costs.report("naming the forbids that refused an element (the sandbox, only on a refusal)", extra)
    allowed = measure(capital, "tax_policy_draft", drafted(entry("ibi_urban", CAP), entry("water", 5)))
    assert allowed["forbids"] < refused["named"]  # an allowed call never asks the sandbox


def test_g_a_forbid_installed_between_filing_and_execution_refuses_the_execution(capital):
    ok(capital, "proposal_vote", "bob", proposal=capital.filed, choice="yes")  # the vote that carries it
    proposal = ok(capital, "proposal_get", "founder", query=True, proposal=capital.filed)["proposal"]
    assert proposal["status"] == "failed", proposal
    assert "tax_policy_propose is forbidden by the legislation: taxes[0]" in proposal["result"]["error"]
    drafts = capital.call("tax_policy_view", {}, sender=capital.network.people["ctl"], query=True)["drafts"]
    assert capital.legacy in [d["id"] for d in drafts]  # still a draft: the policy in force is the one adopted before


def test_h_the_forbids_are_in_force_again_after_an_upgrade(capital):
    capital.wait(600)  # the replica refuses an upgrade right after the install of the wasm
    capital.upgrade()
    assert capital.shell(IN_FORCE).strip() == "True ['tax_policy_draft', 'tax_policy_propose']"
    out = call(capital, "tax_policy_draft", "ctl", **drafted(entry("ibi_urban", CAP + 1)))
    assert not out["ok"] and "forbidden by the legislation: taxes[0]" in out["error"], out
    assert ok(capital, "tax_policy_draft", "ctl", **drafted(entry("ibi_urban", CAP)))["policy"]


def test_i_a_codex_without_forbids_costs_the_engine_and_the_fact_schema_nothing(capital, costs):
    """The next codex forbids nothing: the realm's own policies come back, and its load never reads GGG's fact schema."""
    got = install(capital, [(TOWN, "tax", TOWN_TAX)], layers=(TOWN,), calendars=(), previous="")
    assert capital.shell(IN_FORCE).strip() == "False []"
    parts = run(capital, LOAD % {"codex": got["codex"]})
    assert parts["generate"] < 5_000_000 and parts["engine"] < 1_000_000 and parts["policies"] == 0, parts
    costs.report("load of a codex without forbids: Cedar generation and handover", parts["generate"])
    costs.report("load of a codex without forbids: sandbox, spawn to finish", parts["sandbox"])
    assert ok(capital, "tax_policy_draft", "ctl", **drafted(entry("ibi_urban", CAP + 1)))["policy"]


def test_j_a_codex_that_goes_takes_its_forbids_with_it(capital):
    install(capital, CODEX, layers=(SPAIN, TOWN), calendars=())
    assert capital.shell(IN_FORCE).strip() == "True ['tax_policy_draft', 'tax_policy_propose']"
    ok(capital, "codex_uninstall", "ctl")
    assert capital.shell(IN_FORCE).strip() == "False []"
    assert ok(capital, "tax_policy_draft", "ctl", **drafted(entry("ibi_urban", CAP + 1)))["policy"]
