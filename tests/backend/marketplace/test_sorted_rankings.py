"""Rankings read sorted indexes (Entity.top_by) instead of sorting every listing."""

import pytest
from ic_python_db import Database

from marketplace_backend.api import extensions as ext_api
from marketplace_backend.api import rankings as rank_api
from marketplace_backend.core import models

from .conftest import grant_license


def _publish(extension_id, installs=0, likes=0, verified=False, active=True):
    grant_license("dev-1")
    assert ext_api.create_extension(
        developer="dev-1",
        extension_id=extension_id,
        name=extension_id,
        description="d",
        version="1.0.0",
        price_e8s=0,
        icon="x",
        categories="tools",
        screenshots="",
        file_registry_canister_id="fr",
        file_registry_namespace=f"ext/{extension_id}/1.0.0",
        download_url="",
    )["success"]
    ext = models.ExtensionListingEntity[extension_id]
    ext.installs, ext.likes = installs, likes
    if verified:
        ext.verification_status = "verified"
    if not active:
        ext.is_active = False
    return ext


def _ids(rows):
    return [r["extension_id"] for r in rows]


@pytest.fixture
def no_table_scans(monkeypatch):
    """Rankings must not load whole tables."""

    def refuse(*_args, **_kwargs):
        raise AssertionError("a ranking loaded the whole table")

    monkeypatch.setattr(models.ExtensionListingEntity, "instances", classmethod(refuse))


def test_matches_a_full_sort_of_many_listings(as_caller, no_table_scans):
    rows = [(f"e{i}", (i * 37) % 23, (i * 11) % 7) for i in range(120)]
    for extension_id, installs, likes in rows:
        _publish(extension_id, installs, likes)
    # The old ranking: installs then likes, highest first; ties to the newer listing
    order = sorted(range(len(rows)), key=lambda i: (rows[i][1], rows[i][2], i), reverse=True)
    expected = [rows[i][0] for i in order][:20]
    assert _ids(rank_api.top_extensions_by_downloads(20)) == expected


def test_installs_and_likes_move_a_listing(as_caller, no_table_scans):
    _publish("a", installs=1)
    _publish("b", installs=5)
    assert _ids(rank_api.top_extensions_by_downloads(2)) == ["b", "a"]
    ext = models.ExtensionListingEntity["a"]
    ext.installs = 9
    assert _ids(rank_api.top_extensions_by_downloads(2)) == ["a", "b"]
    models.ExtensionListingEntity["b"].likes = 3
    assert _ids(rank_api.top_extensions_by_likes(1)) == ["b"]


def test_ties_broken_by_the_other_counter(as_caller, no_table_scans):
    _publish("few-likes", installs=4, likes=1)
    _publish("many-likes", installs=4, likes=8)
    _publish("most-installed", installs=6, likes=0)
    assert _ids(rank_api.top_extensions_by_downloads(3)) == ["most-installed", "many-likes", "few-likes"]
    assert _ids(rank_api.top_extensions_by_likes(3))[0] == "many-likes"


def test_inactive_and_unverified_are_skipped(as_caller, no_table_scans):
    _publish("gone", installs=100, active=False)
    _publish("plain", installs=50)
    _publish("verified", installs=10, verified=True)
    assert _ids(rank_api.top_extensions_by_downloads(10)) == ["plain", "verified"]
    assert _ids(rank_api.top_extensions_by_downloads(10, verified_only=True)) == ["verified"]


def test_backfill_indexes_listings_written_before_the_sorted_indexes(as_caller):
    _publish("old-a", installs=3, likes=1)
    _publish("old-b", installs=7, likes=0)
    legacy = models.ExtensionListingEntity["old-a"]
    storage = Database.get_instance()._db_storage
    # Rows from before this change: no sorted entries, and a None counter
    for key in list(storage.keys()):
        if key.startswith(("_si:", "_sb:")) or key.endswith(models._RANKING_BACKFILL_KEY):
            storage.remove(key)
    legacy.__dict__["_prop_likes"] = None
    legacy._save()
    assert rank_api.top_extensions_by_downloads(10) == []

    models.backfill_ranking_indexes()

    assert _ids(rank_api.top_extensions_by_downloads(10)) == ["old-b", "old-a"]
    assert models.ExtensionListingEntity["old-a"].likes == 0
    # Runs once: a second call finds the marker and changes nothing
    models.backfill_ranking_indexes()
    assert len([k for k in storage.keys() if k.startswith("_si:ExtensionListingEntity:installs,likes:d|")]) == 2
