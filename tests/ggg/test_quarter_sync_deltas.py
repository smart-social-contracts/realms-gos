"""A quarter's catch-up takes what its capital changed since it last synced, and costs nothing when nothing changed
(api.quarters.sync, core.sync_log; "What a sync carries" in docs/architecture/quarters-and-federation.md)."""

import json

import fake_chain
import pytest
import world
from api import assets, propagation, quarters, sweeps
from core import op, sync_log
from fake_chain import rejects
from ggg import Codex, Event, LayeredCodex, Quarter, Realm, SyncChange, Zone
from ic_python_db import Entity
from kit import call, ok
from sites import Site
from test_quarters import CAPITAL, capital
from test_sweeps import RULE

IMAGE = "data:image/png;base64," + "A" * 300


def first_sync(name="north", **report):
    """A quarter's first sync with the capital: no cursor, so everything, and the cursor to present next time."""
    return ok("quarter_sync", name, population=0, **report)


def delta(name, since, **report):
    return ok("quarter_sync", name, population=0, have=since["rev"], **report)


def sent(cid="capital", method="quarter_sync"):
    """What a realm sent to ``cid``, as the calls it made show."""
    return [json.loads(arg) for c, m, arg in fake_chain.state["calls"] if (c, m) == (cid, method)]


# ---- The capital: what it answers


def test_a_sync_that_presents_no_cursor_gets_everything_and_the_cursor_to_present_next_time():
    capital("north")
    ok("zone_define", "ctl", zone="riverside", name="Riverside", kind="district")
    out = first_sync()
    assert "delta" not in out and {row["id"] for row in out["directory"]} == {CAPITAL, "north"}
    assert [z["zone"] for z in out["zones"]] == ["riverside"] and {"settings", "sweep"} <= set(out)
    assert out["rev"] == sync_log.cursor() and out["rev"]["n"] > 0


def test_a_quarter_that_holds_the_last_change_gets_the_small_parts_and_nothing_else():
    capital("north")
    held = first_sync()
    out = delta("north", held)
    assert out["delta"] and [row["id"] for row in out["directory"]] == [CAPITAL]  # the capital's own row, always
    assert out["removed"] == [] and out["zones"] == [] and "settings" not in out and "sweep" not in out
    assert out["rev"] == held["rev"] and {"codex", "index", "sync_every", "official_timezone"} <= set(out)


def test_every_change_of_the_capital_reaches_a_quarter_that_holds_the_one_before():
    capital("north", "south")
    held = first_sync()
    ok("zone_define", "ctl", zone="riverside", name="Riverside", kind="district")
    ok("zone_define", "ctl", zone="hillside", name="Hillside", kind="district")
    ok("quarter_register", "ctl", quarter="east", name="East")
    ok("quarter_deregister", "ctl", quarter="south")
    ok("quarter_report", "east", population=7)
    ok("quarter_configure", "ctl", quarter="east", name="East Bank")
    ok("realm_configure", "ctl", manifesto="One realm")
    ok("treasury_configure", "ctl", config={"sweep": [RULE]})
    out = delta("north", held)
    rows = {row["id"]: row for row in out["directory"]}
    assert set(rows) == {CAPITAL, "east"} and rows["east"]["population"] == 7 and rows["east"]["name"] == "East Bank"
    assert out["removed"] == ["south"] and sorted(z["zone"] for z in out["zones"]) == ["hillside", "riverside"]
    assert out["settings"]["manifesto"] == "One realm" and out["sweep"]["rules"] == [RULE]
    again = delta("north", out)  # asked again, there is nothing new
    assert again["removed"] == [] and again["zones"] == [] and "settings" not in again and "sweep" not in again


def test_a_zone_defined_again_as_it_is_is_no_change():
    capital("north")
    ok("zone_define", "ctl", zone="riverside", name="Riverside", kind="district")
    head = sync_log.cursor()
    ok("zone_define", "ctl", zone="riverside", name="Riverside", kind="district")
    assert sync_log.cursor() == head
    ok("zone_define", "ctl", zone="riverside", name="Riverside Park", kind="district")
    assert sync_log.cursor()["n"] == head["n"] + 1


