"""The realm's official journal on the real wasm (docs/codices/layered-codex.md §9.6, realms-extensions#54): an issue
for every day from the realm's first one, the day ending at midnight on the capital's clock across a change of summer
time, an install and a revert as entries, an upgrade that keeps the journal, a scheduled install the journal cannot
move, and a quarter that issues nothing and shows its capital's issue. tests/ggg/test_journal.py runs the same rules in
process.

The tests of this module run in order on one network: the clock only goes forward.
"""

from datetime import datetime, timezone

import pytest
from network import Network
from test_installs import Site, active_id, expected_id, install, own_layer, settled, status

DAY = 86400
HOUR = 3600
# What the journal extension's manifest allows an entry to keep (extensions/journal/manifest.json).
RULES = ("import ggg\nggg.Extension(id='journal', manifest={'config': {'personal_data': {'entry_fields': "
         "['codex', 'layers', 'proposal', 'activates_at', 'reverted']}}})")
RECORD = "from core import journal\nfor _ in range({times}):\n    journal.record('codex_installed')"
WATER = b'from realms.legislation import above\n\nclass Water(above("water")):\n    pass\n'
# A query may spend 5 billion instructions: an issue of this many entries costs a small part of it.
BUSY_DAY = 60
READ_BUDGET = 500_000_000


@pytest.fixture(scope="module")
def net(wasm_of, people):
    return Network(wasm_of("production"), people)


@pytest.fixture(scope="module")
def capital(net):
    return net.realm("capital", members=("alice",))


@pytest.fixture(scope="module")
def north(net):
    return net.realm("north", members=("alice",))


@pytest.fixture(scope="module")
def site(net):
    return Site(net.web)


def utc(*when):
    return int(datetime(*when, tzinfo=timezone.utc).timestamp())


