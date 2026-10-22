"""The realm's listing: what it sends to the registry, and only when it changed."""

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src" / "realm_backend"))
sys.modules.setdefault("_cdk", MagicMock())

from core import listing  # noqa: E402

STATUS = {
    "realm_name": "Harbour Town",
    "realm_manifesto": "  A town that decides together.  ",
    "realm_stage": "alpha",
    "users_count": 1_250,
    "last_activity_at": 1_792_661_582,
    "logo_url": "",
}
ZONES = [{"h3_index": "8839540ad3fffff", "name": "North Quay", "zone_type": "mixed", "user_count": 1}]


def _zone(i, h3, land=None, name="Zone"):
    return types.SimpleNamespace(_id=str(i), h3_index=h3, land=land, name=name, zone_type="mixed")


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    listing._round.update(started=0, cursor=1, zones={})
    listing._sent.clear()
    monkeypatch.setattr(listing, "ic", MagicMock())
    yield


def test_payload_parts():
    parts = listing.build_payload(STATUS, ZONES)
    assert parts["summary"] == {
        "name": "Harbour Town", "users_count": 1_250,
        "last_activity_at": 1_792_661_582, "stage": "alpha",
    }
    assert parts["manifesto"] == "A town that decides together."
    assert parts["zones"] == ZONES


def test_stage_and_logo_are_only_sent_when_the_registry_accepts_them():
    summary = listing.build_payload(
        dict(STATUS, realm_stage="setup", logo_url="/custom/logo.png"), []
    )["summary"]
    assert "stage" not in summary and "logo" not in summary
    summary = listing.build_payload(dict(STATUS, logo_url="https://x.icp0.io/logo.png"), [])["summary"]
    assert summary["logo"] == "https://x.icp0.io/logo.png"


def test_first_send_has_everything_then_nothing():
    parts = listing.build_payload(STATUS, ZONES)
    first = listing.changed_parts(parts, {})
    assert first["manifesto"] and first["zones"] and first["name"] == "Harbour Town"
    assert listing.changed_parts(parts, parts) == {}


def test_a_new_member_sends_the_summary_only():
    sent = listing.build_payload(STATUS, ZONES)
    parts = listing.build_payload(dict(STATUS, users_count=1_251), ZONES)
    payload = listing.changed_parts(parts, sent)
    assert payload["users_count"] == 1_251
    assert "manifesto" not in payload and "zones" not in payload


def test_new_zones_are_sent_with_the_summary():
    sent = listing.build_payload(STATUS, ZONES)
    more = ZONES + [dict(ZONES[0], h3_index="8839540ad1fffff")]
    payload = listing.changed_parts(listing.build_payload(STATUS, more), sent)
    assert len(payload["zones"]) == 2 and "manifesto" not in payload
    assert payload["name"] == "Harbour Town"


def test_zone_scan_batches_skips_parcels_and_counts_shared_cells(monkeypatch):
    rows = [_zone(1, "8839540ad3fffff"), _zone(2, "8839540ad3fffff"),
            _zone(3, "8839540ad1fffff", land=object()), _zone(4, "")]
    pages = {1: rows[:2], 3: rows[2:]}
    ggg = types.ModuleType("ggg")
    ggg.Zone = types.SimpleNamespace(load_some=lambda cursor, n: pages.get(cursor, []))
    monkeypatch.setitem(sys.modules, "ggg", ggg)
    monkeypatch.setattr(listing, "ZONE_BATCH", 2)

    assert listing._scan_zones() is False
    assert listing._round["cursor"] == 3
    assert listing._scan_zones() is False
    listing._round["cursor"] = 99
    assert listing._scan_zones() is True
    assert list(listing._round["zones"].values()) == [
        {"h3_index": "8839540ad3fffff", "name": "Zone", "zone_type": "mixed", "user_count": 2}
    ]


def test_zone_count_is_capped(monkeypatch):
    rows = [_zone(i, f"8839540a{i:07x}") for i in range(1, 6)]
    ggg = types.ModuleType("ggg")
    ggg.Zone = types.SimpleNamespace(load_some=lambda cursor, n: rows if cursor == 1 else [])
    monkeypatch.setitem(sys.modules, "ggg", ggg)
    monkeypatch.setattr(listing, "ZONES_MAX", 3)
    assert listing._scan_zones() is True
    assert len(listing._round["zones"]) == 3


def test_a_stuck_round_is_replaced(monkeypatch):
    now = {"t": 10_000}
    monkeypatch.setattr(listing, "_now_s", lambda: now["t"])
    listing._begin_round()
    assert listing._round["started"] == 10_000
    listing._round["cursor"] = 7
    now["t"] += 60
    listing._begin_round()
    assert listing._round["cursor"] == 7
    now["t"] += listing.ROUND_S
    listing._begin_round()
    assert listing._round["cursor"] == 1


def _drive(gen, reply):
    try:
        next(gen)
        gen.send(reply)
    except StopIteration:
        pass


@pytest.fixture
def realm_env(monkeypatch):
    realm = types.SimpleNamespace(is_quarter=False)
    ggg = types.ModuleType("ggg")
    ggg.Realm = types.SimpleNamespace(load=lambda key: realm)
    monkeypatch.setitem(sys.modules, "ggg", ggg)
    status = types.ModuleType("api.status")
    status.get_status = lambda: dict(STATUS)
    monkeypatch.setitem(sys.modules, "api.status", status)
    setup = types.ModuleType("core.setup")
    setup.get_realm_registry_canister_id = lambda r: "e56by-5iaaa-aaaas-qg72a-cai"
    monkeypatch.setitem(sys.modules, "core.setup", setup)
    sent = []

    class _Registry:
        def __init__(self, principal):
            pass

        def listing_publish(self, args):
            sent.append(json.loads(args))
            return "call"

    monkeypatch.setattr(listing, "ListingRegistry", _Registry)
    monkeypatch.setattr(listing, "Principal", MagicMock())
    return types.SimpleNamespace(realm=realm, setup=setup, sent=sent)


def test_publish_records_what_the_registry_accepted(realm_env):
    _drive(listing._publish(), {"Ok": json.dumps({"success": True, "revision": 1})})
    assert realm_env.sent[0]["name"] == "Harbour Town"
    assert listing._sent["manifesto"] == "A town that decides together."
    gen = listing._publish()
    with pytest.raises(StopIteration):
        next(gen)
    assert len(realm_env.sent) == 1


def test_a_refused_listing_is_sent_again(realm_env):
    _drive(listing._publish(), {"Ok": json.dumps({"success": False, "error": "no"})})
    assert listing._sent == {}
    _drive(listing._publish(), {"Err": "unreachable"})
    assert listing._sent == {}
    assert len(realm_env.sent) == 2


def test_quarters_and_unregistered_realms_publish_nothing(realm_env):
    realm_env.realm.is_quarter = True
    _drive(listing._publish(), None)
    realm_env.realm.is_quarter = False
    realm_env.setup.get_realm_registry_canister_id = lambda r: ""
    _drive(listing._publish(), None)
    assert realm_env.sent == []
