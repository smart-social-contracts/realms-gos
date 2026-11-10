"""One quarter of a city's federation at full size, on the real wasm: what a year of the city's own activity costs it
(València phase 0, realms-gos#564).

A capital and one quarter share a PocketIC instance, both under a layered codex with Spain's IBI and the city's bonus
for large families. The quarter is filled with ``n`` members, each with a home district, an identity proof and an
assessed parcel, and its residents stay idle: the city drives the load. It bills two taxes over everyone in pages, takes
the payments of most bills, sends the capital a transfer each month and is upgraded; the capital carries the Junta de
Govern Local, one proposal per item with ten votes each. Its reads are checked as queries along the way.

Opt-in, since a quarter of 8,000 takes about half an hour: ``REALM_BENCH_MEMBERS=2000,8000`` runs a quarter of
each size, and the module is skipped without it. ``REALM_BENCH_JGL_ITEMS`` (default 3,600, a year) sets the Junta's
items, ``REALM_BENCH_TAX_PAGE`` (default 250) a tax run's page, and ``REALM_BENCH_OUT`` names a JSON file for every
sample.

Two measures. The bulk runs in the heap, through the controller's ``__shell__`` in batches, and ``performance_counter``
times each operation: no message overhead, and a row the batch has already read is cheaper. A sample of each operation
also runs as a real call, which the replica charges: its cycles, less the fees of a message, are its instructions.
"""

import json
import os
import statistics

import mock_ledger
import pytest
from conftest import principal
from ic.candid import Types, decode, encode
from ic.principal import Principal
from mock_ledger import idl_hash
from network import Network
from pic import CONTROLLER, _management
from test_legislation_runtime import IBI, TOWN, VALENCIA_TAX, install

SIZES = [int(n) for n in os.environ.get("REALM_BENCH_MEMBERS", "").split(",") if n.strip()]
JGL_ITEMS = int(os.environ.get("REALM_BENCH_JGL_ITEMS", "3600"))
TAX_PAGE = int(os.environ.get("REALM_BENCH_TAX_PAGE", "250"))
OUT = os.environ.get("REALM_BENCH_OUT")
pytestmark = pytest.mark.skipif(not SIZES, reason="REALM_BENCH_MEMBERS names no quarter size: an opt-in benchmark")

# The fees of an update message on a 13-node subnet (docs.internetcomputer.org/references/cycle-costs).
EXECUTION_FEE, INGRESS_FEE, INGRESS_BYTE_FEE = 5_000_000, 1_200_000, 2_000
QUERY_LIMIT = 5 * 10**9
# Members per shell message. A parcel's registration reads every parcel of the cells around it, so it grows with how
# full they are: twenty stay well inside a message's 40 B at 8,000 members.
BATCH = 20
PAY_BATCH = 200  # invoices booked per shell message
SAMPLES = 8  # points of each scaling curve
IMPORT = 200  # members registered by one user_import, the bulk way to seed
DISTRICTS = 106  # València's 19 districts and 87 barris, as zones
LARGE_FAMILY_EVERY = 30  # about 3 % of households hold a large-family title, which the city's IBI chapter reads
UNIT = 10**8  # DOM has 8 decimals
PARCEL_VALUE = 60_000 * UNIT
WASTE_FEE = {"code": "waste", "kind": "tax", "subject": "User", "amount": 95 * UNIT, "facts": {}}
UNPAID = "0"  # the invoices whose number ends with it stay unpaid within the year: a tenth
MONTHS = 12
JGL_SEATS = 10
PUBLIC_READS = (("realm_info", {}), ("status", {}), ("quarter_directory", {}), ("scale_status", {}),
                ("user_list", {"from": 1, "count": 50}), ("land_map", {"from": 1, "count": 200}),
                ("codex_status", {}), ("journal_issue", {}))