def go_to(net, instant):
    net.wait(instant - net.pic.get_time()["nanos_since_epoch"] // 10**9)


def date(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%d")


def issue(realm, day=None, query=True):
    return realm.ok("journal_issue", "alice", query=query, **({"day": day} if day else {}))["issue"]


def refused(realm, day):
    out = realm.call("journal_issue", {"day": day}, query=True)
    assert not out["ok"], out
    return out


def record(realm, times=1):
    out = realm.shell(RECORD.format(times=times))
    assert "Traceback" not in out, out


def test_a_a_new_realm_has_a_journal_and_an_issue_for_today_with_no_entries(capital):
    out = capital.call("journal_issue", {}, query=True)  # anonymous: the journal is public
    today = date(capital.now())
    assert out["ok"] and out["issued"] is True and out["capital"] == capital.cid
    assert out["issue"] == {"realm": "capital", "day": today, "number": 1, "first_day": today, "today": today,
                            "timezone": "UTC", "entries": []}


def test_b_every_day_from_the_first_has_an_issue_and_a_day_after_today_or_before_the_first_has_none(net, capital):
    first = issue(capital)["first_day"]
    net.wait(3 * DAY)
    today = date(capital.now())
    assert [(issue(capital, d)["number"], issue(capital, d)["entries"]) for d in (first, date(capital.now() - DAY), today)] \
        == [(1, []), (3, []), (4, [])]
    assert refused(capital, date(capital.now() + DAY))["kind"] == "not_found"
    assert refused(capital, date(utc(2021, 1, 1)))["kind"] == "not_found"
    assert refused(capital, "2021-02-30")["kind"] == "invalid_argument"


def test_c_the_day_ends_at_midnight_on_the_capitals_clock_across_the_change_of_summer_time(net, capital):
    capital.govern("realm_configure", {"timezone": "Europe/Madrid"})
    # Madrid falls back on 2021-10-31: that day is 25 hours, from the 30th 22:00 UTC to the 31st 23:00 UTC.
    for when in ((2021, 10, 30, 21, 59, 30), (2021, 10, 30, 22, 0, 30), (2021, 10, 31, 22, 59, 30),
                 (2021, 10, 31, 23, 0, 30)):
        go_to(net, utc(*when))
        record(capital)
    assert issue(capital)["day"] == "2021-11-01" and issue(capital)["timezone"] == "Europe/Madrid"
    assert [len(issue(capital, d)["entries"]) for d in ("2021-10-30", "2021-10-31", "2021-11-01")] == [1, 2, 1]
    first = datetime.fromisoformat(issue(capital)["first_day"])
    assert issue(capital, "2021-10-31")["number"] == (datetime(2021, 10, 31) - first).days + 1


def test_d_an_upgrade_keeps_the_journal_and_does_not_move_its_first_day(net, capital):
    before = issue(capital, "2021-10-31")
    net.wait(600)
    capital.upgrade()
    assert issue(capital, "2021-10-31") == before and issue(capital)["first_day"] == before["first_day"]


def test_e_an_install_is_an_entry_with_what_the_personal_data_rules_allow(net, capital, site, people):
    capital.shell(RULES)
    layer = own_layer(people["founder"])
    job = install(net, capital, site.change_list(layer))
    assert job["status"] == "done"
    net.wait(1)
    [entry] = [e for e in issue(capital)["entries"] if e["kind"] == "codex_installed" and e["details"]]
    assert entry["details"] == {"codex": expected_id(layer), "layers": [{"source": "arcadia", "versions": []}],
                                "proposal": entry["details"]["proposal"], "activates_at": 0}
    assert entry["details"]["proposal"].startswith("P-") and entry["at"] <= capital.now()


def test_f_publishing_moves_no_activates_at_and_no_install(net, capital, site, people):
    current = active_id(capital)
    layer = {**own_layer(people["founder"]), "arcadia/water": WATER}
    effective_at = capital.now() + HOUR
    proposal = capital.govern("codex_install", site.change_list(layer, replaces=current), effective_at=effective_at)
    assert settled(net, capital)["step"] == "wait"
    entries = len(issue(capital)["entries"])
    for _ in range(3):  # the journal is read, and printed by whoever reads it, while the install waits
        issue(capital)
        net.wait(10 * 60)
    seen = status(capital)
    assert (seen["pending"]["status"], seen["pending"]["activates_at"]) == ("pending", effective_at)
    assert seen["job"]["step"] == "wait" and seen["job"]["status"] == "running"  # still staged, as it was
    assert active_id(capital) == current and len(issue(capital)["entries"]) == entries
    capital.run_at_effective_at(proposal)
    switched = issue(capital)["entries"][-1]
    assert switched["details"]["activates_at"] == effective_at and switched["at"] >= effective_at
    assert status(capital)["codex"]["activates_at"] == effective_at


def test_g_a_quarter_issues_nothing_and_shows_the_capitals_issue(net, capital, north):
    net.register_quarters(capital, north)
    out = north.call("journal_issue", {}, query=True)
    assert out == {"ok": True, "issued": False, "capital": capital.cid}
    relayed = north.ok("journal_capital_issue", "alice")
    assert relayed["issued"] is False and relayed["capital"] == capital.cid
    assert relayed["issue"] == issue(capital) and relayed["issue"]["entries"]
    assert north.call("journal_capital_issue", {})["denied"] is True  # a visitor cannot make a quarter call its capital
    assert north.shell("from core import journal\nprint(journal.record('codex_installed'))").strip() == "None"
    assert capital.call("journal_capital_issue", {}, sender=net.people["alice"])["kind"] == "conflict"


def test_h_what_reading_an_issue_costs(capital, costs):
    record(capital, BUSY_DAY)
    spent = costs.measure(capital, f"journal: an issue of {BUSY_DAY}+ entries, read", "from core import journal\njournal.issue()")
    assert spent < READ_BUDGET, spent


def test_i_a_realm_west_of_utc_started_after_utc_midnight_has_an_issue_for_today(net, people):
    go_to(net, utc(2021, 11, 3, 1))  # the 2nd in New York, where it is 21:00
    west = net.realm("west", members=("alice",))
    west.govern("realm_configure", {"timezone": "America/New_York"})
    record(west)
    today = issue(west)
    assert (today["day"], today["first_day"], today["number"], len(today["entries"])) == ("2021-11-02", "2021-11-02", 1, 1)


def test_j_a_revert_by_a_vote_is_an_entry_naming_the_codex_restored_and_the_one_reverted(capital):
    reverted = status(capital)["codex"]
    capital.govern("codex_revert", {"codex": reverted["previous"]})
    assert active_id(capital) == reverted["previous"]
    entry = issue(capital)["entries"][-1]
    assert entry["kind"] == "codex_reverted" and entry["at"] <= capital.now()
    assert entry["details"] == {"codex": reverted["previous"], "reverted": reverted["id"],
                                "layers": [{"source": "arcadia", "versions": []}]}
