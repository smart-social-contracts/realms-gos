"""Quarters in different time zones on real wasms (realms-gos#528, docs/codices/layered-codex.md §4.5): a capital on
Europe/Madrid and its quarters on one PocketIC instance. A new quarter starts on its capital's zone unless it chose one;
after that the zone is its own, which it changes itself or the realm sets by a realm-wide vote. The capital's clock
counts the realm-wide matters and a quarter's its own; an instant is the same instant everywhere; no codex sets a zone.

The realms are built once for the module and the tests below use them in order.
"""

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from network import Network
from test_installs import HOUR, Site, expected_id, own_layer
from test_legislation_runtime import CALENDAR, install
from test_propagation import active, job, rows, status
from test_timezone import local

# test_legislation_runtime has put the realm's source on the path, for the calendar the tests count with
from realms.legislation.calendar import Calendar
from realms.legislation.periods import working_days

MADRID, TOKYO, MEXICO = "Europe/Madrid", "Asia/Tokyo", "America/Mexico_City"
# One layered codex for the capital and a quarter: a guard that answers with the day one working day after today, on
# the clock of the realm it runs in, and a hook that tries to set a zone in each way a chapter could.
CHAPTER = '''
from realms.legislation import above, working_days

class Procedure(above("procedure")):
    def check_proposal_create(self, args):
        if args.title == "count":
            return self.deny(self.calendar.add(self.local_day(self.now()), working_days(1)))

    def on_proposal_create(self, event):
        title = event.args["title"]
        if title == "own zone":
            self.ggg.realm_configure(timezone="Asia/Seoul")
        if title == "quarter zone":
            self.ggg.quarter_configure(quarter=event.args["description"], timezone="Asia/Seoul")
        if title == "relayed zone":
            self.ggg.federation_message(topic="quarter.local_settings", body={"timezone": "Asia/Seoul"})
'''
# A quarter's heap made to answer as the previous release does, which has no handler for quarter.local_settings, and back.
OLD_RELEASE = "from api import federation, quarters\ndel federation.TOPICS[quarters.LOCAL_SETTINGS]"
THIS_RELEASE = "from api import federation, quarters\nfederation.TOPICS[quarters.LOCAL_SETTINGS] = quarters.on_local_settings"


@pytest.fixture(scope="module")
def net(wasm_of, people):
    return Network(wasm_of("production"), people)


@pytest.fixture(scope="module")
def federation(net):
    """The capital on Madrid time; ``east``, which has not chosen a zone; ``west``, which chose Mexico City."""
    capital = net.realm("capital")
    capital.ok("realm_configure", "ctl", timezone=MADRID)
    east, west = net.realm("east", members=("alice",)), net.realm("west", members=("alice",))
    west.ok("realm_configure", "ctl", timezone=MEXICO)
    net.register_quarters(capital, east, west)
    return capital, east, west


def zone(realm):
    return realm.ok("realm_info", None, query=True)["realm"]["timezone"]


def label(capital, quarter):
    return next(r["name"] for r in capital.ok("quarter_directory", None, query=True)["quarters"] if r["id"] == quarter.cid)


def utc(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc)


def offset_hours(realm, seconds):
    """The UTC offset, in hours, of ``realm``'s own clock at ``seconds``: the zone it has loaded, not only its name."""
    ((_, hours),) = local(realm, seconds * 10**9)
    return hours


def test_a_new_quarter_starts_on_its_capitals_zone_and_one_that_chose_keeps_its_own(federation):
    capital, east, west = federation
    assert (zone(capital), zone(east), zone(west)) == (MADRID, MADRID, MEXICO)
    now = capital.now()
    assert offset_hours(east, now) == offset_hours(capital, now) == 2  # May: summer time in Madrid
    assert offset_hours(west, now) == -5


def test_the_capitals_later_change_does_not_follow_and_a_quarter_changes_its_own(federation):
    capital, east, west = federation
    capital.ok("realm_configure", "ctl", timezone="Atlantic/Canary")
    assert (zone(capital), zone(east), zone(west)) == ("Atlantic/Canary", MADRID, MEXICO)
    capital.ok("realm_configure", "ctl", timezone=MADRID)

    west.ok("realm_configure", "ctl", timezone="America/Bogota")
    assert (zone(capital), zone(west)) == (MADRID, "America/Bogota")
    west.ok("realm_configure", "ctl", timezone=MEXICO)


