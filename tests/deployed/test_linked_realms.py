"""The harness for linked realms (links.py, outcalls.py, costs.py) on the real wasm: realms of one wasm on one PocketIC
instance call each other, a governed call waits for its time across an upgrade of one canister, HTTPS outcalls are
answered from a table, and instruction costs are reported (realms-gos#499).

The realms are built once for the module and the tests below use them in order.
"""

import json

import pytest
from costs import Costs
from links import Network

HOUR = 3600
URL = "https://legislation.example.test/arcadia/tax.py"

# A timer in the realm makes one HTTPS outcall, as the install task will, and keeps what came back in the heap.
FETCH = """
from core import chain
from core.candid import Reader, idl_hash, quote
if not hasattr(chain, "fetched"):
    chain.fetched = {{}}
chain.fetched.pop({url!r}, None)

def fetch():
    arg = ('(record {{ url = ' + quote({url!r}) + '; max_response_bytes = opt (1000 : nat64); '
           'method = variant {{ get }}; headers = vec {{}}; body = null; transform = null }})')
    try:
        reply = Reader((yield from chain.call("aaaaa-aa", "http_request", arg, 100_000_000_000))).args()[0]
        chain.fetched[{url!r}] = [reply[idl_hash("status")], reply[idl_hash("body")].decode()]
    except Exception as e:
        chain.fetched[{url!r}] = str(e)

chain.set_timer(0, fetch)
"""
FETCHED = "import json\nfrom core import chain\nprint(json.dumps(chain.fetched.get({url!r})))"


@pytest.fixture(scope="module")
def net(wasm_of, people):
    return Network(wasm_of("production"), people)


@pytest.fixture(scope="module")
def capital(net):
    return net.realm("capital")


@pytest.fixture(scope="module")
def north(net):
    return net.realm("north", members=("alice",))


@pytest.fixture(scope="module")
def south(net):
    return net.realm("south", members=("bob",))


def quarter_names(realm):
    rows = realm.ok("quarter_directory", None, query=True)["quarters"]
    return {row["id"]: row["name"] for row in rows}


def fetch(net, realm, url):
    """What the realm's outcall for ``url`` came back with, ``[status, body]``, or the error it saw; None while waiting."""
    out = realm.shell(FETCH.format(url=url))
    assert "Traceback" not in out, out
    net.wait(2)
    return json.loads(realm.shell(FETCHED.format(url=url)))


def test_realms_of_one_wasm_on_one_instance_call_each_other(net, people, capital, north, south):
    assert {realm.pic for realm in (capital, north, south)} == {net.pic}
    assert len({realm.id for realm in (capital, north, south)}) == 3
    net.register_quarters(capital, north, south)
    assert set(quarter_names(north)) == {capital.cid, north.cid, south.cid}  # north called the capital and learnt south

    north.ok("self_change_quarter", "alice", quarter=south.cid)  # north called south directly
    alice = people["alice"]
    assert south.ok("user_get", "ctl", query=True, user=alice)["user"]["status"] == "active"
    assert north.ok("user_get", "ctl", query=True, user=alice)["user"]["home_quarter"] == south.cid


def test_a_governed_call_waits_for_its_time_and_the_canister_upgraded_in_between_keeps_its_state(net, capital, north):
    assert quarter_names(capital)[north.cid] == "north"
    effective_at = capital.now() + HOUR
    change = capital.govern("quarter_configure", {"quarter": north.cid, "name": "Northern"}, effective_at=effective_at)
    assert capital.proposal(change)["status"] == "execution_scheduled"
    registered = capital.ok("quarter_directory", None, query=True)

    net.upgrade_when(capital, lambda: capital.now() >= effective_at - 600, step=60)

    assert capital.now() < effective_at
    assert capital.ok("quarter_directory", None, query=True) == registered  # the federation survived the upgrade
    assert capital.proposal(change)["status"] == "execution_scheduled"
    assert quarter_names(capital)[north.cid] == "north"
    north.ok("quarter_sync", "ctl")  # the quarter still reaches its capital, and the capital still accepts it

    assert capital.run_at_effective_at(change)["result"]
    assert quarter_names(capital)[north.cid] == "Northern"


def test_an_outcall_gets_the_bytes_served_then_the_changed_bytes(net, capital):
    net.web.serve(URL, b"property_tax_rate = 0.40")
    assert fetch(net, capital, URL) == [200, "property_tax_rate = 0.40"]
    net.web.serve(URL, b"property_tax_rate = 0.45")
    assert fetch(net, capital, URL) == [200, "property_tax_rate = 0.45"]
    assert net.web.asked[-2:] == [("GET", URL), ("GET", URL)]


def test_an_outcall_for_a_url_nobody_serves_gets_a_404(net, capital):
    missing = "https://legislation.example.test/arcadia/missing.py"
    assert fetch(net, capital, missing) == [404, "not found"]


def test_an_outcall_to_a_server_that_is_down_fails(net, capital):
    down = "https://legislation.example.test/down.py"
    net.web.unreachable(down, "connection refused")
    assert "connection refused" in fetch(net, capital, down)


def test_an_outcall_nobody_answers_stays_pending_until_the_url_is_served(net, capital):
    silent = "https://legislation.example.test/silent.py"
    net.web.no_answer(silent)
    assert fetch(net, capital, silent) is None
    assert [request["url"] for request in net.web.pending()] == [silent]
    net.web.serve(silent, b"late")
    net.wait(2)
    assert json.loads(capital.shell(FETCHED.format(url=silent))) == [200, "late"]
    assert net.web.pending() == []


def test_a_realm_reports_what_a_quarter_report_costs(costs, capital, north):
    report = f'import json\nfrom core import op\nop.run(op.dispatch("quarter_report", json.dumps({{"population": 3}}), signer="{north.cid}"))'
    spent = costs.measure(capital, "quarter report, handled by the capital", report)
    assert 0 < spent < 1_000_000_000
    assert costs.samples["quarter report, handled by the capital"][-1] == spent


def test_the_cost_table_has_a_line_for_each_thing_measured():
    costs = Costs()
    costs.report("install step: build", 4_000_000)
    costs.report("install step: build", 6_500_000)
    costs.report("chapter fetch, by https", 1_234_567)
    assert costs.table() == [
        "what                     times            least             most",
        "chapter fetch, by https      1        1,234,567        1,234,567",
        "install step: build          2        4,000,000        6,500,000",
    ]
