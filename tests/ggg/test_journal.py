"""The realm's official journal (docs/codices/layered-codex.md §9.6, realms-extensions#54), in process: one issue a day,
the day's entries on the capital's clock, computed when read. tests/deployed/test_journal.py runs the same on the wasm."""

from datetime import datetime, timezone

import fake_chain
import world
from core import journal, localtime
from ggg import Extension, JournalEntry, LayeredCodex, Realm
from kit import call, denied, fire_timers, later, ok, rows
from test_installs import (HOUR, change_list, expected_id, fire_due, install, own_layer, realm,  # noqa: F401
                           run_job, schedule, status)

DAY = 86400
CAPITAL = "capital-canister"
# What the journal extension's manifest allows an entry to keep (extensions/journal/manifest.json).
RULES = {"config": {"personal_data": {"entry_fields": ["codex", "layers", "proposal", "activates_at"]}}}


def utc(*when):
    return int(datetime(*when, tzinfo=timezone.utc).timestamp())


def at(*when):
    fake_chain.state["now"] = utc(*when)


def issue(day=None, who="alice", **more):
    args = {"day": day} if day else {}
    return ok("journal_issue", who, **args, **more)["issue"]


def refused(day, kind="not_found"):
    out = call("journal_issue", "alice", day=day)
    assert not out["ok"] and out["kind"] == kind, out
    return out["error"]


def rules():
    Extension(id="journal", manifest=RULES)


def zone(name):
    Realm["1"].timezone = name
    localtime.use(name)


def entries_of(day):
    return [(e["kind"], e["at"]) for e in issue(day)["entries"]]


# ---- the journal exists, and so does an issue for every day


def test_a_new_realm_has_a_journal_and_an_issue_for_today_with_no_entries():
    at(2026, 11, 9, 10)
    world.build()
    out = ok("journal_issue", fake_chain.ANONYMOUS)  # public: a visitor reads it
    assert out["issued"] is True and out["capital"] == fake_chain.self_id()
    assert out["issue"] == {"day": "2026-11-09", "number": 1, "first_day": "2026-11-09", "today": "2026-11-09",
                            "timezone": "UTC", "entries": []}


def test_every_day_from_the_first_to_today_has_an_issue_with_or_without_entries():
    at(2026, 11, 9, 10)
    world.build()
    at(2026, 11, 12, 23)
    assert [(issue(d)["day"], issue(d)["number"], issue(d)["entries"]) for d in ("2026-11-09", "2026-11-10", "2026-11-12")] == [
        ("2026-11-09", 1, []), ("2026-11-10", 2, []), ("2026-11-12", 4, [])]
    assert issue()["day"] == "2026-11-12" and issue()["number"] == 4


def test_a_day_after_today_or_before_the_first_one_has_no_issue_and_a_bad_date_is_refused():
    at(2026, 11, 9, 10)
    world.build()
    assert "today is 2026-11-09" in refused("2026-11-10")
    assert "begins on 2026-11-09" in refused("2026-11-08")
    for bad in ("2026-02-30", "09/11/2026", "2026-1-9", "tomorrow", "2026-11-09T00:00", ""):
        if bad:
            assert "YYYY-MM-DD" in refused(bad, "invalid_argument")
    assert call("journal_issue", "alice", day=20261109)["kind"] == "invalid_argument"


def test_the_first_day_is_fixed_when_the_realm_first_starts_with_the_journal():
    at(2026, 11, 9, 10)
    world.build()
    at(2026, 12, 1, 10)
    journal.begin()  # every start of the realm runs it: an upgrade does not move the first day
    assert issue()["first_day"] == "2026-11-09" and issue()["number"] == 23


# ---- the day is the local day on the capital's clock


def test_the_day_ends_at_midnight_on_the_capitals_clock_also_across_a_summer_time_change():
    at(2026, 3, 27, 12)
    world.build()
    zone("Europe/Madrid")
    rules()
    # Spring forward on 2026-03-29: Madrid's 29th is 23 hours, from 28th 23:00 UTC to 29th 22:00 UTC.
    # Fall back on 2026-10-25: its 25th is 25 hours, from 24th 22:00 UTC to 25th 23:00 UTC.
    for when in ((2026, 3, 28, 22, 59, 59), (2026, 3, 28, 23), (2026, 3, 29, 21, 59, 59), (2026, 3, 29, 22),
                 (2026, 10, 25, 22, 59, 59), (2026, 10, 25, 23)):
        at(*when)
        journal.record("codex_installed")
    at(2026, 11, 1, 12)
    assert [len(issue(d)["entries"]) for d in ("2026-03-28", "2026-03-29", "2026-03-30")] == [1, 2, 1]
    assert [len(issue(d)["entries"]) for d in ("2026-10-25", "2026-10-26")] == [1, 1]
    assert issue("2026-03-29")["number"] == 3


def test_the_day_follows_the_realms_zone_not_utc():
    at(2026, 11, 9, 23, 30)  # still the 9th in UTC, already the 10th in Madrid
    world.build()
    zone("Europe/Madrid")
    journal.record("codex_installed")
    assert issue()["day"] == "2026-11-10" and len(issue()["entries"]) == 1
    assert issue()["timezone"] == "Europe/Madrid"


def test_a_change_of_zone_moves_no_past_entry():
    at(2026, 11, 9, 8)
    world.build()
    zone("Europe/Madrid")
    at(2026, 11, 9, 23, 30)  # the 10th in Madrid
    journal.record("codex_installed")
    zone("America/Los_Angeles")  # where it is the 9th
    at(2026, 11, 10, 12)
    assert [kind for kind, _ in entries_of("2026-11-10")] == ["codex_installed"]
    assert entries_of("2026-11-09") == []


