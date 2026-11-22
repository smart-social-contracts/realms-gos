"""What an extension's backend costs in its sandbox, and how it behaves there, on the real wasm.

A study for .cto/reports/extension-grant-cost-2026-11-22.md (branch study/extension-grant-cost, 22 Nov 2026), not a
regression test. ``BENCH`` is an extension written for it alone. It is installed as a row, as test_inner_calls does,
with different operation sets, and called through ``extension_call`` (api/extensions.py), which runs it in a fresh
subinterpreter (core/sandbox.py ``execute``) as the member who calls it. Each test prints what it measured and reports
it to ``costs``; the shell programs count with ``ic.performance_counter(0)``, the instructions of the message so far.
"""

import json

import pytest
from pic import Canister
from test_delegation_groups import launch, ok

BENCH = '''
def nothing():
    return 1


def spin(n):
    total = 0
    for i in range(n):
        total += i
    return total


def reads(n, proposal):
    out = None
    for _ in range(n):
        out = rpc("proposal_get", proposal=proposal, count=1)
    return out["ok"] if out else None


def renames(n):
    out = None
    for i in range(n):
        out = rpc("profile_update", nickname="bench %d" % i)
    return out["ok"] if out else None


def proposes(n):
    out = None
    for i in range(n):
        out = rpc("proposal_create", title="Bench motion %d" % i)
    return out["ok"] if out else None


def one(op, args):
    try:
        return {"reply": rpc(op, **args)}
    except Exception as e:
        return {"raised": type(e).__name__ + ": " + str(e)[:300]}


def echo(payload):
    return payload


def relay(proposal, pad):
    return rpc("proposal_get", proposal=proposal, count=1, pad=pad)["ok"]


def nest(depth):
    if depth <= 0:
        return 0
    return rpc("extension_call", extension="nester", function="nest", args={"depth": depth - 1})


def fails():
    raise ValueError("a bug in the extension")
'''
# The operations the benchmark uses: a read and two writes a member holds, and one a member does not hold.
DECLARED = ["proposal_get", "profile_update", "proposal_create", "governance_update"]

PRELUDE = '''
import json
import _basilisk_sandbox as native
from core import auth, sandbox
from core.op import audit, call, dispatch, ops, run
from ggg import Extension

def counted(fn, *a, **k):
    t = ic.performance_counter(0)
    out = fn(*a, **k)
    return ic.performance_counter(0) - t, out

def put(name, operations, source):
    """The row an install makes, open to every member."""
    row = Extension[name] or Extension(id=name, version="1.0.0")
    row.operations, row.source = list(operations), source
    row.access = {"profiles": [], "departments": []}

def ext(user, name, function, **args):
    return call("extension_call", {"extension": name, "function": function, "args": args}, user=user)

def timed(user, name, function, **args):
    used, out = counted(ext, user, name, function, **args)
    assert out["ok"], out
    return used

BENCH = %(bench)r
DECLARED = %(declared)r
ALICE = %(alice)r
P = %(proposal)r
'''


@pytest.fixture(scope="module")
def realm(wasm_of, people):
    """A launched realm: Alice and Bob are members, Bob's proposal P-1 is open, the benchmark extension is installed."""
    realm = Canister(wasm_of("production"), installer=people["installer"])
    launch(realm, people)
    realm.proposal = ok(realm, "proposal_list", people["alice"], query=True)["proposals"][0]["id"]
    shell(realm, people, 'put("bench", DECLARED, BENCH)\nprint(json.dumps({}))')
    return realm


def shell(realm, people, program):
    """Run ``program`` after the prelude in the realm's heap and return the JSON it printed last."""
    prelude = PRELUDE % {"bench": BENCH, "declared": DECLARED, "alice": people["alice"],
                         "proposal": getattr(realm, "proposal", "")}
    out = realm.shell(prelude + program)
    assert "Traceback" not in out, out
    return json.loads(out.strip().splitlines()[-1])