def test_a_report_that_changes_nothing_notes_nothing_and_still_stamps_the_quarter():
    capital("north")
    ok("quarter_report", "north", population=5, codex_id="agora", codex_version="1.4.2")
    head, stamped = sync_log.cursor(), Quarter["north"].last_sync
    fake_chain.state["now"] += 3600
    ok("quarter_report", "north", population=5, codex_id="agora", codex_version="1.4.2")
    assert sync_log.cursor() == head and Quarter["north"].last_sync == stamped + 3600
    ok("quarter_report", "north", population=5, codex_id="agora", codex_version="1.4.3")
    assert sync_log.cursor()["n"] == head["n"] + 1


def test_a_population_is_noted_only_once_it_moved_by_a_step_of_the_capacity():
    capital("north", "south")  # a quarter takes 2,000: the step is 20
    held = first_sync()
    ok("quarter_report", "south", population=10)  # the first it tells
    head = sync_log.cursor()
    ok("quarter_report", "south", population=29)
    assert sync_log.cursor() == head and Quarter["south"].population == 29  # kept, and not told to the quarters
    ok("quarter_report", "south", population=30)
    assert sync_log.cursor()["n"] == head["n"] + 1
    assert {row["id"]: row["population"] for row in delta("north", held)["directory"]}["south"] == 30
    ok("quarter_report", "south", population=49)  # 19 on from the 30 told
    assert sync_log.cursor()["n"] == head["n"] + 1
    ok("quarter_report", "south", population=10)  # 20 under it
    assert sync_log.cursor()["n"] == head["n"] + 2
    ok("quarter_configure", "ctl", capacity=300)  # a smaller quarter: a step of 3
    ok("quarter_report", "south", population=12)
    assert sync_log.cursor()["n"] == head["n"] + 2
    ok("quarter_report", "south", population=13)
    assert sync_log.cursor()["n"] == head["n"] + 3


def test_the_first_population_a_quarter_reports_is_told_whatever_it_is_and_the_row_keeps_it():
    """Told, not only the step: a row that told none would show whatever population the quarter has when the row travels,
    so a quarter that took the row in a delta and one that takes it in a full answer would not show the same."""
    capital("north")
    ok("quarter_register", "ctl", quarter="east", name="East")
    ok("quarter_configure", "ctl", quarter="east", status="active")  # active before it reported: no change of status to note
    held = first_sync()
    head = sync_log.cursor()
    ok("quarter_report", "east", population=5)  # 5 is under a step
    assert sync_log.cursor()["n"] == head["n"] + 1 and Quarter["east"].told_population == 5
    shown = lambda reply: {row["id"]: row["population"] for row in reply["directory"]}["east"]  # noqa: E731
    assert shown(delta("north", held)) == 5
    ok("quarter_report", "east", population=9)  # under a step from the 5 told: kept
    assert shown(first_sync()) == 5  # what a full answer shows is what the delta did


def test_a_row_from_before_this_release_tells_its_population_at_the_quarters_next_report():
    capital("north", "south")
    Quarter["south"].population = 400  # reported before told_population was kept
    held = first_sync()
    assert {row["id"]: row["population"] for row in held["directory"]}["south"] == 400  # until it reports, its own
    head = sync_log.cursor()
    ok("quarter_report", "south", population=402)
    assert sync_log.cursor()["n"] == head["n"] + 1 and Quarter["south"].told_population == 402
    assert {row["id"]: row["population"] for row in delta("north", held)["directory"]}["south"] == 402


def test_what_a_quarter_shows_of_another_quarters_population_is_within_a_step_whatever_happened_to_its_row():
    """A row carries the population it last told, and tells the one it has when it travels for another reason, so a delta
    shows what a full sync does and neither is more than a step from the truth."""
    capital("north", "south")
    held = first_sync()
    shows = lambda reply: {row["id"]: row["population"] for row in reply["directory"]}["south"]  # noqa: E731
    ok("quarter_report", "south", population=100)  # 100 on from the 0 told: told
    assert shows(delta("north", held)) == 100
    ok("quarter_report", "south", population=119)  # 19 on from the 100: kept, and not told
    ok("quarter_configure", "ctl", quarter="south", name="South Bank")  # the row travels, and tells what it has
    assert shows(delta("north", held)) == 119 == shows(first_sync("north"))
    ok("quarter_report", "south", population=81)  # 38 under the 119: told
    assert shows(delta("north", held)) == 81 == shows(first_sync("north"))
    ok("quarter_report", "south", population=70)  # 11 under the 81: kept
    assert shows(first_sync("north")) == 81 and abs(81 - Quarter["south"].population) < 20


