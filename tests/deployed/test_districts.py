"""Districts as zones on the real wasm (realms-gos#502, docs/codices/layered-codex.md §3 and §4): the realm's own chapter
maps its subdivisions to zones when its codex activates, a quarter reports the zones its members live in, and the
capital's ``compute_realm_join`` prefers the quarter that holds the joiner's zone while capacity allows.

A capital and two quarters on one PocketIC instance, the capital running the town's codex. A quarter takes a joiner as
the portal sends them: ``join_targets`` on the capital, then ``realm_join`` on the quarter it names."""

import hashlib

import pytest
from ic.candid import Types, decode
from ic.principal import Principal
from network import Network
from pic import GGG_ERROR
from test_legislation_runtime import TOWN, install

CAPACITY = 5
DISTRICTS = '''
from realms.legislation import above

DISTRICTS = (("riverside", "Riverside"), ("hillside", "Hillside"))

class Realm(above("realm")):
    def on_codex_activate(self, event):
        for zone, name in DISTRICTS:
            self.ggg.zone_define(zone=zone, name=name, kind="district")

    def compute_realm_join(self, user, home_zone, quarters):
        holding = [q for q in quarters if q.zones and home_zone in q.zones]
        return max(holding, key=lambda q: q.zones[home_zone]).id if holding else None
'''
JOIN_TARGETS_ARGS = Types.Opt(Types.Record({"home_zone": Types.Opt(Types.Text)}))
JOIN_TARGETS = Types.Record({"default": Types.Opt(Types.Text), "targets": Types.Vec(Types.Text), "mode": Types.Opt(Types.Text)})


def person(name):
    """A stable principal for a joiner, as the ``people`` fixture makes them."""
    return Principal.self_authenticating(hashlib.sha256(name.encode()).digest()).to_str()


@pytest.fixture(scope="module")
def net(wasm_of, people):
    return Network(wasm_of("production"), people)


@pytest.fixture(scope="module")
def capital(net):
    return net.realm("capital", members=())


@pytest.fixture(scope="module")
def north(net):
    return net.realm("north", members=("alice",))


@pytest.fixture(scope="module")
def south(net):
    return net.realm("south", members=())


@pytest.fixture(scope="module")
def federation(net, capital, north, south):
    net.register_quarters(capital, north, south)
    capital.govern("quarter_configure", {"capacity": CAPACITY})
    return capital, north, south


def zones_of(realm):
    """The zones of the realm with members, as its ``land_map`` counts them: zone id to population."""
    return {z["id"]: z["population"] for z in realm.ok("land_map", "founder", query=True)["zones"] if z["population"]}


def names_of(realm):
    """The zones of the realm with their names, as its ``land_map`` lists them."""
    return {z["id"]: z["name"] for z in realm.ok("land_map", "founder", query=True)["zones"]}


def join(capital, quarter, who, zone):
    """A joiner as the portal sends them: the capital says where, they join the quarter they were offered, and it reports."""
    sent = capital.ok("join_targets", who, query=True, home_zone=zone)["default"]
    assert sent == quarter.cid, (zone, sent, quarter.cid)
    quarter.ok("realm_join", who, home_zone=zone)
    quarter.ok("quarter_sync", "ctl")


def test_a_the_chapter_maps_the_districts_to_zones_when_the_codex_activates(federation):
    capital, north, south = federation
    install(capital, [(TOWN, "realm", DISTRICTS)], layers=(TOWN,), calendars=())
    # The realm has launched, so a governed zone_define of a chapter is a proposal the realm files: its members decide.
    proposals = capital.ok("proposal_list", "founder", query=True)["proposals"]
    defining = [p for p in proposals if p["title"].startswith("zone_define")]
    assert len(defining) == 2
    for proposal in defining:
        capital.ok("proposal_vote", "founder", proposal=proposal["id"], choice="yes")
        if capital.proposal(proposal["id"])["status"] == "voting":
            capital.ok("proposal_finalize", "ctl", proposal=proposal["id"])
    districts = {"riverside": "Riverside", "hillside": "Hillside"}
    assert names_of(capital) == districts
    for quarter in (north, south):  # a quarter runs no on_codex_activate: the capital's sync sends it the zones
        assert names_of(quarter) == {}
        quarter.ok("quarter_sync", "ctl")
        assert names_of(quarter) == districts


def test_b_a_zone_spans_several_quarters_and_each_counts_its_own_members(federation):
    capital, north, south = federation
    for who, quarter, zone in (("a", north, "riverside"), ("b", north, "hillside"), ("c", south, "hillside"),
                               ("d", south, "hillside")):
        quarter.ok("realm_join", person(who), home_zone=zone)
    for quarter in (north, south):
        quarter.ok("quarter_sync", "ctl")
    assert zones_of(north) == {"riverside": 1, "hillside": 1} and zones_of(south) == {"hillside": 2}
    held = {q["id"]: q["zones"] for q in capital.ok("quarter_directory", "founder", query=True)["quarters"]}
    assert held[north.cid] == zones_of(north) and held[south.cid] == zones_of(south)
    assert [capital.ok("quarter_directory", "founder", query=True)["quarters"][0]["zones"]] == [{}]  # the capital holds none
    populations = {q["id"]: q["population"] for q in capital.ok("quarter_directory", "founder", query=True)["quarters"]}
    assert (populations[north.cid], populations[south.cid]) == (4, 3)  # south is the least populated


def test_c_a_member_with_a_home_zone_joins_the_quarter_that_holds_it_while_capacity_allows(federation):
    capital, north, south = federation
    ask = lambda arg: capital.ggg("ggg_join_targets", JOIN_TARGETS_ARGS, arg, JOIN_TARGETS, person("h"), query=True)  # noqa: E731
    assert ask([{"home_zone": ["riverside"]}])["Ok"]["default"] == [north.cid]  # only north holds it, south has fewer members
    sent_nothing = capital.pic.query_call(capital.id, "ggg_join_targets", b"DIDL\x00\x00")  # `()`, as before the argument
    no_zone = [ask([{"home_zone": []}]), ask([]),
               decode(bytes(sent_nothing), [Types.Variant({"Ok": JOIN_TARGETS, "Err": GGG_ERROR})])[0]["value"]]
    assert [got["Ok"]["default"] for got in no_zone] == [[south.cid]] * 3  # the least populated
    join(capital, north, person("e"), "riverside")
    assert zones_of(north) == {"riverside": 2, "hillside": 1}  # the population follows the members


def test_d_capacity_decides_before_the_zone(federation):
    capital, north, south = federation
    # north has 5 of 5 members now: it is not offered, though it holds riverside.
    join(capital, south, person("f"), "riverside")
    join(capital, south, person("g"), "hillside")
    assert zones_of(south) == {"riverside": 1, "hillside": 3}
    held = {q["id"]: q["zones"] for q in capital.ok("quarter_directory", "founder", query=True)["quarters"]}
    assert held[south.cid] == zones_of(south) and held[north.cid] == zones_of(north)
