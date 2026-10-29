"""Paged list and search endpoints: newest first, a bounded read per call."""

from marketplace_backend.api import codices as codex_api
from marketplace_backend.api import extensions as ext_api
from marketplace_backend.core import models

from .conftest import grant_license


def _publish(extension_id, **overrides):
    grant_license("dev-1")
    fields = dict(
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
    )
    fields.update(overrides)
    assert ext_api.create_extension(**fields)["success"]


def _walk(fetch):
    """Every listing a paged endpoint returns, following next_cursor to 0."""
    seen, cursor = [], 0
    while True:
        page = fetch(cursor)
        seen.extend(r["extension_id"] for r in page["listings"])
        cursor = page["next_cursor"]
        if cursor == 0:
            return seen


def test_pages_come_newest_first_and_cover_everything(as_caller):
    for i in range(7):
        _publish(f"e{i}")
    page = ext_api.list_extensions_page(0, 3, False)
    assert [r["extension_id"] for r in page["listings"]] == ["e6", "e5", "e4"]
    assert page["next_cursor"] != 0
    assert _walk(lambda c: ext_api.list_extensions_page(c, 3, False)) == [
        f"e{i}" for i in reversed(range(7))
    ]


def test_inactive_and_unverified_listings_are_skipped(as_caller):
    for i in range(4):
        _publish(f"e{i}")
    ext_api.delist_extension("dev-1", "e1")
    assert _walk(lambda c: ext_api.list_extensions_page(c, 10, False)) == ["e3", "e2", "e0"]
    assert _walk(lambda c: ext_api.list_extensions_page(c, 10, True)) == []


def test_each_call_reads_at_most_the_scan_budget(as_caller, monkeypatch):
    monkeypatch.setattr(models, "SCAN_BUDGET", 3)
    for i in range(5):
        _publish(f"e{i}")
    for i in range(5):
        ext_api.delist_extension("dev-1", f"e{i}")
    # Nothing matches, but each call stops after 3 reads and says where to go on
    top = models.ExtensionListingEntity.max_id()
    calls, cursor = 0, 0
    while True:
        page = ext_api.list_extensions_page(cursor, 10, False)
        assert page["listings"] == []
        calls += 1
        cursor = page["next_cursor"]
        if cursor == 0:
            break
        assert cursor == top - 3 * calls
    assert calls == -(-top // 3)


def test_search_pages_filter_like_search(as_caller):
    _publish("alpha", description="voting tool")
    _publish("beta", description="treasury")
    _publish("gamma", categories="voting")
    expected = sorted(r["extension_id"] for r in ext_api.search_extensions("voting", False))
    paged = _walk(lambda c: ext_api.search_extensions_page("voting", c, 1, False))
    assert sorted(paged) == expected == ["alpha", "gamma"]


def test_empty_tables_finish_at_once(as_caller):
    assert ext_api.list_extensions_page(0, 10, False) == {"listings": [], "next_cursor": 0}
    assert codex_api.search_codices_page("x", 0, 10, False) == {"listings": [], "next_cursor": 0}