# ---- entries, and the personal-data rules


def test_an_entry_keeps_only_the_fields_the_personal_data_rules_allow():
    world.build()
    rules()
    journal.record("codex_installed", codex="sha256:ab", proposal="P-1", proposer="2vxsx-fae", nickname="Alice")
    assert issue()["entries"][0]["details"] == {"codex": "sha256:ab", "proposal": "P-1"}
    assert [e.details for e in rows(JournalEntry)] == [{"codex": "sha256:ab", "proposal": "P-1"}]  # never stored


def test_a_realm_without_the_journal_extension_has_no_rules_and_keeps_no_details():
    world.build()
    journal.record("codex_installed", codex="sha256:ab")
    assert issue()["entries"][0]["details"] == {}


def test_entries_come_in_the_order_they_were_recorded_and_are_never_changed():
    world.build()
    rules()
    for n in range(3):
        later(60)
        journal.record("codex_installed", codex=f"sha256:{n}")
    today, first = issue()["day"], issue()["entries"]
    assert [e["details"]["codex"] for e in first] == ["sha256:0", "sha256:1", "sha256:2"]
    later(DAY)
    assert issue(today)["entries"] == first  # a past issue reads the same tomorrow


# ---- a codex install is an entry of the day it switched, and publishing changes nothing


def test_the_switch_of_an_install_makes_an_entry_with_the_codex_and_its_layers(realm):  # noqa: F811
    rules()
    layer = own_layer()
    install(change_list(layer))
    [entry] = issue()["entries"]
    assert entry["kind"] == "codex_installed" and entry["at"] == fake_chain.now()
    assert entry["details"] == {"codex": expected_id(layer), "layers": [{"source": "arcadia", "versions": []}],
                                "proposal": "", "activates_at": 0}


def test_publishing_changes_no_activates_at_and_no_install(realm):  # noqa: F811
    rules()
    layer = own_layer()
    p = schedule(change_list(layer))
    run_job()
    staged = LayeredCodex[expected_id(layer)]
    due = staged.activates_at
    assert staged.status == "pending" and due == fake_chain.state["now"] + HOUR
    for minutes in (0, 10, 30):  # the journal is read, issue after issue, while the install waits
        later(minutes * 60)
        assert issue()["entries"] == []
        ok("journal_issue", fake_chain.ANONYMOUS)
    assert (staged.status, staged.activates_at) == ("pending", due) and status()["codex"] is None
    later(due - fake_chain.now())
    fire_timers()  # the proposal's time: the install switches
    fire_due()
    [entry] = issue()["entries"]
    assert LayeredCodex[expected_id(layer)].activates_at == due and entry["details"]["activates_at"] == due
    assert entry["details"]["proposal"] == p and entry["at"] == due


def test_a_legacy_single_file_install_makes_no_entry(realm):  # noqa: F811
    from test_codex import SOURCE, manifest

    rules()
    Realm["1"].require_marketplace_approval = False
    ok("codex_install", "ctl", manifest=manifest(), source=SOURCE)
    assert issue()["entries"] == []


# ---- a quarter issues nothing and shows the capital's


def quarter():
    world.build()
    row = Realm["1"]
    row.is_quarter, row.is_capital, row.federation_realm_id = True, False, CAPITAL


def test_a_quarter_issues_nothing_and_names_the_capital_whose_journal_it_is():
    at(2026, 11, 9, 10)
    quarter()
    assert ok("journal_issue", "alice") == {"ok": True, "issued": False, "capital": CAPITAL}
    assert journal.record("codex_installed") is None and JournalEntry.count() == 0


def test_a_quarter_shows_the_capitals_issue_by_asking_it():
    at(2026, 11, 9, 10)
    quarter()
    capitals = {"ok": True, "issued": True, "capital": CAPITAL,
                "issue": {"day": "2026-11-09", "number": 40, "first_day": "2026-10-01", "today": "2026-11-09",
                          "timezone": "Asia/Tokyo", "entries": []}}
    peer = fake_chain.peer(CAPITAL, journal_issue=lambda arg: capitals)
    out = ok("journal_capital_issue", "alice", day="2026-11-09")
    assert out["issued"] is False and out["capital"] == CAPITAL and out["issue"] == capitals["issue"]
    assert fake_chain.state["calls"][-1] == (CAPITAL, "journal_issue", '{"day": "2026-11-09"}')
    ok("journal_capital_issue", "alice")
    assert fake_chain.state["calls"][-1] == (CAPITAL, "journal_issue", "{}") and len(peer.calls) == 2


def test_the_capitals_refusal_reaches_the_quarters_reader():
    quarter()
    fake_chain.peer(CAPITAL, journal_issue={"ok": False, "error": "there is no issue for 2026-01-01", "kind": "not_found"})
    assert "there is no issue for 2026-01-01" in denied("journal_capital_issue", "alice", day="2026-01-01")


def test_a_realm_that_is_not_a_quarter_issues_its_own_journal_and_does_not_forward():
    world.build()
    out = call("journal_capital_issue", "alice")
    assert not out["ok"] and out["kind"] == "conflict" and not fake_chain.state["calls"]


def test_only_a_logged_in_reader_can_ask_a_quarter_to_call_its_capital():
    quarter()
    assert call("journal_capital_issue", fake_chain.ANONYMOUS)["denied"] is True
    assert not fake_chain.state["calls"]