def report(costs, prefix, numbers):
    for what, n in numbers.items():
        if isinstance(n, int):
            costs.report(f"{prefix}: {what}", n)
    print(prefix, json.dumps(numbers, indent=1))


BREAKDOWN = '''
ext(ALICE, "bench", "nothing")  # once before timing: it compiles what the call reads
calls = [timed(ALICE, "bench", "nothing") for _ in range(3)]
row = Extension["bench"]
text = sandbox.LOADER + row.source
parts = {}
t = ic.performance_counter(0)
digest = native.sha256(text)
parts["sha256 of the program"] = ic.performance_counter(0) - t
t = ic.performance_counter(0)
native.approve_hash(digest)
parts["approve_hash"] = ic.performance_counter(0) - t
t = ic.performance_counter(0)
handle = native.spawn_subinterpreter(text, digest, "", tuple(row.operations), lambda *a: None, sandbox.BUDGET)
parts["spawn (ggg_sdk loader + extension source)"] = ic.performance_counter(0) - t
t = ic.performance_counter(0)
native.call_in_subinterpreter(handle, "nothing", {})
parts["call nothing()"] = ic.performance_counter(0) - t
t = ic.performance_counter(0)
native.close_subinterpreter(handle)
parts["close"] = ic.performance_counter(0) - t
native.revoke_hash(digest)
bare = row.source
d2 = native.sha256(bare)
native.approve_hash(d2)
t = ic.performance_counter(0)
h2 = native.spawn_subinterpreter(bare, d2, "", (), lambda *a: None, sandbox.BUDGET)
parts["spawn of the extension source alone (no ggg_sdk)"] = ic.performance_counter(0) - t
native.close_subinterpreter(h2)
native.revoke_hash(d2)
empty = "x = 1\\n"
d3 = native.sha256(empty)
native.approve_hash(d3)
t = ic.performance_counter(0)
h3 = native.spawn_subinterpreter(empty, d3, "", (), lambda *a: None, sandbox.BUDGET)
parts["spawn of a one-line program"] = ic.performance_counter(0) - t
native.close_subinterpreter(h3)
native.revoke_hash(d3)
parts["sandbox.execute as a whole"] = counted(sandbox.execute, row.source, "nothing", {}, lambda a, k: None,
                                              row.operations)[0]
args = {"extension": "bench", "function": "nothing", "args": {}}
parts["extension_call: Cedar decision (auth.authorize)"] = counted(auth.authorize, ALICE, ALICE, "extension_call",
                                                                   ops()["extension_call"], args)[0]
parts["extension_call: audit Event (core.op.audit)"] = counted(audit, "extension_call", ALICE, ALICE, "user", args,
                                                               {"result": 1})[0]
parts["extension_call, whole (3 runs)"] = calls
print(json.dumps(parts))
'''


def test_1_what_a_call_that_does_nothing_costs(realm, people, costs):
    parts = shell(realm, people, BREAKDOWN)
    whole = min(parts["extension_call, whole (3 runs)"])
    parts["extension_call, whole (least of 3)"] = whole
    parts["extension_call outside the sandbox (whole - execute)"] = whole - parts["sandbox.execute as a whole"]
    report(costs, "1 nothing()", parts)
    assert whole < 400_000_000