# Spain's IBI, as test_legislation_runtime's chapter computes it, except that a tax it does not define is left to the
# realm's own charge (None) rather than billed at nothing: the city's waste fee is such a tax.
ES_TAX = '''
from realms.legislation import above, fixed, overridable

class Tax(above("tax")):
    @fixed(legislation="TRLRHL Arts. 61, 65, 71 and 74")
    def compute_tax_assess(self, entry, subject):
        if entry.code != "ibi_urban":
            return None
        gross = subject.assessed_value.times(entry.rate)
        net = gross - gross.times(self.ibi_bonus(subject))
        items = [{"concept": entry.code, "amount": gross, "legislation": "TRLRHL Art. 71.1"}]
        if net < gross:
            items.append({"concept": entry.code + "_bonus", "amount": net - gross, "legislation": "TRLRHL Art. 74"})
        return items

    @overridable(legislation="TRLRHL Art. 74")
    def ibi_bonus(self, land):
        return 0
'''
# What every batch runs first. The codex's sandbox dies after a few thousand calls, and the realm does not load it again:
# core.legislation._call reloads it only on an error that names BudgetExceeded, which the sandbox's last error does not.
# ``call`` warms it again and asks once more, counting each time, so that the rest of a year can be measured.
HELPERS = '''
import json
from core import legislation
from core.op import call as _call
from ggg import LayeredCodex
revived = 0
def dead(out):
    return "sandbox terminated" in str(out.get("error", ""))
def call(name, args, user):
    global revived
    out = _call(name, args, user=user)
    if dead(out):
        revived += 1
        legislation.warm(LayeredCodex[legislation._state["codex"]])
        out = _call(name, args, user=user)
    return out
'''
WARM = HELPERS + '''
legislation.warm(LayeredCodex[legislation._state["codex"]])
print(json.dumps({"revived": 1}))
'''
SEED = HELPERS + '''
spent = {}
def timed(what, name, args, user):
    t = ic.performance_counter(0)
    out = call(name, args, user=user)
    spent.setdefault(what, []).append(ic.performance_counter(0) - t)
    assert out.get("ok"), (name, args, out)
    return out
for i, user, zone, titled in %(members)r:
    timed("realm_join", "realm_join", {"nickname": f"resident {i}", "home_zone": zone}, user)
    timed("identity_submit", "identity_submit", {"kind": "national_id", "issuer": "ES", "ref": f"vault:id-{i}"}, user)
    land = timed("land_register", "land_register", {"owner": user, "x": i %% 1000, "y": i // 1000}, %(ctl)r)["land"]
    timed("land_assess", "land_assess", {"land": land, "value": %(value)d, "area": 90, "reference": f"CAT-{i}"}, %(ctl)r)
    if titled:
        title = timed("attribute_submit", "attribute_submit", {"kind": "large_family", "value": "general", "ref": f"vault:lf-{i}"}, user)
        timed("attribute_review", "attribute_review", {"attribute": title["attribute"], "status": "verified"}, %(ctl)r)
print(json.dumps({"spent": spent, "message": ic.performance_counter(0), "revived": revived}))
'''
CALLS = HELPERS + '''
from core.op import dispatch, run
spent = []
for name, args, user, query in %(calls)r:
    t = ic.performance_counter(0)
    out = json.loads(run(dispatch(name, json.dumps(args), signer=user, query=True))) if query else call(name, args, user=user)
    spent.append(ic.performance_counter(0) - t)
    assert out.get("ok"), (name, args, out)
print(json.dumps({"spent": spent, "message": ic.performance_counter(0), "last": out, "revived": revived}))
'''
PAY = '''
import json
from api import treasury
from ggg import Invoice
spent = []
for invoice in Invoice.load_some(%(start)d, %(count)d):
    if invoice.status == "pending" and not invoice.id.endswith(%(unpaid)r):
        t = ic.performance_counter(0)
        treasury.receive(invoice, invoice.amount)
        spent.append(ic.performance_counter(0) - t)
print(json.dumps({"spent": spent, "message": ic.performance_counter(0)}))
'''
JGL_ITEM = HELPERS + '''
spent = {"proposal_create": [], "proposal_vote": []}
for n in range(%(count)d):
    t = ic.performance_counter(0)
    out = call("proposal_create", {"title": f"JGL item {n}", "department": "JGL"}, user=%(seats)r[0])
    spent["proposal_create"].append(ic.performance_counter(0) - t)
    assert out.get("ok"), out
    for seat in %(seats)r:
        t = ic.performance_counter(0)
        vote = call("proposal_vote", {"proposal": out["proposal"], "choice": "yes"}, user=seat)
        spent["proposal_vote"].append(ic.performance_counter(0) - t)
        assert vote.get("ok"), vote
print(json.dumps({"spent": spent, "message": ic.performance_counter(0), "revived": revived}))
'''
# What post_upgrade runs, timed in the heap: the part of an upgrade that grows with the realm's data. The replica also
# charges compiling the wasm, which does not.
START = '''
import json
from api import setup
t = ic.performance_counter(0)
setup.start()
print(json.dumps({"spent": ic.performance_counter(0) - t}))
'''
# One parcel's IBI as the codex computes it, apart from the writes of the invoice (test_legislation_runtime's COMPUTE).
COMPUTE = '''
import json
from core import legislation
spent = []
for subject in %(subjects)r:
    t = ic.performance_counter(0)
    legislation.compute("tax_assess", decimals=8, entry=%(entry)r, subject=subject)
    spent.append(ic.performance_counter(0) - t)
print(json.dumps({"spent": spent}))
'''
# Every proposal arms a timer for its deadline (api.proposals.arm), which fires once the vote has closed: what it does.
EXPIRE = '''
import json
from api import proposals
from core.op import run
from ggg import Proposal
t = ic.performance_counter(0)
run(proposals.expire(f"P-{Proposal.max_id()}"))
print(json.dumps({"spent": ic.performance_counter(0) - t}))
'''
MEMORY = '''
import json
print(json.dumps({"stable": ic.stable64_size() * 65536}))
'''
_STATUS = Types.Record({"canister_id": Types.Principal})