# ---- Whether a quarter is wanted


def scaling_capital(*names):
    """A capital on a test network, whose quarters take 10 members: one is full at 9."""
    capital(*names)
    Realm["1"].network = "test"


def test_a_report_of_less_than_full_does_not_have_the_capital_read_every_quarter(monkeypatch):
    scaling_capital("north", "south")
    read = []
    monkeypatch.setattr(quarters, "fullest", lambda: read.append(1) or 0)
    assert not quarters._scale["check"]  # what an earlier report had to look at, it has looked at
    ok("quarter_report", "north", population=8)
    assert read == []
    ok("quarter_report", "north", population=9)
    assert read == [1]


def test_a_full_quarter_asks_for_another_at_each_report_once_the_one_before_is_registered():
    scaling_capital("north")
    ok("quarter_report", "north", population=9)
    assert Realm["1"].scale_in_flight
    ok("quarter_register", "ctl", quarter="east")
    assert not Realm["1"].scale_in_flight
    ok("quarter_report", "north", population=9)  # nothing changed, and north is still full
    assert Realm["1"].scale_in_flight


def test_a_capital_that_is_full_itself_has_the_next_report_of_a_quarter_check():
    scaling_capital("north")
    ok("quarter_configure", "ctl", capacity=5)  # the capital's own members are more than that
    ok("quarter_report", "north", population=0)
    assert Realm["1"].scale_in_flight
    ok("quarter_register", "ctl", quarter="east")
    assert not Realm["1"].scale_in_flight
    ok("quarter_report", "north", population=0)  # nothing changed, and the capital is still full
    assert Realm["1"].scale_in_flight


def test_a_change_of_the_capacity_has_the_next_report_check_whatever_it_reports():
    scaling_capital("north", "south")
    ok("quarter_report", "south", population=8)
    ok("quarter_report", "north", population=1)
    assert not Realm["1"].scale_in_flight
    ok("quarter_configure", "ctl", capacity=8)  # south is full now
    ok("quarter_report", "north", population=1)
    assert Realm["1"].scale_in_flight


# ---- The audit log


def test_a_report_that_changes_nothing_leaves_no_audit_event_and_pushes_no_other_out(monkeypatch):
    monkeypatch.setattr(op, "EVENTS_KEPT", 20)
    capital("north")
    ok("zone_define", "ctl", zone="riverside", name="Riverside", kind="district")
    earliest = Event.max_id()
    ok("quarter_report", "north", population=3, codex_id="agora", codex_version="1.4.2")
    assert Event.max_id() == earliest + 1  # a report that changed something keeps its event
    for hour in range(100):  # five times the audit log's length, the population drifting by a few members
        fake_chain.state["now"] += 3600
        ok("quarter_report", "north", population=3 + hour % 7, codex_id="agora", codex_version="1.4.2")
        ok("quarter_sync", "north", population=3 + hour % 7, codex_id="agora", codex_version="1.4.2")
    assert Event.max_id() == earliest + 1 and Event["1"] is not None
    assert Quarter["north"].last_sync == fake_chain.state["now"]  # still exact
    ok("quarter_report", "north", population=3, codex_id="agora", codex_version="1.4.3")
    assert Event.max_id() == earliest + 2


def test_a_refused_report_is_refused_as_before():
    capital("north")
    head, events = sync_log.cursor(), Event.max_id()
    assert "needs quarter.sync" in call("quarter_sync", "stranger", population=1, have={"n": 1, "at": 1})["error"]
    assert sync_log.cursor() == head and Event.max_id() == events


# ---- A quarter: what it asks and what it writes


def pair():
    capital_site = Site("capital")
    north = Site("north", capital_site)
    capital_site.register(north)
    return capital_site, north


def test_a_quarter_asks_for_everything_in_its_first_sync_and_for_the_changes_after_that():
    capital_site, north = pair()
    assert north.catch_up() == ""
    north.catch_up()
    with north.here():
        first, second = sent()
        assert "have" not in first and second["have"] == Realm["1"].sync_state["rev"]
    north.restart()
    north.catch_up()
    with north.here():
        assert "have" not in sent()[-1]