def official_zone(realm):
    return realm.ok("realm_info", None, query=True)["realm"]["official_timezone"]


def test_a_quarter_shows_its_capitals_zone_as_the_official_one_and_learns_its_change_at_the_next_sync(federation):
    capital, east, west = federation
    assert (official_zone(capital), official_zone(east), official_zone(west)) == (MADRID, MADRID, MADRID)
    assert zone(west) == MEXICO  # the quarter's own zone is its own, whatever the official one is
    capital.ok("realm_configure", "ctl", timezone="Atlantic/Canary")
    assert official_zone(capital) == "Atlantic/Canary" and official_zone(west) == MADRID  # not until it syncs
    west.ok("quarter_sync", "ctl")
    assert (official_zone(west), zone(west)) == ("Atlantic/Canary", MEXICO)
    capital.ok("realm_configure", "ctl", timezone=MADRID)
    west.ok("quarter_sync", "ctl")
    assert official_zone(west) == MADRID


def test_a_realm_wide_vote_sets_a_quarters_zone_and_the_quarter_applies_it(federation):
    capital, east, _ = federation
    capital.govern("quarter_configure", {"quarter": east.cid, "timezone": TOKYO})
    assert (zone(capital), zone(east)) == (MADRID, TOKYO)
    assert offset_hours(east, capital.now()) == 9
    received = [m for m in rows(east, "FederationMessage", "topic", "source", "body")
                if m["topic"] == "quarter.local_settings"]
    assert received == [{"topic": "quarter.local_settings", "source": capital.cid, "body": {"timezone": TOKYO}}]


def test_a_malformed_zone_or_one_without_a_quarter_is_refused_before_any_vote(federation):
    capital, east, _ = federation
    capital.ok("department_set_policy", "ctl", department="Administration", m=1, n=2)  # a call becomes a proposal
    filed = len(capital.ok("proposal_list", "founder", query=True)["proposals"])
    for args in ({"quarter": east.cid, "timezone": "Asia/Tokio"}, {"quarter": east.cid, "timezone": "Mars/Olympus"},
                 {"timezone": TOKYO}):
        out = capital.call("quarter_configure", args, sender=capital.network.people["ctl"])
        assert not out["ok"] and out["kind"] == "invalid_argument", (args, out)
    assert len(capital.ok("proposal_list", "founder", query=True)["proposals"]) == filed
    assert capital.ok("quarter_configure", "ctl", quarter=east.cid, timezone=TOKYO)["requires_vote"]
    capital.govern("department_set_policy", {"department": "Administration", "m": 1, "n": 1})
    assert zone(east) == TOKYO


def test_a_quarter_that_refuses_cannot_be_reached_or_runs_an_older_release_leaves_everything_unchanged(net, federation):
    capital, east, _ = federation
    stray = net.realm("stray")  # registered by the capital, but it never joined: it takes no call from it
    capital.ok("quarter_register", "ctl", quarter=stray.cid, name="Stray")
    out = capital.call("quarter_configure", {"quarter": stray.cid, "timezone": TOKYO, "name": "Renamed"},
                       sender=net.people["ctl"])
    assert not out["ok"] and "refused timezone" in out["error"], out
    assert (zone(stray), label(capital, stray)) == ("UTC", "Stray")
    capital.ok("quarter_deregister", "ctl", quarter=stray.cid)

    east.stop()
    out = capital.call("quarter_configure", {"quarter": east.cid, "timezone": "Asia/Seoul", "name": "Renamed"},
                       sender=net.people["ctl"])
    east.start()
    assert not out["ok"] and out["kind"] == "unavailable" and "cannot be reached" in out["error"], out
    assert (zone(east), label(capital, east)) == (TOKYO, "east")

    # A quarter on an older release does not know the topic: it answers ok and applies nothing.
    assert "Traceback" not in east.shell(OLD_RELEASE)
    out = capital.call("quarter_configure", {"quarter": east.cid, "timezone": "Asia/Seoul", "name": "Renamed"},
                       sender=net.people["ctl"])
    assert "Traceback" not in east.shell(THIS_RELEASE)
    assert not out["ok"] and out["kind"] == "conflict" and "did not apply timezone" in out["error"], out
    assert (zone(east), label(capital, east)) == (TOKYO, "east")


