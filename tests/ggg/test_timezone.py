"""``Realm.timezone`` (ggg#5, realms-gos#511): an IANA name from the tz database the release bundles, UTC until the
realm sets one, and the realm's local time follows it from the moment it is set."""

import pytest

import fake_chain
import ggg_methods as g
import world
from api import setup
from core import localtime, op, tzif
from core.civil import civil_from_days
from ggg import Department, Proposal, Realm
from kit import call, ok

NS = 10**9
EVENING = 1_782_943_200 * NS  # 2026-07-01 22:00 UTC: already the 2nd in Madrid, still the 1st in Los Angeles
BAD = ["CET", "Madrid", "europe/madrid", "+01:00", "Mars/Olympus", "Europe/Mardid", "Europe/", "", "utc", 5, ["UTC"]]


@pytest.fixture(autouse=True)
def utc():
    """The reader keeps its zone in the module, which outlives the test that set it: a canister starts in UTC."""
    localtime.use("UTC")


def local_date(ns=EVENING):
    return civil_from_days(localtime.local_day(ns)[0])


def launched():
    world.build()
    ok("setup_step", "ctl", step="install", network="ic", founder="frank")
    ok("setup_step", "frank", step="realm", name="Agora")
    ok("setup_launch", "frank")


def test_a_realm_is_in_utc_until_it_sets_its_zone():
    world.build()
    assert Realm["1"].timezone == "UTC" and ok("realm_info", "alice")["realm"]["timezone"] == "UTC"
    assert g.ggg_realm_info()["timezone"] == "UTC"
    assert local_date() == (2026, 7, 1)


def test_the_founder_sets_it_during_setup_and_the_realm_keeps_time_in_it():
    world.build()
    ok("setup_step", "ctl", step="install", network="ic", founder="frank")
    ok("setup_step", "frank", step="realm", name="Agora", timezone="Europe/Madrid")
    assert Realm["1"].timezone == "Europe/Madrid" and ok("setup_state", "frank")["realm"]["timezone"] == "Europe/Madrid"
    assert ok("realm_info", "alice")["realm"]["timezone"] == "Europe/Madrid" and g.ggg_realm_info()["timezone"] == "Europe/Madrid"
    assert local_date() == (2026, 7, 2)


@pytest.mark.parametrize("name", BAD)
def test_a_name_that_is_not_a_zone_of_the_release_is_refused(name):
    launched()
    out = call("realm_configure", "ctl", timezone=name)
    assert not out["ok"] and out["kind"] == "invalid_argument", out
    assert Realm["1"].timezone == "UTC" and local_date() == (2026, 7, 1)


def test_the_setup_step_refuses_it_too_and_changes_nothing():
    world.build()
    ok("setup_step", "ctl", step="install", network="ic", founder="frank")
    out = call("setup_step", "frank", step="realm", name="Agora", timezone="Madrid")
    assert not out["ok"] and out["kind"] == "invalid_argument", out
    assert Realm["1"].timezone == "UTC" and not Realm["1"].name


def test_the_ggg_method_refuses_it_as_an_invalid_argument():
    launched()
    fake_chain.state["caller"] = "ctl"
    out = op.run(g.ggg_realm_configure({"timezone": "Madrid"}))
    assert "InvalidArgument" in out["Err"], out
    assert Realm["1"].timezone == "UTC"


def test_every_area_location_name_of_the_tz_database_is_taken_including_its_old_names():
    launched()
    for name in ("America/Argentina/Buenos_Aires", "America/Buenos_Aires", "Asia/Calcutta", "Etc/GMT+5", "Pacific/Auckland", "UTC"):
        ok("realm_configure", "ctl", timezone=name)
        assert Realm["1"].timezone == name


def test_changing_the_zone_applies_from_that_moment_and_rewrites_nothing():
    launched()
    p = ok("proposal_create", "alice", title="x")["proposal"]
    before = ok("proposal_get", "alice", proposal=p)["proposal"]
    ok("realm_configure", "ctl", timezone="Europe/Madrid")
    assert local_date() == (2026, 7, 2)
    ok("realm_configure", "ctl", timezone="America/Los_Angeles")
    assert local_date() == (2026, 7, 1)
    ok("realm_configure", "ctl", timezone="Asia/Tokyo")
    assert local_date() == (2026, 7, 2) and localtime.local_day(EVENING)[1] == 9 * 3600
    after = ok("proposal_get", "alice", proposal=p)["proposal"]
    assert after == before  # its opening and its deadline are UTC instants: the zone does not touch them


def test_after_launch_the_change_is_a_governed_setting():
    launched()
    ok("governance_update", "ctl", voting_window=0)  # a proposal closes when its outcome is certain
    world.policy(Department["Administration"], 2, 3)
    p = ok("realm_configure", "ctl", timezone="Europe/Madrid")["proposal"]
    assert Realm["1"].timezone == "UTC" and local_date() == (2026, 7, 1)
    for voter in world.PEOPLE:
        if Proposal[p].status != "voting":  # it closed as soon as its outcome was certain
            break
        ok("proposal_vote", voter, proposal=p, choice="yes")
    assert Realm["1"].timezone == "Europe/Madrid" and local_date() == (2026, 7, 2)


def test_an_upgrade_reads_the_zone_again():
    launched()
    ok("realm_configure", "ctl", timezone="Europe/Madrid")
    localtime._zone, localtime._name = tzif.UTC, "UTC"  # a new heap: the parsed zone is gone
    assert local_date() == (2026, 7, 1)
    setup.start()  # post_upgrade
    assert local_date() == (2026, 7, 2)


def test_a_zone_already_in_memory_is_not_parsed_again(monkeypatch):
    launched()
    ok("realm_configure", "ctl", timezone="Europe/Madrid")
    monkeypatch.setattr(tzif, "parse", lambda data: pytest.fail("parsed again"))
    ok("realm_configure", "ctl", timezone="Europe/Madrid", name="Agora")
    setup.start()
    assert local_date() == (2026, 7, 2)
