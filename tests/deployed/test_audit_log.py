"""The audit log on the real wasm (realms-gos#672, finding F2): once it holds EVENTS_KEPT events, every update that records
one drops the oldest, and that must cost an update next to nothing. It cost about 20 M instructions, as much as the
cheapest updates (Entity.delete looks through the relations of all 93 entity classes, and the row is loaded first):
profile_update was 26 M at 1,990 events and 47 M at 2,030.

The upgrade runs from the previous release's own wasm, named by ``REALM_PREVIOUS_WASM``, or from this release's when it
is unset, which CI can always build: the rows of the log are the same in both.
"""

import json
import os

import pytest
from pic import CONTROLLER, Canister
from test_delegation_groups import launch, ok

PREVIOUS = os.environ.get("REALM_PREVIOUS_WASM")
KEPT = 2000
BELOW, ABOVE, FAR_ABOVE = 1900, 2030, 3020  # events recorded when an update is measured
# What an update may cost more in a full log than in one that is not, in instructions. Measured: profile_update is
# 26.2 M at 1,990 events, 28.1 M at 2,030 and 28.2 M at 3,020, and dropping an event (Event.forget) is 1.7 M. Entity.delete
# made it 47 M.
MARGIN = 3_000_000
FILL_BATCH = 500  # events one shell message records: it may spend 40 B instructions, an event costs about 15 M

FILL = """
from core import op
for i in range({count}):
    op.audit("profile_update", "{user}", "{user}", "user", {{"nickname": "Ali"}}, {{}}, grant="{grant}")
"""
SPENT = """
import json
from core.op import call
spent = []
for i in range(5):
    start = ic.performance_counter(0)
    out = call("profile_update", {{"nickname": "Ali" + str(i)}}, user="{user}")
    spent.append(ic.performance_counter(0) - start)
    assert out["ok"], out
print(json.dumps(sorted(spent)[2]))  # the median: the first call of a message pays for what the others find loaded
"""
FORGET = """
import json
from core import op
from ggg import Event
start = ic.performance_counter(0)
Event.forget(str(Event.max_id() - op.EVENTS_KEPT + 1))
print(json.dumps(ic.performance_counter(0) - start))
"""
HELD = """
import json
from ggg import Event
print(json.dumps({{"recorded": Event.max_id(), "held": Event.count(), "granted": Event.count_by("grant", "D-1")}}))
"""
LEFT = """
import json
from ic_python_db import Database
from ggg import Event
db = Database.get_instance()
print(json.dumps([i for i in range({first}, {last}) if db.load("Event", str(i)) or db.load(Event._alias_key(), str(i))]))
"""


def shell(realm, code, **names):
    out = realm.shell(code.format(**names))
    assert "Traceback" not in out, out
    return json.loads(out.strip().splitlines()[-1]) if out.strip() else None


def held(realm):
    return shell(realm, HELD)


def record_until(realm, recorded, user, grant=""):
    """Record events, by the audit log's own function, until ``recorded`` have been."""
    while (missing := recorded - held(realm)["recorded"]) > 0:
        shell(realm, FILL, count=min(missing, FILL_BATCH), user=user, grant=grant)


def update_cost(realm, user):
    return shell(realm, SPENT, user=user)


def dropped_remain(realm, up_to):
    """The ids below ``up_to``, which the log has dropped, of which a row or an alias is left."""
    return shell(realm, LEFT, first=1, last=up_to)


def events_newest_first(realm):
    """Every event the ``events`` operation lists, a page at a time."""
    listed, start = [], None
    while True:
        page = ok(realm, "events", CONTROLLER, query=True, count=500, **({"from": start} if start else {}))
        listed += page["events"]
        if not page["next"]:
            return listed
        start = page["next"]


@pytest.fixture(scope="module")
def realm(wasm_of, people):
    """A launched realm of this release whose audit log is filled up in steps, the cost of an update measured on the way."""
    realm = Canister(wasm_of("production"), installer=people["installer"])
    launch(realm, people)
    realm.cost = {}
    for recorded in (BELOW, ABOVE, FAR_ABOVE):
        record_until(realm, recorded, people["alice"])
        realm.cost[recorded] = update_cost(realm, people["alice"])
    return realm


def test_an_update_costs_the_same_in_a_full_log_as_in_one_that_is_not(realm, costs):
    for recorded, spent in realm.cost.items():
        costs.report(f"profile_update with {recorded:,} events recorded", spent)
    assert realm.cost[ABOVE] <= realm.cost[BELOW] + MARGIN, realm.cost
    assert realm.cost[FAR_ABOVE] <= realm.cost[BELOW] + MARGIN, realm.cost


def test_the_log_holds_its_last_events_in_order_and_nothing_of_the_older_ones(realm):
    state = held(realm)
    listed = events_newest_first(realm)
    assert state["held"] == KEPT
    assert [int(e["id"]) for e in listed] == list(range(state["recorded"], state["recorded"] - KEPT, -1))
    assert listed[0]["op"] == "profile_update" and listed[0]["at"] >= listed[-1]["at"]
    assert not dropped_remain(realm, state["recorded"] - KEPT + 1)  # no row, no alias of an event that was dropped


def test_dropping_an_event_from_a_full_log_costs_a_fraction_of_an_update(realm, costs):
    spent = shell(realm, FORGET)  # the last test of the realm: the log is one event short after it
    costs.report("an event dropped from a full audit log (Event.forget)", spent)
    assert spent < MARGIN


@pytest.fixture(scope="module")
def upgraded(wasm_of, people):
    """A realm of the previous release whose log is full, the oldest rows of it three events of a grant, and the cost of an
    update before it was; then upgraded to this release, which drops those rows as the log moves on."""
    realm = Canister(PREVIOUS or wasm_of("production"), installer=people["installer"])
    launch(realm, people)
    alice = people["alice"]
    shell(realm, FILL, count=3, user=alice, grant="D-1")
    oldest = held(realm)["recorded"] - 2
    record_until(realm, BELOW, alice)
    realm.before = update_cost(realm, alice)
    record_until(realm, oldest + KEPT - 1, alice)
    realm.state = held(realm)
    realm.wait(600)  # PocketIC refuses an upgrade right after the install of a wasm this big
    realm.upgrade(wasm_of("production"))
    return realm


def test_the_log_of_the_previous_release_is_read_as_before_after_the_upgrade(upgraded):
    before = upgraded.state
    assert before["held"] == KEPT and before["granted"] == 3
    listed = events_newest_first(upgraded)
    assert [int(e["id"]) for e in listed] == list(range(before["recorded"], before["recorded"] - KEPT, -1))
    assert [e["grant"] for e in listed[-3:]] == ["D-1"] * 3


def test_updates_after_the_upgrade_drop_its_events_cheaply_and_leave_nothing_of_them(upgraded, people, costs):
    spent = update_cost(upgraded, people["alice"])  # five updates: the three events of the grant and two more are dropped
    costs.report("profile_update in a full log, after an upgrade", spent)
    assert spent <= upgraded.before + MARGIN, (spent, upgraded.before)
    state = held(upgraded)
    assert state["held"] == KEPT and state["granted"] == 0
    assert not dropped_remain(upgraded, state["recorded"] - KEPT + 1)
