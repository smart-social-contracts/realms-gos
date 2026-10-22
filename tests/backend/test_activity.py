"""Last activity: what counts, how often it is written, how quarters report it."""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src" / "realm_backend"))
sys.modules.setdefault("_cdk", MagicMock())

from core import activity  # noqa: E402
from core import extension_bridge as eb  # noqa: E402
from core import quarter_drift as qd  # noqa: E402


@pytest.fixture
def realm(monkeypatch):
    row = types.SimpleNamespace(last_activity_at=0)
    ggg = types.ModuleType("ggg")
    ggg.Realm = types.SimpleNamespace(load=lambda key: row if key == "1" else None)
    monkeypatch.setitem(sys.modules, "ggg", ggg)
    monkeypatch.setattr(activity, "_self_id", lambda: "realm-canister")
    monkeypatch.setattr(activity, "_now_s", lambda: 1_000_000)
    return row


@pytest.mark.parametrize("caller", ["", "system", "2vxsx-fae", "realm-canister"])
def test_only_people_count(realm, caller):
    assert activity.record(caller) is False
    assert realm.last_activity_at == 0


def test_a_person_is_recorded(realm):
    assert activity.record("alice") is True
    assert realm.last_activity_at == 1_000_000


def test_at_most_one_write_a_minute(realm):
    activity.record("alice", now_s=1_000)
    assert activity.record("bob", now_s=1_030) is False
    assert realm.last_activity_at == 1_000
    assert activity.record("bob", now_s=1_060) is True
    assert realm.last_activity_at == 1_060


def test_capital_shows_the_latest_of_the_federation():
    capital = types.SimpleNamespace(last_activity_at=500)
    quarters = [types.SimpleNamespace(last_activity_at=900),
                types.SimpleNamespace(last_activity_at=0),
                types.SimpleNamespace()]
    assert activity.federation_latest(capital, quarters) == 900
    assert activity.federation_latest(None, []) == 0


def test_quarter_reports_its_activity_in_the_gossip_block():
    block = qd.build_directory_self("q1", None, last_activity_at=1234)
    assert block["last_activity_at"] == 1234
    assert "last_activity_at" not in qd.build_directory_self("q1", None)


def test_capital_keeps_the_newest_report():
    quarter = types.SimpleNamespace(last_activity_at=2000)
    assert qd.apply_self_report_to_quarter(quarter, {"last_activity_at": 1500}) is False
    assert quarter.last_activity_at == 2000
    assert qd.apply_self_report_to_quarter(quarter, {"last_activity_at": 2500}) is True
    assert quarter.last_activity_at == 2500
    assert qd.apply_self_report_to_quarter(quarter, {"last_activity_at": "junk"}) is False


@pytest.fixture
def bridge(monkeypatch, realm):
    monkeypatch.setattr(eb, "authorize", lambda action, caps: None)
    monkeypatch.setattr(eb, "_cedar_check", lambda *a, **k: None)
    monkeypatch.setattr(eb, "dispatch", lambda *a, **k: {"ok": True})
    return eb.make_rpc_handler("land_registry", [], "alice")


def test_extension_writes_count(bridge, realm):
    write = next(v for v in sorted(eb.WRITE_VERBS) if v != "log.write")
    bridge("ctx", write, {})
    assert realm.last_activity_at == 1_000_000


@pytest.mark.parametrize("verb", ["log.write", sorted(eb.READ_VERBS)[0]])
def test_reads_and_logging_do_not_count(bridge, realm, verb):
    bridge("ctx", verb, {})
    assert realm.last_activity_at == 0