def test_a_codex_switch_at_t_happens_at_one_instant_in_every_zone(net, people, federation):
    capital, east, west = federation
    site = Site(net.web)
    layer = own_layer(people["founder"])
    t = capital.now() + HOUR
    proposal = capital.govern("codex_install", site.change_list(layer), effective_at=t)
    net.wait_until(lambda: all(job(realm).get("step") == "wait" for realm in federation), rounds=2)
    capital.run_at_effective_at(proposal)
    net.wait_until(lambda: all(active(realm) == expected_id(layer) for realm in federation), rounds=2)
    switched = [status(realm)["codex"]["installed_at"] for realm in federation]
    assert t <= min(switched) and max(switched) - min(switched) <= 2
    # T is one instant and three local times: the night in Madrid, the morning in Tokyo, the evening before in Mexico City
    assert [offset_hours(realm, t) for realm in federation] == [2, 9, -5]


def test_working_days_count_on_the_capitals_clock_for_a_realm_wide_vote_and_on_a_quarters_for_its_own(
        net, people, federation):
    capital, east, _ = federation
    for realm in (capital, east):
        install(realm, [("x", "procedure", CHAPTER)], layers=("x",), calendars=(("x", json.dumps(CALENDAR)),))
    # A Thursday, 20:00 UTC: still Thursday in Madrid, already Friday in Tokyo.
    moment = utc(capital.now()).replace(hour=20, minute=0, second=0, microsecond=0) + timedelta(days=1)
    while moment.weekday() != 3:
        moment += timedelta(days=1)
    net.wait(int(moment.timestamp()) - capital.now())
    calendar = Calendar([CALENDAR])
    madrid, tokyo = (moment.astimezone(ZoneInfo(z)).strftime("%Y-%m-%d") for z in (MADRID, TOKYO))
    assert madrid < tokyo
    expected = [calendar.add(day, working_days(1)) for day in (madrid, tokyo)]
    assert expected[0] < expected[1]  # Friday, and the Monday after Tokyo's Friday

    founder = people["founder"]
    counted = [realm.call("proposal_create", {"title": "count"}, sender=founder)["error"] for realm in (capital, east)]
    assert counted == expected  # the capital's realm-wide vote on Madrid's clock, the quarter's own on Tokyo's


def test_a_federal_votes_deadline_is_one_instant_that_every_quarters_leg_keeps(federation):
    capital, east, west = federation
    capital.ok("governance_update", "ctl", voting_window=2 * 24 * HOUR)
    east.ok("governance_update", "ctl", voting_window=HOUR)  # a quarter's own window counts for its own votes only
    vote = capital.ok("federal_vote_propose", "founder", title="one deadline",
                      action={"op": "governance_update", "args": {"voting_window": 0}})["vote"]
    (row,) = [v for v in rows(capital, "FederalVote", "id", "deadline") if v["id"] == vote]
    deadlines = set()
    for realm in federation:
        (leg,) = [r for r in rows(realm, "FederalVoteLeg", "id", "proposal_id") if r["id"] == f"{vote}:{realm.cid}"]
        deadlines.add(realm.proposal(leg["proposal_id"])["deadline"])
    assert deadlines == {row["deadline"]}
    assert row["deadline"] > east.now() + HOUR  # the capital's two days, not the quarter's own hour
    capital.govern("federal_vote_cancel", {"vote": vote})
    for realm in (capital, east):
        realm.ok("governance_update", "ctl", voting_window=0)


def test_no_hook_sets_a_zone(federation, people):
    capital, east, _ = federation
    founder = people["founder"]
    errors = []
    for title, description in (("own zone", ""), ("quarter zone", east.cid), ("relayed zone", "")):
        capital.ok("proposal_create", founder, title=title, description=description)
        errors.append(capital.ok("codex_status", founder, query=True)["failures"]["x/procedure.py"]["on_proposal_create"]["error"])
    assert errors[:2] == ["realm_configure: a chapter never sets a time zone",
                          "quarter_configure: a chapter never sets a time zone"], errors
    assert errors[2].startswith("federation_message: "), errors  # the realm's own message: no chapter sends it
    assert (zone(capital), zone(east)) == (MADRID, TOKYO)
