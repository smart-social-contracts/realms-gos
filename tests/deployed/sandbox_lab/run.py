"""Run the lab extension (backend/entry.py, with the operations manifest.json declares) in a realm on PocketIC.

    ./run.sh                      count(n) for the two n either side of the limit
    ./run.sh rename 1000 2000     any function of backend/entry.py, once for each n
    ./run.sh --find rename        the largest n that still runs, and what stops n + 1
    ./run.sh --repack ...         rebuild the realm's wasm first (only needed after a change under src/)

Each run starts a fresh realm (Alice and Bob are members, Bob's proposal P-1 is open), installs the extension as its
row, then calls ``extension_call`` as Alice once for each n, one after another on that realm. The call is made from
inside the canister, through the controller's shell, so that it can count the instructions it spends
(``ic.performance_counter``); a message from Alice's browser runs the same code.
"""

import argparse
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEPLOYED = os.path.dirname(HERE)
sys.path.insert(0, DEPLOYED)

from conftest import principal  # noqa: E402  (it puts scripts/ on the path for pack_realm_backend)
from pack_realm_backend import pack  # noqa: E402
from pic import CONTROLLER, Canister  # noqa: E402
from test_delegation_groups import launch  # noqa: E402

CACHE = os.path.join(HERE, ".cache")
WASM = os.path.join(CACHE, "realm_backend.wasm")
MESSAGE_LIMIT = 40_000_000_000  # instructions an update message may run (the Internet Computer's resource limits)
# count(n) of the backend/entry.py this lab ships with: the largest n that runs, and the next, which the budget stops.
DEFAULT_N = (1_666_633, 1_666_634)

INSTALL = """
import json
from ggg import Extension
manifest, source = json.loads({manifest!r}), {source!r}
row = Extension["lab"] or Extension(id="lab")
row.version, row.manifest, row.source = manifest.get("version", ""), manifest, source
row.operations = list(manifest.get("operations", []))
row.access = {{"profiles": [], "departments": []}}  # every member may call it
print("installed")
"""
CALL = """
import json
from core.op import call
start = ic.performance_counter(0)
reply = call("extension_call", {{"extension": "lab", "function": {function!r}, "args": {{"n": {n}}}}}, user={alice!r})
print(json.dumps({{"instructions": ic.performance_counter(0) - start, "reply": reply}}))
"""


def wasm(repack):
    """The realm's production wasm, packed once and kept in .cache: the extension is installed at run time, so a change
    to backend/entry.py or manifest.json needs no new wasm."""
    if repack or not os.path.exists(WASM):
        print("Packing the realm's wasm (about half a minute)...", flush=True)
        os.makedirs(CACHE, exist_ok=True)
        shutil.copy(pack(variant="production"), WASM)
    return WASM


def realm_with_lab(repack):
    people = {n: principal(n) for n in ("founder", "alice", "bob", "installer")} | {"ctl": CONTROLLER}
    realm = Canister(wasm(repack), installer=people["installer"])
    launch(realm, people)
    with open(os.path.join(HERE, "manifest.json")) as f:
        manifest = f.read()
    with open(os.path.join(HERE, "backend", "entry.py")) as f:
        source = f.read()
    out = realm.shell(INSTALL.format(manifest=manifest, source=source))
    if "installed" not in out:
        sys.exit(f"the extension did not install:\n{out}")
    operations = json.loads(manifest).get("operations", [])
    print(f"Realm ready; lab installed: {len(source):,} bytes of backend, operations {operations}", flush=True)
    return realm, people["alice"]


def why_stopped(error):
    """What the trap's text says stopped the call, in plain words."""
    if "instruction budget (" in error:
        return "the sandbox's budget of 10,000,000 bytecode instructions ran out (core/sandbox.py BUDGET)"
    if "exceeded the limit of" in error and "instructions" in error:
        return f"the message reached the Internet Computer's limit of {MESSAGE_LIMIT:,} instructions"
    marker = "with message: '"
    if marker in error:
        return "the call trapped: " + error.split(marker, 1)[1].split("'.", 1)[0][:300]
    return "the call failed: " + error[:300]


def attempt(realm, alice, function, n):
    """One call of ``function(n=n)``: (True, instructions, result) when it runs, (False, None, why) when it does not."""
    try:
        out = realm.shell(CALL.format(function=function, n=n, alice=alice))
    except Exception as e:  # noqa: BLE001  the message trapped: the extension, the sandbox or the replica stopped it
        return False, None, why_stopped(str(e))
    if "Traceback" in out:
        return False, None, "the call raised in the canister:\n" + out[-1500:]
    measured = json.loads(out.strip().splitlines()[-1])
    reply = measured["reply"]
    if not reply.get("ok"):
        return False, None, "extension_call refused it: " + str(reply.get("error"))
    return True, measured["instructions"], reply.get("result")


def show(function, n, outcome):
    ran, instructions, detail = outcome
    head = f"{function}(n={n:,})"
    if ran:
        share = instructions / MESSAGE_LIMIT
        result = json.dumps(detail)
        print(f"{head:<24} runs     {instructions:>15,} instructions ({share:.1%} of a message)   result: "
              f"{result[:60]}{'...' if len(result) > 60 else ''}", flush=True)
    else:
        print(f"{head:<24} STOPPED  {detail}", flush=True)
    return ran


def find(realm, alice, function, start=1):
    """The largest n that runs: double n until a call is stopped, then halve the gap between the two."""
    good, bad, n = 0, None, start
    while bad is None:
        if show(function, n, attempt(realm, alice, function, n)):
            good, n = n, n * 2
        else:
            bad = n
    while bad - good > 1:
        mid = (good + bad) // 2
        if show(function, mid, attempt(realm, alice, function, mid)):
            good = mid
        else:
            bad = mid
    print(f"\nLargest n that runs: {good:,}. Calls for n = {bad:,} are stopped.")
    if good:
        print("Both again, on this realm as it is now:")
        show(function, good, attempt(realm, alice, function, good))
    show(function, bad, attempt(realm, alice, function, bad))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("function", nargs="?", default="count", help="a function of backend/entry.py (default count)")
    parser.add_argument("n", nargs="*", type=int, help="the n to call it with, in order")
    parser.add_argument("--find", action="store_true", help="search for the largest n that still runs")
    parser.add_argument("--repack", action="store_true", help="rebuild the realm's wasm from src/ first")
    args = parser.parse_args()
    began = time.time()
    realm, alice = realm_with_lab(args.repack)
    if args.find:
        find(realm, alice, args.function)
    else:
        ns = args.n or (list(DEFAULT_N) if args.function == "count" else [10])
        for n in ns:
            show(args.function, n, attempt(realm, alice, args.function, n))
    print(f"\n({time.time() - began:.0f} s)")


if __name__ == "__main__":
    main()