PER_RPC = '''
for fn, extra in (("reads", {"proposal": P}), ("renames", {}), ("proposes", {})):
    ext(ALICE, "bench", fn, n=1, **extra)  # once before timing
out = {"nothing": timed(ALICE, "bench", "nothing")}
for fn, ns, extra in (("reads", (1, 10, 50), {"proposal": P}), ("renames", (1, 10, 50), {}), ("proposes", (1, 10), {})):
    for n in ns:
        out[f"{fn} {n}"] = timed(ALICE, "bench", fn, n=n, **extra)

def direct(name, args, n=10):
    """``name`` dispatched ``n`` times as an extension's rpc dispatches it (api/extensions.py:285), without a sandbox."""
    t = ic.performance_counter(0)
    for _ in range(n):
        reply = json.loads(run(dispatch(name, json.dumps(args), origin="extension", signer=ALICE, declared=DECLARED,
                                        root="user")))
        assert reply["ok"], reply
    return (ic.performance_counter(0) - t) // n

out["direct proposal_get"] = direct("proposal_get", {"proposal": P, "count": 1})
out["direct profile_update"] = direct("profile_update", {"nickname": "direct"})
out["direct proposal_create"] = direct("proposal_create", {"title": "Direct motion"})
for name, args in (("proposal_get", {"proposal": P, "count": 1}), ("profile_update", {"nickname": "x"}),
                   ("proposal_create", {"title": "x"})):
    out[f"Cedar decision, {name}"] = counted(auth.authorize, ALICE, ALICE, name, ops()[name], args, "extension",
                                             DECLARED)[0]
out["audit Event, profile_update"] = counted(audit, "profile_update", ALICE, ALICE, "extension", {"nickname": "x"},
                                             {"user": ALICE})[0]
out["audit Event, proposal_create"] = counted(audit, "proposal_create", ALICE, ALICE, "extension", {"title": "x"},
                                              {"proposal": "P-9"})[0]
print(json.dumps(out))
'''


def test_2_what_each_call_the_extension_makes_costs(realm, people, costs):
    out = shell(realm, people, PER_RPC)
    for fn, (lo, hi) in (("reads", (1, 50)), ("renames", (1, 50)), ("proposes", (1, 10))):
        out[f"{fn}: one more rpc (slope)"] = (out[f"{fn} {hi}"] - out[f"{fn} {lo}"]) // (hi - lo)
    for fn, op in (("reads", "proposal_get"), ("renames", "profile_update"), ("proposes", "proposal_create")):
        out[f"{op}: the sandbox boundary per rpc (slope - direct)"] = out[f"{fn}: one more rpc (slope)"] - out[f"direct {op}"]
    report(costs, "2 per rpc", out)


BEHAVIOUR = '''
import json as _json
cases = {}
def case(label, operations, user, op, args):
    put("perm", operations, BENCH)
    reply = ext(user, "perm", "one", op=op, args=args)
    result = reply.get("result") if reply.get("ok") else {"extension_call refused": reply.get("error")}
    cases[label] = _json.dumps(result)[:400]
case("no operations, calls a read", [], ALICE, "proposal_get", {"proposal": P, "count": 1})
case("read only, calls the read", ["proposal_get"], ALICE, "proposal_get", {"proposal": P, "count": 1})
case("read only, calls a write it did not declare", ["proposal_get"], ALICE, "profile_update", {"nickname": "z"})
case("write declared, member holds it", ["profile_update"], ALICE, "profile_update", {"nickname": "z"})
case("declared, member lacks the permission", ["governance_update"], ALICE, "governance_update", {"voting_quorum": 0})
case("declared, the controller calls it", ["governance_update"], %(ctl)r, "governance_update", {"voting_quorum": 50000000})
case("an operation that does not exist", ["no_such_op"], ALICE, "no_such_op", {})
case("a visitor who is not a member", ["proposal_get"], %(mallory)r, "proposal_get", {"proposal": P, "count": 1})
everything = sorted(ops())
sizes = {}
for n in (0, 1, 4, 16, 32):
    chosen = (["proposal_get"] + [o for o in everything if o != "proposal_get"])[:n]
    put("perm", chosen, BENCH)
    ext(ALICE, "perm", "nothing")
    sizes[f"nothing() with {n} operations declared"] = min(timed(ALICE, "perm", "nothing") for _ in range(2))
print(json.dumps({"cases": cases, "sizes": sizes, "operations in the realm": len(everything)}))
'''


