"""Unit tests for api/likes.py and api/rankings.py."""

from marketplace_backend.api import codices as cx_api
from marketplace_backend.api import extensions as ext_api
from marketplace_backend.api import likes as likes_api
from marketplace_backend.api import rankings as rank_api

from .conftest import grant_license


def _create_ext(extension_id="voting", **kw):
    base = dict(
        developer="dev-1",
        extension_id=extension_id,
        name=f"Ext {extension_id}",
        description="",
        version="0.1.0",
        price_e8s=0,
        icon="",
        categories="other",
        screenshots="",
        file_registry_canister_id="fr-1",
        file_registry_namespace=f"ext/{extension_id}/0.1.0",
    )
    base.update(kw)
    # Publishing is license-gated; the license is scaffolding here, not the
    # subject of these tests.
    grant_license(base["developer"])
    return ext_api.create_extension(**base)


def _create_codex(codex_id="dominion/x", **kw):
    base = dict(
        developer="dev-1",
        codex_id=codex_id,
        realm_type=codex_id.split("/")[0] if "/" in codex_id else "",
        name=f"Codex {codex_id}",
        description="",
        version="0.1.0",
        price_e8s=0,
        icon="",
        categories="other",
        file_registry_canister_id="fr-1",
        file_registry_namespace=f"codex/{codex_id}/0.1.0",
    )
    base.update(kw)
    grant_license(base["developer"])
    return cx_api.create_codex(**base)


def test_like_idempotent_and_counter_in_sync():
    _create_ext()
    r = likes_api.like_item("user-A", "ext", "voting")
    assert r["action"] == "created"
    r2 = likes_api.like_item("user-A", "ext", "voting")
    assert r2["action"] == "exists"
    e = ext_api.get_extension_details("voting")["extension"]
    assert e["likes"] == 1

    likes_api.like_item("user-B", "ext", "voting")
    e = ext_api.get_extension_details("voting")["extension"]
    assert e["likes"] == 2


def test_unlike_before_like_is_noop_and_counter_floor_zero():
    _create_ext()
    r = likes_api.unlike_item("user-A", "ext", "voting")
    assert r["action"] == "noop"
    e = ext_api.get_extension_details("voting")["extension"]
    assert e["likes"] == 0


def test_unlike_decrements():
    _create_ext()
    likes_api.like_item("user-A", "ext", "voting")
    likes_api.like_item("user-B", "ext", "voting")
    likes_api.unlike_item("user-A", "ext", "voting")
    e = ext_api.get_extension_details("voting")["extension"]
    assert e["likes"] == 1
    assert likes_api.has_liked("user-A", "ext", "voting") is False
    assert likes_api.has_liked("user-B", "ext", "voting") is True


def test_my_likes_returns_only_caller_rows():
    _create_ext()
    _create_codex()
    likes_api.like_item("user-A", "ext", "voting")
    likes_api.like_item("user-B", "codex", "dominion/x")
    rows = likes_api.my_likes("user-A")
    assert len(rows) == 1
    assert rows[0]["item_kind"] == "ext"
    assert rows[0]["item_id"] == "voting"


def test_top_extensions_ranks_by_installs_then_likes():
    _create_ext("a")
    _create_ext("b")
    _create_ext("c")
    ext_api.buy_extension("u1", "a")
    ext_api.buy_extension("u2", "a")
    ext_api.buy_extension("u1", "b")
    likes_api.like_item("u1", "ext", "c")  # c has 0 installs but 1 like
    top = rank_api.top_extensions_by_downloads(10)
    ids = [t["extension_id"] for t in top]
    assert ids[:3] == ["a", "b", "c"]


def test_top_extensions_by_likes():
    _create_ext("a")
    _create_ext("b")
    _create_ext("c")
    likes_api.like_item("u1", "ext", "b")
    likes_api.like_item("u2", "ext", "b")
    likes_api.like_item("u1", "ext", "a")
    top = rank_api.top_extensions_by_likes(10)
    ids = [t["extension_id"] for t in top]
    assert ids[0] == "b"
    assert "a" in ids[:2]


def test_top_codices_by_downloads():
    _create_codex("dominion/a")
    _create_codex("dominion/b")
    cx_api.buy_codex("u1", "dominion/b")
    cx_api.buy_codex("u2", "dominion/b")
    cx_api.buy_codex("u1", "dominion/a")
    top = rank_api.top_codices_by_downloads(10)
    assert [t["codex_id"] for t in top[:2]] == ["dominion/b", "dominion/a"]


def test_verified_only_filter_in_rankings():
    _create_ext("verified-one")
    _create_ext("plain-one")
    # mark one verified
    from marketplace_backend.core.models import ExtensionListingEntity
    ExtensionListingEntity["verified-one"].verification_status = "verified"

    full = rank_api.top_extensions_by_downloads(10)
    assert {t["extension_id"] for t in full} == {"verified-one", "plain-one"}

    only_v = rank_api.top_extensions_by_downloads(10, verified_only=True)
    assert {t["extension_id"] for t in only_v} == {"verified-one"}