class Bench:
    """Where a size's samples go: the session's cost table (conftest.py), and every sample for ``REALM_BENCH_OUT``."""

    def __init__(self, costs, size):
        self.costs, self.size, self.samples = costs, size, {}

    def report(self, what, *counts):
        for count in counts:
            self.costs.report(f"[{self.size}] {what}", count)
            self.samples.setdefault(what, []).append(count)

    def point(self, curve, members, count):
        """One point of a scaling curve: what ``curve`` cost with ``members`` in the quarter."""
        self.samples.setdefault(f"curve: {curve}", []).append([members, count])

    def summary(self):
        return {what: {"n": len(v), "p50": statistics.median(v), "max": max(v), "sum": sum(v)} if not isinstance(v[0], list)
                else v for what, v in self.samples.items()}


def heap(realm, code, bench):
    """Run ``code`` in ``realm``'s heap through the controller's shell and return the JSON it printed last."""
    out = realm.shell(code)
    assert "Traceback" not in out, out[-4000:]
    got = json.loads(out.strip().splitlines()[-1])
    if got.get("revived"):
        bench.report("codex sandbox died and was warmed again (times)", got["revived"])
    return got


def charged(realm, op, args, who, bench):
    """Run ``op`` as a real update call; returns its reply and the instructions the replica charged it for: its cycles
    less the fees of the message. A call that calls another canister also pays that call's fees, which count here."""
    payload = encode([{"type": Types.Text, "value": op}, {"type": Types.Text, "value": json.dumps(args)}])
    before = realm.pic.get_cycles_balance(realm.id)
    out = realm.call(op, args, sender=who)
    cycles = before - realm.pic.get_cycles_balance(realm.id)
    if "sandbox terminated" in out.get("error", ""):  # see HELPERS
        heap(realm, WARM, bench)
        return charged(realm, op, args, who, bench)
    assert out["ok"], (op, args, out)
    return out, cycles - EXECUTION_FEE - INGRESS_FEE - INGRESS_BYTE_FEE * len(payload)


def queried(realm, op, args, who, bench):
    """Read ``op`` as a real query (which fails past a query's instruction limit) and, in the heap, count it."""
    reply = realm.call(op, args, sender=who, query=True)
    assert reply["ok"], (op, reply)
    [count] = heap(realm, CALLS % {"calls": [(op, args, who, True)]}, bench)["spent"]
    bench.report(f"read {op} {json.dumps(args)}", count)
    return count


def memory(realm, bench, when):
    """Report the canister's memory, all of which storage is charged for, and the stable memory part of it (bytes)."""
    realm.pic.set_sender(Principal.from_str(CONTROLLER))
    status = decode(bytes(_management(realm.pic, realm.id, "canister_status", _STATUS, {"canister_id": realm.id.bytes})))
    total = status[0]["value"][f"_{idl_hash('memory_size')}"]
    stable = heap(realm, MEMORY, bench)["stable"]
    bench.report(f"memory {when}: canister (bytes)", total)
    bench.report(f"memory {when}: stable (bytes)", stable)