def test_3_what_each_set_of_operations_lets_it_do(realm, people, costs):
    out = shell(realm, people, BEHAVIOUR % {"ctl": people["ctl"], "mallory": people["mallory"]})
    report(costs, "3 allow-list size", out["sizes"])
    for label, result in out["cases"].items():
        print(f"3 behaviour: {label}: {result}")
    print("3 operations registered in the realm:", out["operations in the realm"])


def test_3b_33_operations_trap_every_call(realm, people):
    """core/sandbox.py:17: the native sandbox traps on an allow-list longer than 32. The install does not refuse one
    (api/extensions.py extension_manifest checks only that ``operations`` is a list of text)."""
    shell(realm, people, 'put("wide", sorted(ops())[:33], BENCH)\nprint(json.dumps({}))')
    try:
        reply = realm.call("extension_call", {"extension": "wide", "function": "nothing"}, sender=people["alice"])
        print("3b 33 operations:", reply)
    except Exception as e:  # noqa: BLE001  the message traps
        print("3b 33 operations: the message traps:", str(e)[:600])


BUDGET_PROBE = '''
out = {}
for n in %(ns)r:
    t = ic.performance_counter(0)
    try:
        sandbox.execute(BENCH, "spin", {"n": n}, lambda a, k: None, ())
        out[f"spin({n})"] = ic.performance_counter(0) - t
    except RuntimeError as e:
        out[f"spin({n}) exhausted"] = ic.performance_counter(0) - t
        out[f"spin({n}) error"] = str(e)[:200]
print(json.dumps(out))
'''


def test_4_the_bytecode_budget(realm, people, costs):
    """How far 10,000,000 bytecode instructions go (core/sandbox.py:16), and what they cost in the canister's own
    instructions. Each probe runs in a message of its own, as one at the budget costs a good part of a message."""
    found = {}
    for ns in ((1_000, 10_000, 100_000), (10**9,), (1_000_000,), (1_500_000,), (2_000_000,), (2_500_000,), (3_000_000,)):
        found.update(shell(realm, people, BUDGET_PROBE % {"ns": ns}))
    report(costs, "4 bytecode budget", found)


RPC_VS_BUDGET = '''
out = {}
for n in (200, 1000):
    t = ic.performance_counter(0)
    try:
        sandbox.execute(BENCH, "reads", {"n": n, "proposal": P},
                        lambda action, kw: json.loads(run(dispatch(action, json.dumps(kw), origin="extension",
                                                                   signer=ALICE, declared=DECLARED, root="user"))),
                        DECLARED)
        out[f"reads({n})"] = ic.performance_counter(0) - t
    except RuntimeError as e:
        out[f"reads({n}) failed"] = ic.performance_counter(0) - t
        out[f"reads({n}) error"] = str(e)[:200]
print(json.dumps(out))
'''


def test_5_whether_the_host_side_of_an_rpc_counts_against_the_budget(realm, people, costs):
    """If the realm's work for an rpc counted against the sandbox's bytecode budget, a thousand reads would spend it."""
    report(costs, "5 rpc vs budget", shell(realm, people, RPC_VS_BUDGET))


def test_6_the_message_limit_for_writes(realm, people, costs):
    """The 40 B instructions an update message may run (the IC's resource limits): how many writes one extension call
    gets through. ``n`` is set from test 2's slope; one call under the limit completes, one over it traps."""
    slope = shell(realm, people, 'ext(ALICE, "bench", "renames", n=1)\n'
                                 'a = timed(ALICE, "bench", "renames", n=1)\nb = timed(ALICE, "bench", "renames", n=21)\n'
                                 'print(json.dumps({"slope": (b - a) // 20}))')["slope"]
    limit = 40_000_000_000
    for share in (0.8, 1.2):
        n = int(limit * share / slope)
        try:
            reply = realm.call("extension_call", {"extension": "bench", "function": "renames", "args": {"n": n}},
                               sender=people["alice"])
            print(f"6 renames({n}), {share:.0%} of the limit by the slope {slope:,}: ok={reply['ok']}")
        except Exception as e:  # noqa: BLE001  the message traps
            print(f"6 renames({n}), {share:.0%} of the limit by the slope {slope:,}: traps: {str(e)[:300]}")
    costs.report("6 renames: one more rpc (slope)", slope)