def test_a_quarter_that_took_every_change_one_at_a_time_holds_what_one_that_took_them_all_at_once_does(monkeypatch):
    capital_site, north = pair()
    north.catch_up()
    def a_codex_whose_time_comes():
        LayeredCodex(id="sha256:" + "b" * 64, activates_at=fake_chain.now() + 30)
        fake_chain.state["now"] += 60

    changes = (
        lambda: ok("zone_define", "ctl", zone="riverside", name="Riverside", kind="district"),
        lambda: ok("zone_define", "ctl", zone="riverside", name="Riverside Park", kind="district"),
        lambda: ok("quarter_register", "ctl", quarter="east", name="East"),
        lambda: ok("quarter_report", "east", population=40, codex_id="agora", codex_version="1.4.2"),
        lambda: ok("quarter_configure", "ctl", quarter="east", name="East Bank"),
        lambda: ok("quarter_configure", "ctl", quarter="north", name="North Bank"),
        lambda: Codex(id="agora@1.5.0", version="1.5.0", manifest={"id": "agora"}),
        lambda: ok("realm_configure", "ctl", manifesto="One realm", name="Agora"),
        lambda: ok("realm_configure_branding", "ctl", logo_url=IMAGE),
        lambda: ok("treasury_configure", "ctl", config={"sweep": [RULE]}),
        lambda: ok("quarter_configure", "ctl", quarter="east", status="suspended"),
        lambda: ok("quarter_deregister", "ctl", quarter="east"),
        a_codex_whose_time_comes,
    )
    for n, change in enumerate(changes):
        with capital_site.here():
            fake_chain.state["now"] += 60
            change()
        assert north.catch_up() == ""
        fresh = Site(f"fresh-{n}", capital_site)  # a quarter that syncs for the first time, as every quarter once did
        capital_site.register(fresh)
        assert fresh.catch_up() == ""
        mine, theirs = north.taken(), fresh.taken()
        for held in (mine, theirs):
            for gone in (fresh.cid, north.cid):  # north learns of the new quarter at its next sync; none is told of itself
                held["quarters"].pop(gone, None)
                held["populations"].pop(gone, None)
        assert mine == theirs, change  # the populations too: both take the one the rows last told
        assert (north.own(), fresh.own()) == (north.row(), fresh.row()), change


def test_a_quarter_that_missed_a_week_of_syncs_catches_up_in_full(monkeypatch):
    monkeypatch.setattr(sync_log, "KEPT", 10)
    capital_site, north = pair()
    north.catch_up()
    for n in range(30):  # a week of changes, more than the log holds
        with capital_site.here():
            fake_chain.state["now"] += 6 * 3600
            ok("zone_define", "ctl", zone=f"zone-{n}", name=f"Zone {n}", kind="district")
            ok("quarter_register", "ctl", quarter=f"q-{n}", name=f"Q {n}")
    assert north.catch_up() == ""
    fresh = Site("south", capital_site)
    capital_site.register(fresh)
    fresh.catch_up()
    mine, theirs = north.taken(), fresh.taken()
    for held in (mine, theirs):
        for gone in ("south", "north"):
            held["quarters"].pop(gone, None)
            held["populations"].pop(gone, None)
    assert mine == theirs and len(mine["zones"]) == 30 and len(mine["quarters"]) == 31
    assert north.own() == north.row()


def test_a_sync_where_nothing_changed_writes_nothing_on_a_quarter(monkeypatch):
    capital_site, north = pair()
    with capital_site.here():
        ok("zone_define", "ctl", zone="riverside", name="Riverside", kind="district")
        ok("realm_configure", "ctl", manifesto="One realm")
    north.catch_up()
    north.catch_up()  # the cursor is in; from here on the capital has nothing new
    saved = []
    original = Entity._save
    monkeypatch.setattr(Entity, "_save", lambda self: (saved.append((self.db() is north.db, type(self).__name__)),
                                                        original(self))[1])
    fake_chain.state["now"] += 3600
    north.catch_up()
    assert [kind for here, kind in saved if here] == [], saved  # not a row of the quarter's
    assert sorted(kind for here, kind in saved if not here) == ["Quarter", "Quarter"]  # the capital stamps the quarter


def test_what_a_quarter_decides_for_itself_is_not_overwritten_by_a_sync_that_changed_nothing_of_it():
    capital_site, north = pair()
    north.catch_up()
    with north.here():
        ok("realm_configure", "ctl", timezone="Asia/Tokyo", welcome_message="Hello", open_registration=True)
        mine = {k: getattr(Realm["1"], k) for k in ("timezone", "welcome_message", "open_registration")}
    with capital_site.here():
        ok("realm_configure", "ctl", manifesto="Changed", timezone="Europe/Madrid", welcome_message="Capital's")
    north.catch_up()
    with north.here():
        assert {k: getattr(Realm["1"], k) for k in mine} == mine and Realm["1"].manifesto == "Changed"


