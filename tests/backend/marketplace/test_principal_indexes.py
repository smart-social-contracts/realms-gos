"""Purchases and likes are read per principal through an index, not a scan."""

from ic_python_db import Database

from marketplace_backend.api import extensions as ext_api
from marketplace_backend.api import likes as likes_api
from marketplace_backend.api import status as status_api
from marketplace_backend.core import models

from .conftest import grant_license


def _publish(extension_id="demo"):
    grant_license("dev-1")
    r = ext_api.create_extension(
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
    assert r["success"], r


def _forget_principal_indexes():
    """Make every row look like it was written before the fields were indexed."""
    storage = Database.get_instance()._db_storage
    for key in list(storage.keys()):
        if key.startswith(("_fi:PurchaseEntity:realm_principal:", "_fi:LikeEntity:principal:")):
            storage.remove(key)
        if key.endswith(models._INDEX_BACKFILL_KEY):
            storage.remove(key)


def test_lookups_only_see_the_principals_rows(as_caller):
    _publish("a")
    _publish("b")
    ext_api.buy_extension("realm-1", "a")
    ext_api.buy_extension("realm-2", "b")
    assert [p.item_id for p in models.purchases_of("realm-1")] == ["a"]
    assert ext_api.has_purchased_extension("realm-2", "b")
    assert not ext_api.has_purchased_extension("realm-2", "a")


def test_backfill_indexes_rows_written_before_the_index(as_caller):
    _publish("a")
    ext_api.buy_extension("realm-1", "a")
    likes_api.like_item("realm-1", "ext", "a")
    _forget_principal_indexes()
    assert models.purchases_of("realm-1") == []

    models.backfill_principal_indexes()

    assert ext_api.has_purchased_extension("realm-1", "a")
    assert [r["item_id"] for r in likes_api.my_likes("realm-1")] == ["a"]
    # Buying again finds the old purchase instead of counting a new install
    assert ext_api.buy_extension("realm-1", "a")["action"] == "exists"


def test_backfill_runs_once(as_caller):
    models.backfill_principal_indexes()
    _publish("a")
    ext_api.buy_extension("realm-1", "a")
    storage = Database.get_instance()._db_storage
    for key in list(storage.keys()):
        if key.startswith("_fi:PurchaseEntity:realm_principal:"):
            storage.remove(key)
    models.backfill_principal_indexes()  # marker set: does not rescan
    assert models.purchases_of("realm-1") == []


def test_status_counts_without_loading_tables(as_caller):
    _publish("a")
    ext_api.buy_extension("realm-1", "a")
    s = status_api.get_status()
    assert s["extensions_count"] == 1
    assert s["purchases_count"] == 1