SIZES = '''
def padded(size):
    lines, i = [BENCH], 0
    while sum(len(x) for x in lines) < size:
        lines.append("def pad_%%d(x):\\n    y = x * %%d\\n    return [y, y + 1, {'k': y}]\\n\\n\\n" %% (i, i))
        i += 1
    return "".join(lines)
out = {}
for size in %(sizes)r:
    source = padded(size)
    put("sized", DECLARED, source)
    ext(ALICE, "sized", "nothing")
    out[f"nothing(), source of {len(source):,} bytes"] = min(timed(ALICE, "sized", "nothing") for _ in range(2))
    out[f"sandbox.execute, source of {len(source):,} bytes"] = counted(sandbox.execute, source, "nothing", {},
                                                                     lambda a, k: None, DECLARED)[0]
print(json.dumps(out))
'''


def test_7_what_the_size_of_the_extension_costs(realm, people, costs):
    found = {}
    for sizes in ((1, 16_000), (64_000,), (256_000,)):
        found.update(shell(realm, people, SIZES % {"sizes": sizes}))
    report(costs, "7 source size", found)


PAYLOAD = '''
out = {}
for size in (1_000, 10_000, 100_000, 1_000_000):
    pad = "x" * size
    out[f"echo, {size:,} characters in and out"] = counted(sandbox.execute, BENCH, "echo", {"payload": pad},
                                                           lambda a, k: None, ())[0]
rpc = lambda action, kw: json.loads(run(dispatch(action, json.dumps(kw), origin="extension", signer=ALICE,
                                                 declared=DECLARED, root="user")))
for size in (0, 1_000, 10_000, 100_000):
    out[f"relay, one rpc carrying {size:,} characters"] = counted(sandbox.execute, BENCH, "relay",
                                                                  {"proposal": P, "pad": "x" * size}, rpc, DECLARED)[0]
print(json.dumps(out))
'''


def test_8_what_data_crossing_the_boundary_costs(realm, people, costs):
    report(costs, "8 payload", shell(realm, people, PAYLOAD))


def attempt(realm, people, function, args=None, extension="bench"):
    """``extension_call`` as a real message from Alice: its reply, or the text of the trap."""
    try:
        reply = realm.call("extension_call", {"extension": extension, "function": function, "args": args or {}},
                           sender=people["alice"])
        return "reply " + json.dumps(reply)[:400]
    except Exception as e:  # noqa: BLE001  the message traps
        return "trap " + str(e)[:400]


FREE = """
handles, err = [], ''
program = 'def f():\\n    return 1\\n'
digest = native.sha256(program)
native.approve_hash(digest)
try:
    while len(handles) < 12:
        handles.append(native.spawn_subinterpreter(program, digest, '', (), lambda *a: None, 1000000))
except Exception as e:
    err = str(e)[:200]
finally:
    for h in handles:
        native.close_subinterpreter(h)
    native.revoke_hash(digest)
print(json.dumps({"free": len(handles), "error": err}))
"""


