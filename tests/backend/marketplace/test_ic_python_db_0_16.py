"""What ic-python-db 0.16 asks of the marketplace.

* A listing raises ``MigrationError`` when a row's ``migrate()`` raises; 0.14 skipped the
  row, so the listing came back short with only a log line. None of the marketplace's
  ``migrate()`` can raise, so every row stored before a ``__version__`` bump must still
  come back, migrated. A row is rewritten the way an old release stored it: the fields
  added since are absent and ``__version__`` is the old one.
* Saving a loaded ``TimestampedMixin`` row checks its owner. The marketplace never sets
  ic-python-db's caller, so every save runs as "system" and the check never refuses.
"""

import pytest
from ic_python_db import Database

from marketplace_backend.api import extensions as ext_api
from marketplace_backend.api import verification as verification_api
from marketplace_backend.core import models

from .conftest import grant_license

# For each versioned entity: the fields later versions added, each with the version that
# added it and the value migrate() gives a row that lacks it.
ADDED_FIELDS = {
    models.ExtensionListingEntity: {"screenshots": (2, "")},
    models.DeveloperProfileEntity: {
        "suspended": (2, False),
        "suspended_reason": (2, ""),
    },
    models.MarketplaceConfigEntity: {
        "reviewers": (2, ""),
        "casals_frontend_canister_id": (3, ""),
        "admins": (4, ""),
        "purchases_enabled": (5, False),
    },
}

# A current row, with a value other than the default in each field a later version added
NEW_ENTITIES = {
    models.ExtensionListingEntity: lambda: models.ExtensionListingEntity(
        extension_id="demo", name="Demo", installs=0, likes=0, screenshots="a.png"
    ),
    models.DeveloperProfileEntity: lambda: models.DeveloperProfileEntity(
        principal="dev-1", display_name="Dev One", suspended=True, suspended_reason="spam"
    ),
    models.MarketplaceConfigEntity: lambda: models.MarketplaceConfigEntity(
        id="config",
        reviewers="r1",
        casals_frontend_canister_id="c1",
        admins="a1",
        purchases_enabled=True,
    ),
}

# The calls ic-python-db 0.16 names as raising MigrationError
LISTINGS = {
    "load_some": lambda cls: cls.load_some(1, 10),
    "page": lambda cls: cls.page(1, 10)[0],
    "instances": lambda cls: cls.instances(),
    "find": lambda cls: cls.find({}),
}

OLD_ROWS = [
    pytest.param(cls, version, id=f"{cls.__name__}-v{version}")
    for cls in ADDED_FIELDS
    for version in range(1, cls.__version__)
]


def _store_as_version(entity, version):
    """Rewrite ``entity``'s row as release ``version`` stored it, and forget the loaded copy."""
    db = Database.get_instance()
    row = db.load(entity._type, entity._id)
    for field, (added_in, _default) in ADDED_FIELDS[type(entity)].items():
        if added_in > version:
            del row[field]
    row["__version__"] = version
    db.save(entity._type, entity._id, row)
    db.clear_registry()


def test_every_older_version_is_covered():
    # A new migrate() step adds a version: it needs an entry in ADDED_FIELDS above
    assert {cls: cls.__version__ for cls in ADDED_FIELDS} == {
        models.ExtensionListingEntity: 2,
        models.DeveloperProfileEntity: 2,
        models.MarketplaceConfigEntity: 5,
    }


@pytest.mark.parametrize("listing", LISTINGS.values(), ids=LISTINGS.keys())
@pytest.mark.parametrize("entity_cls, version", OLD_ROWS)
def test_an_old_row_is_listed_migrated(entity_cls, version, listing):
    entity = NEW_ENTITIES[entity_cls]()
    current = {field: getattr(entity, field) for field in ADDED_FIELDS[entity_cls]}
    _store_as_version(entity, version)

    loaded = listing(entity_cls)

    assert [e._id for e in loaded] == [entity._id]
    for field, (added_in, default) in ADDED_FIELDS[entity_cls].items():
        # What the old release stored is kept; what it lacked gets its default
        expected = default if added_in > version else current[field]
        assert getattr(loaded[0], field) == expected, field


def test_a_later_message_edits_a_listing_another_principal_published(as_caller):
    # In a canister a later message loads the row from storage; 0.16 restores its owner
    # there and checks it on save
    grant_license("dev-1")
    assert ext_api.create_extension(
        developer="dev-1",
        extension_id="demo",
        name="Demo",
        description="d",
        version="1.0.0",
        price_e8s=0,
        icon="x",
        categories="tools",
        screenshots="",
        file_registry_canister_id="fr",
        file_registry_namespace="ext/demo/1.0.0",
        download_url="",
    )["success"]
    Database.get_instance().clear_registry()

    as_caller("reviewer-1", controller=True)
    reviewed = verification_api.set_verification_status(
        item_kind="ext", item_id="demo", status="verified", notes="ok"
    )

    assert reviewed["success"], reviewed
    Database.get_instance().clear_registry()
    assert models.ExtensionListingEntity["demo"].verification_status == "verified"
