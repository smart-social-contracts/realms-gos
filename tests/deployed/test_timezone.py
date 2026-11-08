"""``Realm.timezone`` on the real wasm (realms-gos#511): the realm's zone file comes with the release, is read when the
zone is set and again when the realm starts after an upgrade (never in a query), and a change of zone applies from
that moment. The local date is read through the controller's ``__shell__``, which runs in the canister's heap."""

import json
from datetime import datetime, timezone

import pytest
from pic import Canister

NS = 10**9
HOUR = 3600

# Instruction ceilings, about three times what PocketIC measures: 69M for the first zone read since an upgrade (the
# reader, the zone list and the zone's module are compiled once) and 0.8M to 1.7M for each next one.
FIRST_LOAD_BUDGET = 200_000_000
NEXT_LOAD_BUDGET = 6_000_000

LOCAL_DAYS = """
import json
from core import civil, localtime
print(json.dumps([[list(civil.civil_from_days(localtime.local_day(ns)[0])), localtime.local_day(ns)[1] // 3600]
                  for ns in {instants}]))
"""

LOAD_COST = """
import json
from core import localtime
start = ic.performance_counter(0)
localtime.use({name!r})
print(json.dumps(ic.performance_counter(0) - start))
"""


def utc(*when):
    return int(datetime(*when, tzinfo=timezone.utc).timestamp()) * NS


# 2026-07-01 22:00 UTC is the 2nd in Madrid and the 2nd in Tokyo but still the 1st in Los Angeles; the clocks of
# Madrid change at 01:00 UTC on 2026-03-29 and 2026-10-25.
EVENING = utc(2026, 7, 1, 22)
SWITCH = [utc(2026, 3, 29, 0, 59, 59), utc(2026, 3, 29, 1), utc(2026, 10, 25, 0, 59, 59), utc(2026, 10, 25, 1)]


def ok(realm, op, who, query=False, **args):
    out = realm.call(op, args, sender=who, query=query)
    assert out["ok"], (op, out)
    return out


def upgrade(realm):
    """The replica limits how many instructions installs may execute in a short time: let some pass between them."""
    realm.wait(HOUR)
    realm.upgrade()


def local(realm, *instants):
    """``[((year, month, day), offset in hours), ...]`` for UTC instants, in the realm's zone now."""
    out = realm.shell(LOCAL_DAYS.format(instants=list(instants)))
    assert "Traceback" not in out, out
    return [(tuple(date), hours) for date, hours in json.loads(out)]


@pytest.fixture(scope="module")
def realm(wasm_of, people):
    """A launched realm in Madrid: the founder named the zone in the setup."""
    realm = Canister(wasm_of("production"), installer=people["installer"])
    founder = people["founder"]
    ok(realm, "setup_step", people["installer"], step="install", network="local", founder=founder)
    ok(realm, "setup_step", founder, step="realm", name="Agora", open_registration=True, timezone="Europe/Madrid")
    ok(realm, "setup_step", founder, step="trust_policy", require_marketplace_approval=False)
    ok(realm, "setup_launch", founder)
    ok(realm, "realm_join", people["alice"])
    return realm


def test_a_realm_in_madrid_reports_its_local_date_before_and_after_an_upgrade_with_no_manual_load(realm, people):
    assert ok(realm, "realm_info", None, query=True)["realm"]["timezone"] == "Europe/Madrid"
    expected = [((2026, 7, 2), 2)]
    assert local(realm, EVENING) == expected
    assert local(realm, *SWITCH) == [((2026, 3, 29), 1), ((2026, 3, 29), 2), ((2026, 10, 25), 2), ((2026, 10, 25), 1)]
    upgrade(realm)
    assert ok(realm, "realm_info", None, query=True)["realm"]["timezone"] == "Europe/Madrid"
    assert local(realm, EVENING) == expected  # nobody loaded the zone: the realm read it when it started
    assert local(realm, *SWITCH) == [((2026, 3, 29), 1), ((2026, 3, 29), 2), ((2026, 10, 25), 2), ((2026, 10, 25), 1)]


def test_a_change_of_zone_applies_from_that_moment_and_survives_an_upgrade(realm, people):
    ctl, alice = people["ctl"], people["alice"]
    proposal = ok(realm, "proposal_create", alice, title="Before the change")["proposal"]
    before = ok(realm, "proposal_get", alice, query=True, proposal=proposal)["proposal"]
    assert local(realm, EVENING) == [((2026, 7, 2), 2)]

    ok(realm, "realm_configure", ctl, timezone="America/Los_Angeles")
    assert local(realm, EVENING) == [((2026, 7, 1), -7)]
    assert ok(realm, "proposal_get", alice, query=True, proposal=proposal)["proposal"] == before  # UTC: not rewritten

    ok(realm, "realm_configure", ctl, timezone="Asia/Tokyo")
    upgrade(realm)
    assert local(realm, EVENING) == [((2026, 7, 2), 9)]  # still Tokyo after the upgrade
    assert ok(realm, "proposal_get", alice, query=True, proposal=proposal)["proposal"] == before

    ok(realm, "realm_configure", ctl, timezone="UTC")
    assert local(realm, EVENING) == [((2026, 7, 1), 0)]
    upgrade(realm)
    assert local(realm, EVENING) == [((2026, 7, 1), 0)]
    ok(realm, "realm_configure", ctl, timezone="Europe/Madrid")


def test_a_name_the_release_does_not_have_is_refused_and_changes_nothing(realm, people):
    for name in ("Europe/Mardid", "CET", "europe/madrid", "Mars/Olympus"):
        out = realm.call("realm_configure", {"timezone": name}, sender=people["ctl"])
        assert not out["ok"] and out["kind"] == "invalid_argument", (name, out)
    assert ok(realm, "realm_info", None, query=True)["realm"]["timezone"] == "Europe/Madrid"
    assert local(realm, EVENING) == [((2026, 7, 2), 2)]


def test_setting_a_zone_costs_one_parse_and_the_next_zone_less(wasm_of, people):
    """Right after an upgrade nothing of the reader or the zone data is compiled yet: the first zone read pays for
    them and for its own module, the next one only for its module and its parse."""
    realm = Canister(wasm_of("production"), installer=people["installer"])
    upgrade(realm)
    costs = []
    for name in ("America/Argentina/Buenos_Aires", "Asia/Tokyo", "Europe/Madrid"):
        out = realm.shell(LOAD_COST.format(name=name))
        assert "Traceback" not in out, out
        costs.append(json.loads(out))
    print(f"\nthe first zone read after an upgrade: {costs[0]:,} instructions; the next two: {costs[1]:,}, {costs[2]:,}")
    assert costs[0] < FIRST_LOAD_BUDGET and max(costs[1:]) < NEXT_LOAD_BUDGET