def test_9_failures_and_nesting(realm, people, costs):
    """What the member sees when the extension fails, and whether the realm keeps working after."""
    before = shell(realm, people, FREE)
    print("9 free subinterpreter handles before:", before)
    print("9 an exception in the extension:", attempt(realm, people, "fails"))
    print("9 a function that does not exist:", attempt(realm, people, "missing"))
    print("9 a loop past the bytecode budget:", attempt(realm, people, "spin", {"n": 10**9}))
    shell(realm, people, 'put("nester", DECLARED + ["extension_call"], BENCH)\nprint(json.dumps({}))')
    nested = {}
    for depth in (1, 2, 4, 6, 7, 8, 10):
        nested[depth] = attempt(realm, people, "nest", {"depth": depth}, extension="nester")
        print(f"9 nest({depth}):", nested[depth])
    timing = shell(realm, people, 'out = {}\nfor d in (0, 1, 2, 4):\n'
                                  '    ext(ALICE, "nester", "nest", depth=d)\n'
                                  '    out[f"nest({d})"] = timed(ALICE, "nester", "nest", depth=d)\n'
                                  'print(json.dumps(out))')
    report(costs, "9 nesting", timing)
    after = shell(realm, people, FREE)
    print("9 free subinterpreter handles after:", after)
    assert after["free"] == before["free"]
    assert ok(realm, "proposal_get", people["alice"], query=True, proposal=realm.proposal)["proposal"]


BALANCE = "print(json.dumps({'balance': ic.canister_balance128()}))"


def test_10_what_a_real_message_is_charged(realm, people, costs):
    """The cycles the replica takes for one extension_call message, from the canister's balance before and after,
    less what the shell calls that read the balance take themselves."""
    def balance():
        return shell(realm, people, BALANCE)["balance"]

    a, b = balance(), balance()
    shells = a - b
    for function, args in (("nothing", {}), ("reads", {"n": 10, "proposal": realm.proposal}), ("renames", {"n": 10})):
        before = balance()
        reply = realm.call("extension_call", {"extension": "bench", "function": function, "args": args},
                           sender=people["alice"])
        assert reply["ok"], reply
        charged = before - balance() - shells
        costs.report(f"10 cycles charged for one extension_call message, {function}", charged)
        print(f"10 {function} {args.get('n', '')}: {charged:,} cycles for the message (shell reads: {shells:,})")


# ---- (b) the study prototype: the extension's own grant, under which it acts as the realm (api.extensions.act)

GRANTED = '''
from api import extensions
from core import chain, it_team
from ggg import Event
REALM = chain.self_id()
CTL = %(ctl)r
put("realmext", DECLARED + ["department_create", "sync_source_add"], BENCH)

def granting(operations):
    return call("extension_grant", {"extension": "realmext", "operations": operations}, user=CTL)

def act(function, user=CTL, **args):
    return call("extension_act", {"extension": "realmext", "function": function, "args": args}, user=user)

def last_event(op):
    """The newest audit event of ``op``: the inner call's, which is logged before the extension_act around it."""
    i = Event.max_id()
    while Event[str(i)].op != op:
        i -= 1
    e = Event[str(i)]
    return {k: getattr(e, k) for k in ("op", "user", "by", "origin", "grant")}

cases = {}
cases["grant an operation it does not declare"] = granting(["proposal_vote"])
cases["grant an operation that takes the realm's vote"] = granting(["sync_source_add"])
cases["act before any grant"] = act("nothing")
cases["grant three"] = granting(["proposal_get", "proposal_create", "department_create"])
cases["the grant on the row"] = Extension["realmext"].grant
cases["act: a granted read"] = act("one", op="proposal_get", args={"proposal": P, "count": 1})["result"]["reply"]["ok"]
filed = act("one", op="proposal_create", args={"title": "Filed by the realm"})
cases["act: a granted write"] = filed
cases["act: its audit Event"] = last_event("proposal_create")
cases["act: declared, not granted"] = act("one", op="profile_update", args={"nickname": "x"})
cases["act: a governed operation"] = act("one", op="department_create", args={"department": "Bench office"})
cases["act: its audit Event (department_create)"] = last_event("department_create")
cases["act asked by a member"] = act("nothing", user=ALICE)
cases["the same extension called by a member, as herself"] = ext(ALICE, "realmext", "one", op="proposal_create",
                                                                 args={"title": "Filed by Alice"})
cases["revoke"] = granting([])
cases["act after the revoke"] = act("nothing")
print(json.dumps(cases))
'''