def test_a_quarter_sends_its_census_when_a_population_changed_and_in_its_first_sync():
    capital_site, north = pair()
    with capital_site.here():
        ok("zone_define", "ctl", zone="riverside", name="Riverside", kind="district")
    north.catch_up()
    north.catch_up()
    with north.here():
        ok("user_edit", "ctl", user="alice", home_zone="riverside")
    north.catch_up()
    north.catch_up()
    with north.here():
        first, unchanged, joined, after = sent()
        assert first["zones"] == {} and "zones" not in unchanged
        assert joined["zones"] == {"riverside": 1} and "zones" not in after
    with capital_site.here():
        assert Quarter["north"].zones == {"riverside": 1}


def test_a_census_that_did_not_arrive_is_sent_again():
    capital_site, north = pair()
    with capital_site.here():
        ok("zone_define", "ctl", zone="riverside", name="Riverside", kind="district")
    north.catch_up()
    with north.here():
        ok("user_edit", "ctl", user="alice", home_zone="riverside")
    fake_chain.state["peers"]["capital"].replies["quarter_sync"] = rejects("the capital is down")
    assert "the capital is down" in north.catch_up()
    with north.here():
        assert assets.census["taken"] != assets.census["generation"]  # not acknowledged: nothing arrived
    fake_chain.state["peers"]["capital"].replies["quarter_sync"] = north.ask_capital("quarter_sync")
    assert north.catch_up() == ""
    assert sent()[-1]["zones"] == {"riverside": 1}
    with north.here():
        assert assets.census["taken"] == assets.census["generation"]
    with capital_site.here():
        assert Quarter["north"].zones == {"riverside": 1}


def test_a_member_who_moves_while_the_call_is_out_is_reported_at_the_next_sync():
    capital_site, north = pair()
    with capital_site.here():
        ok("zone_define", "ctl", zone="riverside", name="Riverside", kind="district")
    north.catch_up()
    answer = north.ask_capital("quarter_sync")

    def while_asking(args):
        with north.here():
            ok("user_edit", "ctl", user="alice", home_zone="riverside")
        return answer(args)

    fake_chain.state["peers"]["capital"].replies["quarter_sync"] = while_asking
    north.catch_up()  # what it sent held no one in riverside: it moved while the call was out
    fake_chain.state["peers"]["capital"].replies["quarter_sync"] = answer
    north.catch_up()
    assert sent()[-1]["zones"] == {"riverside": 1}


def test_a_capital_that_starts_again_is_taken_in_full_once_and_what_it_changed_meanwhile_is_not_missed():
    """Rolled back to a release that has no log, it changes what it serves and notes nothing; upgraded again, its log reads
    as it did when the quarter last synced. The start is what the quarter's cursor cannot survive."""
    capital_site, north = pair()
    north.catch_up()
    north.catch_up()
    with capital_site.here():
        before = sync_log.cursor()
        Zone(id="riverside", name="Riverside", kind="district", defined=True)  # written by a build that notes nothing
        Quarter["north"].name = "North Bank"
        assert sync_log.cursor() == before
        quarters.resume()  # and started again, as an upgrade does
    assert north.catch_up() == ""
    with north.here():
        assert Zone["riverside"].name == "Riverside"
    assert north.own()["name"] == "North Bank" and north.own() == north.row()
    for _ in range(2):  # and from then on the changes alone
        north.catch_up()
        with north.here():
            assert sent()[-1]["have"] == Realm["1"].sync_state["rev"]
    with capital_site.here():
        ok("zone_define", "ctl", zone="hillside", name="Hillside", kind="district")
        assert "delta" in ok("quarter_sync", "north", population=0, have=north_cursor(north))


def north_cursor(north):
    with north.here():
        return Realm["1"].sync_state["rev"]