def members(size):
    """The quarter's residents: index, principal, home zone, and whether they hold a large-family title."""
    return [(i, principal(f"resident-{i}"), f"zone-{i % DISTRICTS}", i % LARGE_FAMILY_EVERY == 0) for i in range(size)]


@pytest.fixture(scope="module", params=SIZES or [0])
def city(request, wasm_of, people, costs):
    """A capital with its Junta de Govern Local and a quarter holding nothing yet, on one instance with a test ledger."""
    size, bench = request.param, Bench(costs, request.param)
    net = Network(wasm_of("production"), people)
    ledger = mock_ledger.install(net.pic, 10**15)  # every invoice is paid in full, every transfer goes through
    capital, quarter = net.realm("valencia"), net.realm("quarter-1", members=())
    net.register_quarters(capital, quarter)
    ctl = people["ctl"]
    capital.ok("quarter_configure", "ctl", capacity=max(size, 2000))
    for zone in range(DISTRICTS):
        capital.ok("zone_define", "ctl", zone=f"zone-{zone}", name=f"Barri {zone}", kind="barri")
    quarter.ok("quarter_sync", "ctl")
    for realm in (capital, quarter):
        realm.ok("realm_configure_tokens", "ctl", accounting_currency="DOM", accounting_currency_decimals=8)
        realm.ok("token_register", "ctl", symbol="DOM", ledger=ledger, decimals=8, fee=10_000)
        realm.ok("fund_create", "ctl", fund="ROOT")
        now = realm.now()
        realm.ok("period_open", "ctl", period="FY", starts_at=now - 86400, ends_at=now + 400 * 86400)
        install(realm, [("eu/es", "tax", ES_TAX), (TOWN, "tax", VALENCIA_TAX)], layers=("eu/es", TOWN), calendars=())
    quarter.ok("tax_policy_draft", "ctl", document={"symbol": "DOM"}, taxes=[{**IBI, "rate": 700_000}, WASTE_FEE])
    quarter.ok("tax_policy_propose", "ctl", policy="TAX-POLICY-1")
    yield net, capital, quarter, ctl, size, bench
    if OUT:
        saved = json.loads(open(OUT).read()) if os.path.exists(OUT) else {}
        saved[str(size)] = bench.summary()
        with open(OUT, "w") as f:
            json.dump(saved, f, indent=1)


