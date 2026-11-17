"""What a quarter's hourly catch-up sync costs each side (api.quarters.catch_up), on the real production wasm.

A capital and one real quarter share a PocketIC instance; the capital's directory grows by quarters nobody runs, and its
zones are those of a city. The catch-up is run the way the timer runs it, and costs what the replica takes from each
canister's cycles while it does (its fees included): about an instruction a cycle.

An idle federation costs next to nothing: a sync where nothing changed costs the same whatever the number of quarters and
zones, and a sync after one change costs what that change is, not what the federation is. Two federations are measured:

- ``static``: the other quarters are rows that never change, a lower bound.
- ``live``: they are running quarters, active and compliant, with a population and a census of their zones, and before each
  sampled sync every one of them reports to the capital, as its own catch-up would an hour on (``quarters.record``): its
  ``last_sync`` moves, and its population by none, one or two members (a quarter's population is told to the quarters only
  once it has moved by a step). ``live, 106 zones each`` gives each a census of every zone of the city. ``churning`` is the
  same with a population that moves by more than a step each hour: every other quarter has a row the quarter must take at
  every sync, which is what a change costs, not a floor.

``REALM_SYNC_COST_OUT`` names a JSON file for every sample.
"""

import json
import os

import pytest
from conftest import principal
from network import Network

QUARTERS = (1, 26, 103)  # the capital's directory: one quarter, a quarter of the city, all of it
RESIDENTS = 20  # members of the real quarter, each at home in a zone of their own
OUT = os.environ.get("REALM_SYNC_COST_OUT")
IMAGE = "data:image/png;base64," + "A" * 500_000  # a branding image of about the size a realm's logo is
# (zones of the city, how the other quarters are, the zones each reports, how many members its population moves by each hour,
# 0 for none, one or two)
FEDERATIONS = {
    "static, 106 zones": (106, "static", 0, 0),
    "static, 212 zones": (212, "static", 0, 0),
    "live": (106, "live", 3, 0),
    "live, 106 zones each": (106, "live", 106, 0),
    "churning": (106, "live", 3, 60),
}
BATCH = 20  # reports of the other quarters in one message: the replica stops one at 40 billion instructions

# What an unchanged sync may cost, in cycles, at 26 quarters (the catch-up costs about as many instructions as cycles): a
# margin over what it costs on each side now, so that what makes it grow with the federation again, or write what did not
# change, fails here.
UNCHANGED_BUDGET = {"quarter": 45_000_000, "capital": 40_000_000}

DEFINE_ZONES = '''
from api import assets
for z in range(%(first)d, %(last)d):
    assets.define_zone(f"zone-{z}", f"Barri {z}", "barri")
print("{}")
'''
STATIC_QUARTERS = '''
from api import quarters
for n in range(%(first)d, %(last)d + 1):
    quarters.register(f"quarter-{n}", f"Quarter {n}")
print("{}")
'''
RUNNING_QUARTERS = '''
from api import quarters
from ggg import Quarter
for n in range(%(first)d, %(last)d + 1):
    quarters.register(f"quarter-{n}", f"Quarter {n}")
    q = Quarter[f"quarter-{n}"]
    q.status, q.compliance, q.population = "active", '"compliant"', 400 + n
    q.told_population = q.population  # what its row last carried, on a release that keeps it
    q.zones = {f"zone-{z}": 4 for z in range(%(zones)d)}
    q.last_sync = 1
print("{}")
'''
# What the quarters' own catch-ups do on the capital at each hour: the report of each, as the capital receives it.
REPORTS = '''
from api import quarters


class Caller:
    def __init__(self, by):
        self.by = by


for n in range(%(first)d, %(last)d + 1):
    moved = %(churn)d * %(hour)d or sum((n * 31 + h * 17) %% 3 for h in range(1, %(hour)d + 1))
    quarters.record(Caller(f"quarter-{n}"), {"population": 400 + n + moved, "codex_id": "", "codex_version": "",
                                             "zones": {f"zone-{z}": 4 for z in range(%(zones)d)}})
print("{}")
'''
CATCH_UP = '''
from core import chain
from api import quarters
chain.set_timer(0, quarters.catch_up)  # one more link of the chain, which runs once
print("{}")
'''
NOTHING = 'print("{}")'
LAST_SYNC = '''
import json
from ggg import Quarter
print(json.dumps(Quarter[%(cid)r].last_sync))
'''


def balance(realm):
    return realm.pic.get_cycles_balance(realm.id)


def heap(realm, code):
    """Run ``code`` in ``realm``'s heap through the controller's shell; returns the JSON it printed last."""
    out = realm.shell(code)
    assert "Traceback" not in out, out[-4000:]
    return json.loads(out.strip().splitlines()[-1])