def test_a_sync_that_fails_leaves_the_cursor_where_it_was():
    capital_site, north = pair()
    north.catch_up()
    with north.here():
        held = Realm["1"].sync_state["rev"]
    with capital_site.here():
        ok("zone_define", "ctl", zone="riverside", name="Riverside", kind="district")
    fake_chain.state["peers"]["capital"].replies["quarter_sync"] = rejects("the capital is upgrading")
    assert "the capital is upgrading" in north.catch_up()
    with north.here():
        assert Realm["1"].sync_state["rev"] == held and Zone["riverside"] is None
    fake_chain.state["peers"]["capital"].replies["quarter_sync"] = north.ask_capital("quarter_sync")
    assert north.catch_up() == ""
    with north.here():
        assert Zone["riverside"].name == "Riverside"


def test_an_image_that_cannot_be_read_is_asked_for_again_from_the_old_cursor():
    capital_site, north = pair()
    north.catch_up()
    with north.here():
        held = Realm["1"].sync_state["rev"]
    with capital_site.here():
        ok("realm_configure_branding", "ctl", logo_url=IMAGE)
    fake_chain.state["peers"]["capital"].replies["realm_image"] = rejects("the capital is busy")
    north.catch_up()
    with north.here():
        assert Realm["1"].sync_state["rev"] == held and Realm["1"].logo_url == ""
    fake_chain.state["peers"]["capital"].replies["realm_image"] = north.ask_capital("realm_image")
    north.catch_up()
    with north.here():
        assert Realm["1"].logo_url == IMAGE and Realm["1"].sync_state["rev"] != held


def test_a_capital_on_the_previous_release_is_never_given_a_cursor():
    """Its answer has no ``rev``, so there is nothing to present: every sync asks for everything, as it always has."""
    capital_site, north = pair()
    previous = north.ask_capital("quarter_sync")

    def old_answer(args):
        out = json.loads(previous(args))
        for key in ("rev", "delta", "removed"):
            out.pop(key, None)
        return json.dumps(out)

    fake_chain.state["peers"]["capital"].replies["quarter_sync"] = old_answer
    north.catch_up()
    north.catch_up()
    with north.here():
        assert all("have" not in request for request in sent()) and "rev" not in Realm["1"].sync_state


def test_a_quarter_that_leaves_a_capital_forgets_where_it_was_in_its_log():
    capital_site, north = pair()
    north.catch_up()
    with north.here():
        assert "rev" in Realm["1"].sync_state
        fake_chain.state["peers"]["capital"].replies["federation_message"] = json.dumps({"ok": True})
        ok("quarter_secede", "ctl")
        assert Realm["1"].sync_state == {}


def test_a_quarter_whose_treasury_epoch_gets_a_length_arms_its_sweep_timer_then():
    """It holds the capital's rules, which arm the timer once an epoch has a length: the rules do not come again."""
    capital_site, north = pair()
    with capital_site.here():
        ok("treasury_configure", "ctl", config={"sweep": [RULE]})
    with north.here():
        ok("treasury_configure", "ctl", config={"epoch": 0})
    north.catch_up()
    armed = lambda: [t for t in fake_chain.state["timers"] if t[1] is sweeps.end_of_epoch]  # noqa: E731
    assert armed() == []
    with north.here():
        ok("treasury_configure", "ctl", config={"epoch": "monthly"})
    assert len(armed()) == 1


def test_a_capital_answers_a_cursor_after_its_start_with_everything_whatever_the_release_in_between_did():
    """Rolled back to a release that answers every sync in full and notes nothing, the capital changes a zone; upgraded
    again, its log has not moved since the quarter last read it."""
    capital_site, north = pair()
    north.catch_up()
    north.catch_up()
    held = north_cursor(north)
    real = fake_chain.state["peers"]["capital"].replies["quarter_sync"]

    def previous_release(args):
        out = json.loads(real({**args, "have": None}))  # it knows no cursor
        for key in ("rev", "delta", "removed"):
            out.pop(key, None)
        return json.dumps(out)

    fake_chain.state["peers"]["capital"].replies["quarter_sync"] = previous_release
    with capital_site.here():
        Zone(id="riverside", name="Riverside", kind="district", defined=True)  # no note: the old release keeps none
        assert sync_log.cursor()["n"] == held["n"]
    north.catch_up()
    assert north_cursor(north) == held  # nothing to take a cursor from
    fake_chain.state["peers"]["capital"].replies["quarter_sync"] = real
    with capital_site.here():
        quarters.resume()  # upgraded again
    assert north.catch_up() == ""
    with north.here():
        assert Zone["riverside"].name == "Riverside"
