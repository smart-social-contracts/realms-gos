"""Districts are zones (realms-gos#502, docs/codices/layered-codex.md §3 and §2 rule 13): the realm's own chapter maps its
subdivisions to zones when its codex activates, and ``compute_realm_join`` may keep neighbours together, with capacity
deciding first. A quarter reports the zones its members live in, and the capital offers them to the chapter and to no one else."""

import json

import fake_chain
import ggg_methods as g
from core import op
from kit import ok
from test_legislation_runtime import TOWN, activate, fresh_realm, layered  # noqa: F401 (the fixture applies here too)
from test_quarters import CAPITAL, quarter, reply

from api import quarters
from ggg import Quarter, Realm, User, Zone

CAPACITY = 100
# The town's own chapter: its districts under their local names, and a placement that prefers the quarter with the most
# members of the joiner's district.
DISTRICTS = '''
from realms.legislation import above

DISTRICTS = (("riverside", "Riverside"), ("hillside", "Hillside"), ("old-town", "Old Town"))

class Realm(above("realm")):
    def on_codex_activate(self, event):
        for zone, name in DISTRICTS:
            self.ggg.zone_define(zone=zone, name=name, kind="district")

    def compute_realm_join(self, user, home_zone, quarters):
        holding = [q for q in quarters if q.zones and home_zone in q.zones]
        return max(holding, key=lambda q: q.zones[home_zone]).id if holding else None
'''
ALWAYS_SOUTH = '''
from realms.legislation import above

class Realm(above("realm")):
    def compute_realm_join(self, user, home_zone, quarters):
        return "south"
'''
MEMBERS = {"north": (40, {"riverside": 30, "hillside": 5}), "south": (50, {"hillside": 8}), "east": (5, {})}


def town(chapter=DISTRICTS):
    """The town's codex active while the realm is still in setup, so that its effects run directly, then a launched capital
    with the quarters ``MEMBERS`` as they last reported."""
    activate(layered({(TOWN, "realm"): chapter}, layers=[TOWN]))
    Realm["1"].status = "beta"
    ok("quarter_configure", "ctl", capacity=CAPACITY)
    for name, (population, zones) in MEMBERS.items():
        ok("quarter_register", "ctl", quarter=name, name=name.title())
        report(name, population, zones)


def report(name, population, zones):
    ok("quarter_report", name, population=population, zones=zones)


def goes_to(*, zone=None, who="newcomer"):
    return ok("join_targets", who, query=True, **({"home_zone": zone} if zone else {}))["default"]


def test_the_realms_chapter_maps_its_districts_to_zones_and_a_second_codex_redefines_them_in_place():
    first = activate(layered({(TOWN, "realm"): DISTRICTS}, layers=[TOWN]))
    assert [(z.id, z.name, z.kind, z.defined) for z in (Zone["riverside"], Zone["hillside"], Zone["old-town"])] == [
        ("riverside", "Riverside", "district", True), ("hillside", "Hillside", "district", True),
        ("old-town", "Old Town", "district", True)]
    Realm["1"].open_registration = True
    ok("realm_join", "zoe", home_zone="old-town")
    activate(layered({(TOWN, "realm"): DISTRICTS.replace("Old Town", "Casco Viejo")}, layers=[TOWN], previous=first.id))
    assert (Zone["old-town"].name, Zone["old-town"].population) == ("Casco Viejo", 1)  # redefined, not created twice
    assert User["zoe"].home_zone == "old-town"


def test_a_member_joins_the_quarter_that_holds_their_zone_while_it_has_room():
    town()
    assert goes_to() == "east"  # no zone: the least populated
    assert goes_to(zone="riverside") == "north"  # only north holds it, though east has far fewer members
    assert goes_to(zone="old-town") == "east"  # no quarter holds it: the least populated
    assert goes_to(zone="nowhere") == "east"  # not a zone of the realm: it matches no quarter


def test_one_zone_spans_several_quarters_and_the_one_with_most_of_it_is_preferred():
    town()
    assert goes_to(zone="hillside") == "south"  # north has 5 of it and south 8, though south has more members
    report("south", 50, {"hillside": 2})
    assert goes_to(zone="hillside") == "north"


def test_capacity_decides_before_the_zone():
    town()
    report("south", CAPACITY, {"hillside": 8})  # full
    assert goes_to(zone="hillside") == "north"  # the other holder
    report("north", CAPACITY - 1, {"hillside": 5})
    assert goes_to(zone="hillside") == "north"  # one place left
    report("north", CAPACITY, {"hillside": 5})
    assert goes_to(zone="hillside") == "east"  # both holders are full: the least populated that has room


def test_when_every_quarter_is_full_the_least_populated_takes_the_join_and_the_chapter_is_not_asked():
    town(ALWAYS_SOUTH)
    for name, (_, zones) in MEMBERS.items():
        report(name, CAPACITY + {"north": 20, "south": 10, "east": 0}[name], zones)
    assert goes_to(zone="hillside") == "east"


def test_a_chapter_cannot_place_a_member_in_a_full_quarter():
    town(ALWAYS_SOUTH)
    assert goes_to() == "south"  # south has room
    report("south", CAPACITY, {})
    assert goes_to() == "east"  # its answer is ignored


def test_a_member_known_here_is_placed_by_their_home_zone_when_they_state_none():
    town()
    ok("user_edit", "ctl", user="alice", home_zone="riverside")
    assert goes_to(who="alice") == "north"
    assert goes_to(who="alice", zone="hillside") == "south"  # what they state wins