class Federation:
    """The capital, the real quarter and the others, as the module's tests find them."""

    def __init__(self, net, capital, quarter, how, census, churn, samples):
        self.net, self.capital, self.quarter = net, capital, quarter
        self.how, self.census, self.churn, self.samples = how, census, churn, samples
        self.held, self.hour = 1, 0  # quarters in the capital's directory, and hours the others have reported

    def grow(self, size):
        """Make the capital's directory ``size`` quarters."""
        if size > self.held:
            code = RUNNING_QUARTERS if self.how == "live" else STATIC_QUARTERS
            heap(self.capital, code % {"first": self.held, "last": size - 1, "zones": self.census})
            self.held = size

    def others_report(self):
        """Each of the other quarters reports, as its catch-up would an hour on."""
        if self.how != "live":
            return
        self.hour += 1
        for first in range(1, self.held, BATCH):
            heap(self.capital, REPORTS % {"first": first, "last": min(first + BATCH, self.held) - 1, "hour": self.hour,
                                          "zones": self.census, "churn": self.churn})

    def sync(self):
        """One run of the real quarter's catch-up, as its timer runs it, after the others reported: the cycles the quarter
        and the capital spend while it does, less what the shell message that arms it costs the quarter."""
        self.others_report()
        before = balance(self.quarter)
        heap(self.quarter, NOTHING)
        shell = before - balance(self.quarter)
        self.net.pic.advance_time(2 * 10**9)  # so that a sync shows
        synced = heap(self.capital, LAST_SYNC % {"cid": self.quarter.cid})
        mine, theirs = balance(self.quarter), balance(self.capital)
        heap(self.quarter, CATCH_UP)
        for _ in range(6):
            self.net.tick()
        spent = {"quarter": mine - balance(self.quarter) - shell, "capital": theirs - balance(self.capital)}
        assert heap(self.capital, LAST_SYNC % {"cid": self.quarter.cid}) > synced, "the catch-up did not reach the capital"
        return spent

    def steady(self):
        """A sync once the quarter has taken everything the capital held: the cost of the repeat, the middle of three."""
        self.sync()
        costs = sorted((self.sync() for _ in range(3)), key=lambda s: s["quarter"] + s["capital"])
        return costs[1]


@pytest.fixture(scope="module", params=list(FEDERATIONS))
def city(request, wasm_of, people, costs):
    zones, how, census, churn = FEDERATIONS[request.param]
    net = Network(wasm_of("production"), people)
    capital, quarter = net.realm("capital"), net.realm("quarter-1", members=())
    net.register_quarters(capital, quarter)
    capital.ok("quarter_configure", "ctl", capacity=2000)  # a test network's quarters take 10 members: the real quarter is full
    heap(capital, DEFINE_ZONES % {"first": 0, "last": zones})
    quarter.ok("quarter_sync", "ctl")
    for i in range(RESIDENTS):  # they live in the zones: the quarter reports how many
        out = quarter.call("realm_join", {"nickname": f"resident {i}", "home_zone": f"zone-{i}"},
                           sender=principal(f"resident-{i}"))
        assert out["ok"], out
    f = Federation(net, capital, quarter, how, census, churn, {})
    yield f, request.param
    if OUT:
        saved = json.loads(open(OUT).read()) if os.path.exists(OUT) else {}
        saved[request.param] = f.samples
        with open(OUT, "w") as out:
            json.dump(saved, out, indent=1)


def report(costs, name, f, what, cost):
    for side in ("quarter", "capital"):
        costs.report(f"hourly sync {what}, {name}: {side}", cost[side])
    f.samples[what] = cost


def test_an_unchanged_sync_costs_the_same_at_every_size_of_the_federation(city, costs):
    f, name = city
    at = {}
    for size in QUARTERS:
        f.grow(size)
        at[size] = f.steady()
        report(costs, name, f, f"unchanged, {size} quarters", at[size])
    if f.churn:  # the others change a row each at every sync: what that costs is the cost of the change
        return
    for side, budget in UNCHANGED_BUDGET.items():
        assert at[26][side] <= budget, f"an unchanged sync costs the {side} {at[26][side]:,} cycles: {at}"
        assert max(c[side] for c in at.values()) < 1.1 * min(c[side] for c in at.values()), at  # the same at every size


def test_a_sync_after_one_change_costs_what_the_change_is(city, costs):
    f, name = city
    if f.how != "static":
        pytest.skip("measured on the idle federation")
    f.grow(QUARTERS[-1])
    size = f.held
    f.capital.ok("zone_define", "ctl", zone="zone-0", name="Renamed", kind="barri")
    report(costs, name, f, f"a zone redefined, {size} quarters", f.sync())
    f.grow(size + 1)
    report(costs, name, f, f"a quarter joined, {size + 1} quarters", f.sync())
    f.quarter.ok("realm_join", principal("one more"), nickname="one more", home_zone="zone-30")
    report(costs, name, f, "a member joined the quarter", f.sync())


def test_an_unchanged_sync_with_branding_images_in_both_realms(city, costs):
    """The images are in the realm's row, which every message that reads the realm decodes: the floor of a realm that
    has a logo."""
    f, name = city
    if name != "static, 106 zones":
        pytest.skip("measured once")
    f.grow(QUARTERS[-1])
    for realm in (f.capital, f.quarter):
        heap(realm, f"from ggg import Realm\nr = Realm['1']\nr.logo_url = {IMAGE!r}\nr.background_image_url = {IMAGE!r}\n"
                    'print("{}")')
    f.sync()  # the quarter takes the images
    f.sync()
    report(costs, name, f, "unchanged, with two 500 KB images in the realms", f.steady())