def test_seeding_the_quarter(city):
    """``n`` residents join, each with a home zone, an identity proof and an assessed parcel; a sample of each operation
    runs as a real call at every eighth of the way, which draws its curve against the quarter's size."""
    net, capital, quarter, ctl, size, bench = city
    everyone = members(size)
    every = max(1, size // SAMPLES)
    messages = []
    for start in range(0, size, BATCH):
        batch = everyone[start:start + BATCH]
        if start % every < BATCH:
            i, user, zone, _ = batch.pop(0)
            sample(quarter, ctl, bench, start, i, user, zone)
        if batch:
            got = heap(quarter, SEED % {"members": batch, "ctl": ctl, "value": PARCEL_VALUE}, bench)
            for what, counts in got["spent"].items():
                bench.report(f"seed {what} (heap)", *counts)
            messages.append(got["message"])
    bench.report(f"seed message of {BATCH} members (heap)", *messages)
    imported = [{"user": principal(f"imported-{i}"), "nickname": f"imported {i}"} for i in range(IMPORT)]
    _, count = charged(quarter, "user_import", {"users": imported}, ctl, bench)
    bench.report(f"user_import of {IMPORT} members (call)", count)
    population = quarter.ok("scale_status", "ctl", query=True)
    bench.report("seed: members", size)
    assert quarter.ok("me", everyone[-1][1], query=True)["user"]["id"] == everyone[-1][1]
    memory(quarter, bench, "after seeding")
    assert population["populations"]


def sample(quarter, ctl, bench, members, i, user, zone):
    """One resident seeded through real calls, each charged: a point of each seeding curve at ``members``."""
    for what, op, args, who in (
            ("realm_join", "realm_join", {"nickname": f"resident {i}", "home_zone": zone}, user),
            ("identity_submit", "identity_submit", {"kind": "national_id", "issuer": "ES", "ref": f"vault:id-{i}"}, user)):
        _, count = charged(quarter, op, args, who, bench)
        bench.report(f"seed {what} (call)", count)
        bench.point(f"{what} (call)", members, count)
    land = charged(quarter, "land_register", {"owner": user, "x": i % 1000, "y": i // 1000}, ctl, bench)
    bench.report("seed land_register (call)", land[1])
    bench.point("land_register (call)", members, land[1])
    _, count = charged(quarter, "land_assess", {"land": land[0]["land"], "value": PARCEL_VALUE, "area": 90,
                                                "reference": f"CAT-{i}"}, ctl, bench)
    bench.report("seed land_assess (call)", count)
    bench.point("land_assess (call)", members, count)


def test_the_reads_of_a_full_quarter_stay_under_a_query_s_limit(city):
    net, capital, quarter, ctl, size, bench = city
    # As the city's administration, the controller here: once a realm has launched only an admin may list the members.
    last_page, widest = ("user_list", {"from": max(1, size - 99), "count": 100}), ("user_list", {"from": 1, "count": 500})
    for op, args in PUBLIC_READS + (last_page, widest):
        assert queried(quarter, op, args, ctl, bench) < QUERY_LIMIT, op
    assert queried(quarter, "me", {}, members(size)[-1][1], bench) < QUERY_LIMIT
    assert queried(capital, "journal_issue", {}, ctl, bench) < QUERY_LIMIT


def test_a_tax_run_over_every_resident_in_pages(city):
    """Two taxes, the IBI on every parcel (the codex computes it, with the bonus) and a flat waste fee on every member:
    one page is ``TAX_PAGE`` parcels and ``TAX_PAGE`` members. The first page is a real call."""
    net, capital, quarter, ctl, size, bench = city
    args = {"period": "2026", "from": 1, "count": TAX_PAGE}
    out, count = charged(quarter, "tax_assess", args, ctl, bench)
    bench.report(f"tax_assess page of {TAX_PAGE} parcels + {TAX_PAGE} members (call)", count)
    bench.report("tax_assess per invoice (call)", count // max(1, out["count"]))
    invoices = out["count"]
    while out["next"]:
        args = {**args, "from": out["next"]}
        got = heap(quarter, CALLS % {"calls": [("tax_assess", args, ctl, False)]}, bench)
        out, [count] = got["last"], got["spent"]
        bench.report(f"tax_assess page of {TAX_PAGE} parcels + {TAX_PAGE} members (heap)", count)
        bench.report("tax_assess per invoice (heap)", count // max(1, out["count"]))
        invoices += out["count"]
    bench.report("tax run: invoices", invoices)
    assert invoices >= 2 * size - 2  # a parcel or a member billed for nothing would be missing, none is
    parcels = [f"Land:L-{n}" for n in range(1, size, max(1, size // SAMPLES))]
    got = heap(quarter, COMPUTE % {"subjects": parcels, "entry": {**IBI, "rate": 700_000}}, bench)
    bench.report("compute_tax_assess of one parcel (heap)", *got["spent"])


def test_most_bills_are_paid_and_the_capital_gets_its_share_every_month(city):
    """Nine in ten invoices are paid: a sample through ``invoice_check``, which reads the ledger, the rest booked
    in the heap as it books them. Then a transfer to the capital each month, standing in for the sweep GGG lacks."""
    net, capital, quarter, ctl, size, bench = city
    for _, resident, _, _ in members(size)[1:1 + SAMPLES]:
        for invoice in quarter.ok("my_invoices", resident, query=True)["invoices"]:
            _, count = charged(quarter, "invoice_check", {"invoice": invoice["id"]}, resident, bench)
            bench.report("invoice_check, paid (call)", count)
    last = int(quarter.shell("from ggg import Invoice\nprint(Invoice.max_id())").strip())
    for start in range(1, last + 1, PAY_BATCH):
        got = heap(quarter, PAY % {"start": start, "count": PAY_BATCH, "unpaid": UNPAID}, bench)
        bench.report("invoice paid: booked (heap)", *got["spent"])
    for month in range(MONTHS):
        _, count = charged(quarter, "transfer_create", {"fund": "ROOT", "to": capital.cid, "amount": 1_000 * UNIT,
                                                        "category": "sweep"}, ctl, bench)
        bench.report("transfer_create to the capital (call)", count)
    resident = members(size)[1][1]
    for op, args in (("treasury_view", {}), ("ledger_view", {}), ("my_invoices", {}), ("report_view", {})):
        assert queried(quarter, op, args, resident if op == "my_invoices" else ctl, bench) < QUERY_LIMIT, op
    memory(quarter, bench, "after the year")


def test_the_junta_de_govern_local_decides_a_year_of_items(city):
    """Ten seats, every item a proposal that needs all ten. The first items pay a supplier from the Junta's fund, as real
    calls; the year's ``JGL_ITEMS`` run in the heap; a last real item shows what a year of proposals adds."""
    net, capital, quarter, ctl, size, bench = city
    seats = [principal(f"jgl-{n}") for n in range(JGL_SEATS)]
    for seat in seats:
        capital.ok("realm_join", seat)
    capital.ok("department_create", "ctl", department="JGL", kind="board")
    capital.ok("profile_create", "ctl", profile="jgl", allowed_to=["transfer.create"])  # a seat may ask to spend
    capital.ok("position_create", "ctl", department="JGL", position="jgl.member", headcount=JGL_SEATS, profile="jgl")
    for seat in seats:
        capital.ok("appoint", "ctl", position="jgl.member", user=seat)
    capital.ok("fund_create", "ctl", fund="JGL", department="JGL")
    capital.ok("treasury_configure", "ctl", config={"source_fund": "JGL"})
    funds = capital.ok("invoice_create", "ctl", payer=seats[0], amount=10**6 * UNIT,
                       items=[{"concept": "funds", "amount": 10**6 * UNIT}])["invoice"]
    capital.ok("invoice_check", seats[0], invoice=funds)
    capital.ok("department_set_policy", "ctl", department="JGL", m=JGL_SEATS, n=JGL_SEATS)
    jgl_item(capital, seats, bench, "first")
    for start in range(0, JGL_ITEMS, 10):
        got = heap(capital, JGL_ITEM % {"count": min(10, JGL_ITEMS - start), "seats": seats}, bench)
        bench.report("JGL proposal_create (heap)", *got["spent"]["proposal_create"])
        bench.report("JGL proposal_vote (heap)", *got["spent"]["proposal_vote"])
        bench.point("JGL message of 10 items (heap)", start, got["message"])
    bench.report("JGL deadline timer of an item, closed by then (heap)", heap(capital, EXPIRE, bench)["spent"])
    jgl_item(capital, seats, bench, "after a year")
    assert queried(capital, "proposal_list", {"count": 100}, seats[0], bench) < QUERY_LIMIT


def jgl_item(capital, seats, bench, when):
    """One item through real calls: a seat asks to pay a supplier from the Junta's fund, which files a proposal; the
    ten seats vote, and the last vote carries it out, calling the ledger."""
    out, count = charged(capital, "transfer_create", {"fund": "JGL", "to": principal("supplier"), "amount": 100 * UNIT},
                         seats[0], bench)
    bench.report(f"JGL item filed (call, {when})", count)
    for seat in seats:
        _, count = charged(capital, "proposal_vote", {"proposal": out["proposal"], "choice": "yes"}, seat, bench)
        bench.report(f"JGL vote (call, {when})", count)
    assert capital.proposal(out["proposal"])["status"] == "executed"


def test_the_quarter_and_the_capital_upgrade_with_a_year_in_them(city):
    """post_upgrade on a quarter full of members and a year of bills, and on the capital after the Junta's year, within
    an install's limit; the first call after it loads the codex again."""
    net, capital, quarter, ctl, size, bench = city
    for name, realm, member in (("quarter", quarter, members(size)[1][1]), ("capital", capital, net.people["alice"])):
        bench.report(f"post_upgrade's setup.start on the {name} (heap)", heap(realm, START, bench)["spent"])
        before = realm.pic.get_cycles_balance(realm.id)
        realm.upgrade()
        bench.report(f"upgrade of the {name} (cycles)", before - realm.pic.get_cycles_balance(realm.id))
        _, count = charged(realm, "profile_update", {"nickname": "after"}, member, bench)
        bench.report(f"first call after the upgrade of the {name} (call)", count)
    assert quarter.ok("scale_status", "ctl", query=True)["populations"]