def test_join_targets_never_says_the_zone():
    town()
    out = ok("join_targets", "newcomer", query=True, home_zone="riverside")
    assert "riverside" not in json.dumps(out)


def test_the_capital_keeps_the_counts_a_quarter_reports_and_no_reply_carries_them():
    """A small count singles out a person: the counts are for the placement, and not for the anonymous principal that reads
    the directory (public), nor for a quarter that is sent the directory."""
    town()
    assert Quarter["north"].zones == {"riverside": 30, "hillside": 5}
    ok("quarter_report", "north", population=41)  # a report without zones leaves them as they were
    assert Quarter["north"].zones == {"riverside": 30, "hillside": 5}
    for who in (fake_chain.ANONYMOUS, "newcomer", "north"):
        fake_chain.state["caller"] = who
        rows = ok("quarter_directory", who, query=True)["quarters"]
        typed = g.ggg_quarter_directory({"cursor": None, "limit": None})["Ok"]["items"]
        assert rows and typed and not [row for row in rows + typed if "zones" in row]
    synced = ok("quarter_sync", "north", population=41)
    fake_chain.state["caller"] = "north"
    typed = op.run(g.ggg_quarter_sync())["Ok"]
    assert len(synced["directory"]) == len(typed["items"]) == 4
    assert not [row for row in synced["directory"] + typed["items"] if "zones" in row]
    assert all(set(zone) == {"zone", "name", "kind"} for zone in synced["zones"] + typed["zones"])  # definitions, no counts
    assert all("zones" not in row for row in quarters.directory())


def test_a_quarter_reports_the_zones_its_members_live_in(monkeypatch):
    quarter(monkeypatch, "north", "south")
    for zone in ("riverside", "hillside", "old-town"):
        ok("zone_define", "ctl", zone=zone, name=zone.title(), kind="district")
    Realm["1"].open_registration = True
    for who, zone in (("zoe", "riverside"), ("yan", "riverside"), ("xia", "hillside")):
        ok("realm_join", who, home_zone=zone)
    ok("user_edit", "ctl", user="xia", home_zone="old-town")
    ok("user_edit", "ctl", user="alice", home_zone="hillside")
    ok("user_edit", "ctl", user="alice", home_zone="riverside")
    assert quarters.zones_held() == {"riverside": 3, "old-town": 1}  # hillside has no member left
    reply(CAPITAL, "quarter_sync", directory=[], codex={"id": "", "version": ""}, index=1)
    ok("quarter_sync", "ctl")
    sent = json.loads(fake_chain.state["calls"][-1][2])
    assert sent["zones"] == {"riverside": 3, "old-town": 1}
    assert [Zone[z].population for z in ("riverside", "hillside", "old-town")] == [3, 0, 1]


def test_the_capital_sends_its_quarters_the_zones_it_defines_without_a_population():
    town()
    synced = ok("quarter_sync", "north", population=41)
    assert synced["zones"] == [{"zone": z, "name": n, "kind": "district"} for z, n in
                               (("riverside", "Riverside"), ("hillside", "Hillside"), ("old-town", "Old Town"))]
    assert all("zones" not in q for q in synced["directory"])


def sent_by_the_capital(*definitions):
    reply(CAPITAL, "quarter_sync", directory=[], codex={"id": "", "version": ""}, index=1,
          zones=[{"zone": z, "name": n, "kind": "district"} for z, n in definitions])


def test_a_quarter_defines_the_capitals_zones_by_id_so_a_repeat_creates_nothing(monkeypatch):
    quarter(monkeypatch, "north", "south")
    sent_by_the_capital(("riverside", "Riverside"), ("hillside", "Hillside"))
    ok("quarter_sync", "ctl")
    assert [(z.name, z.kind, z.defined, z.population) for z in (Zone["riverside"], Zone["hillside"])] == [
        ("Riverside", "district", True, 0), ("Hillside", "district", True, 0)]
    Realm["1"].open_registration = True
    ok("realm_join", "zoe", home_zone="riverside")  # a joiner may now name it
    before = Zone.count()
    sent_by_the_capital(("riverside", "Ribera"), ("hillside", "Hillside"))
    ok("quarter_sync", "ctl")
    assert Zone.count() == before and (Zone["riverside"].name, Zone["riverside"].population) == ("Ribera", 1)


def test_a_quarters_own_zone_stays_and_a_members_home_cell_is_left_alone(monkeypatch):
    quarter(monkeypatch, "north", "south")
    ok("zone_define", "ctl", zone="harbour", name="Harbour", kind="district")
    ok("zone_set", "alice", zone="871f1d489ffffff")
    sent_by_the_capital(("871f1d489ffffff", "Not a district"), ("riverside", "Riverside"))
    ok("quarter_sync", "ctl")
    assert Zone["harbour"].defined and Zone["riverside"].defined  # the quarter's own stays; the next one still arrives
    assert (Zone["871f1d489ffffff"].defined, Zone["871f1d489ffffff"].user) == (False, "alice")


def test_the_typed_join_targets_takes_the_zone_and_a_caller_with_no_argument_still_works():
    town()
    fake_chain.state["caller"] = "newcomer"
    assert g.ggg_join_targets({"home_zone": "riverside"})["Ok"]["default"] == "north"
    for no_zone in ({"home_zone": None}, None):
        assert g.ggg_join_targets(no_zone)["Ok"]["default"] == "east"