def test_11_what_a_grant_lets_the_extension_do(realm, people):
    out = shell(realm, people, GRANTED % {"ctl": people["ctl"]})
    for label, result in out.items():
        print(f"11 behaviour: {label}: {json.dumps(result)[:400]}")


GRANTED_COST = '''
from core import chain, it_team
from core.op import acted
from ggg import Event
REALM = chain.self_id()
CTL = %(ctl)r
GRANT = ["proposal_get", "proposal_create", "department_create"]
put("realmext", DECLARED + ["department_create"], BENCH)
row = Extension["realmext"]
raw = lambda r: len(r.db()._db_storage.get(f"{r._type}@{r._id}"))
out = {"Extension row, bytes, without a grant": raw(row)}
row.grant = GRANT
out["Extension row, bytes, with a grant of 3"] = raw(row)

def act(function, **args):
    used, reply = counted(call, "extension_act", {"extension": "realmext", "function": function, "args": args}, user=CTL)
    assert reply["ok"], reply
    return used

for fn, extra in (("nothing", {}), ("reads", {"n": 1, "proposal": P}), ("proposes", {"n": 1})):
    act(fn, **extra)  # once before timing
out["act nothing (3 runs)"] = [act("nothing") for _ in range(3)]
for fn, ns, extra in (("reads", (1, 10, 50), {"proposal": P}), ("proposes", (1, 10), {})):
    for n in ns:
        out[f"act {fn} {n}"] = act(fn, n=n, **extra)

def direct(name, args, n=10):
    t = ic.performance_counter(0)
    for _ in range(n):
        reply = json.loads(run(dispatch(name, json.dumps(args), origin="granted", signer=REALM, declared=GRANT,
                                        root="user", extension="realmext")))
        assert reply["ok"], reply
    return (ic.performance_counter(0) - t) // n

out["direct as the realm, proposal_get"] = direct("proposal_get", {"proposal": P, "count": 1})
out["direct as the realm, proposal_create"] = direct("proposal_create", {"title": "Direct realm motion"})
for who, user, origin, declared in (("the realm", REALM, "granted", GRANT), ("Alice", ALICE, "extension", DECLARED)):
    for name, args in (("proposal_get", {"proposal": P, "count": 1}), ("proposal_create", {"title": "x"})):
        out[f"Cedar decision, {who}, {name}"] = counted(auth.authorize, user, user, name, ops()[name], args, origin,
                                                        declared)[0]
    out[f"permissions(), {who}"] = counted(auth.permissions, user)[0]
out["it_team.refuse_change, proposal_create"] = counted(it_team.refuse_change, "proposal_create", {"title": "x"})[0]
out["audit Event, as the realm under the grant"] = counted(acted, "proposal_create", REALM, REALM, "granted",
                                                           {"title": "x"}, {"proposal": "P-9"}, "", None, "realmext")[0]
out["audit Event row, bytes, as the realm"] = raw(Event[str(Event.max_id())])
out["audit Event, as Alice"] = counted(acted, "proposal_create", ALICE, ALICE, "extension", {"title": "x"},
                                       {"proposal": "P-9"}, "")[0]
out["audit Event row, bytes, as Alice"] = raw(Event[str(Event.max_id())])
print(json.dumps(out))
'''


def test_12_what_the_grant_costs(realm, people, costs):
    out = shell(realm, people, GRANTED_COST % {"ctl": people["ctl"]})
    out["act nothing (least of 3)"] = min(out["act nothing (3 runs)"])
    for fn, (lo, hi) in (("reads", (1, 50)), ("proposes", (1, 10))):
        out[f"act {fn}: one more rpc (slope)"] = (out[f"act {fn} {hi}"] - out[f"act {fn} {lo}"]) // (hi - lo)
    report(costs, "12 granted", out)
