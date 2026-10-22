"""``land.*`` verbs — the invariants that generic writes could not express.

``land_registry`` was gated only by ``entry_access`` in its own manifest, so
the restraint shipped inside the thing it restrained. These tests cover the
two consequences of moving it host-side: the operation check now holds
regardless of what the manifest claims, and the registry's rules (one parcel
per cell, one owner, residential-means-member, mint-once) are enforced where
extension code cannot skip them.
"""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src" / "realm_backend"))
sys.modules.setdefault("_cdk", MagicMock())

from core import extension_bridge as eb  # noqa: E402
from core import land_bridge as lb  # noqa: E402


class Row:
    def __init__(self, store, **fields):
        self._id = len(store) + 1
        self.id = str(self._id)
        self.x_coordinate = None
        self.y_coordinate = None
        self.land_type = "unassigned"
        self.status = "pending"
        self.size_width = 1
        self.size_height = 1
        self.metadata = "{}"
        self.registered_by = ""
        self.nft_token_id = None
        self.owner_user = None
        self.owner_organization = None
        self.zones = []
        self.__dict__.update(fields)
        store.append(self)


@pytest.fixture
def realm(monkeypatch):
    lands, zones, users, orgs = [], {}, {}, {}

    class Land:
        RESIDENTIAL = "residential"

        def __new__(cls, **fields):
            return Row(lands, **fields)

        @staticmethod
        def max_id():
            return len(lands)

        @staticmethod
        def load_some(from_id=1, count=10):
            return [land for land in lands
                    if from_id <= land._id < from_id + count]

        @staticmethod
        def load(key):
            return next((land for land in lands if land.id == str(key)), None)

        def __class_getitem__(cls, key):
            return Land.load(key)

    class Zone:
        def __new__(cls, **fields):
            zone = types.SimpleNamespace(**fields)
            zones[fields["h3_index"]] = zone
            land = fields.get("land")
            if land is not None:
                land.zones.append(zone)
            return zone

        @staticmethod
        def instances():
            return list(zones.values())

        def __class_getitem__(cls, key):
            return zones.get(key)

    class User:
        @staticmethod
        def load(key):
            return users.get(key)

    class Organization:
        @staticmethod
        def load(key):
            return orgs.get(key)

    ggg = types.ModuleType("ggg")
    ggg.Land = Land
    ggg.Zone = Zone
    ggg.User = User
    ggg.Organization = Organization
    ggg.LandType = types.SimpleNamespace(
        UNASSIGNED="unassigned", RESIDENTIAL="residential",
        COMMERCIAL="commercial",
    )
    ggg.LandStatus = types.SimpleNamespace(ACTIVE="active")
    monkeypatch.setitem(sys.modules, "ggg", ggg)

    users["alice"] = types.SimpleNamespace(id="alice", nickname="Alice")
    # Organization is keyed by its name; it has no ``id`` field.
    orgs["Acme"] = types.SimpleNamespace(name="Acme")

    granted = set()
    monkeypatch.setattr(eb, "caller_has_operation", lambda c, op: op in granted)
    return types.SimpleNamespace(lands=lands, zones=zones, granted=granted)


CAPS = sorted(lb.VERBS)


def call(caller="admin"):
    return eb.make_rpc_handler("land_registry", CAPS, caller)


def make_land(realm, **fields):
    realm.granted.add("realm.admin")
    return call()("land_registry", "land.create", fields)


# ---------------------------------------------------------------------------
# The manifest no longer decides
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verb", sorted(lb.WRITES))
def test_every_write_requires_realm_admin(realm, verb):
    """Declared capabilities do not substitute for the caller's operation."""
    with pytest.raises(PermissionError, match="realm.admin"):
        call("mallory")("land_registry", verb, {"land_id": "1"})


def test_reads_do_not_require_admin(realm):
    make_land(realm, x_coordinate=1, y_coordinate=1)
    realm.granted.clear()
    assert call("member")("land_registry", "land.list", {})["count"] == 1


# ---------------------------------------------------------------------------
# Registry invariants
# ---------------------------------------------------------------------------


def test_one_parcel_per_h3_cell(realm):
    make_land(realm, h3_indexes=["8a1f"], name="First")
    with pytest.raises(ValueError, match="already exists at H3 cell 8a1f"):
        make_land(realm, h3_indexes=["8a1f"], name="Second")


def test_no_two_parcels_on_the_same_coordinates(realm):
    make_land(realm, x_coordinate=5, y_coordinate=7)
    with pytest.raises(ValueError, match="already exists at these coordinates"):
        make_land(realm, x_coordinate=5, y_coordinate=7)


def test_coordinates_are_required_without_cells(realm):
    with pytest.raises(ValueError, match="x_coordinate and y_coordinate"):
        make_land(realm, land_type="commercial")


def test_registered_by_is_the_caller_not_an_argument(realm):
    realm.granted.add("realm.admin")
    call("admin")("land_registry", "land.create", {
        "x_coordinate": 1, "y_coordinate": 1, "registered_by": "someone-else",
    })
    assert realm.lands[-1].registered_by == "admin"


def test_multi_cell_parcel_labels_each_zone(realm):
    land = make_land(realm, h3_indexes=["a", "b", "c"], name="Big")
    assert land["h3_indexes"] == ["a", "b", "c"]
    assert len(realm.zones) == 0


def test_territory_zone_does_not_block_parcel_create(realm):
    realm.zones["8a1f"] = types.SimpleNamespace(
        h3_index="8a1f", land=None, zone_type="residential", name="Territory",
    )
    land = make_land(realm, h3_indexes=["8a1f"], name="Parcel")
    assert land["h3_index"] == "8a1f"
    assert len(realm.zones) == 1


def test_parcel_inherits_territory_land_type(realm):
    realm.zones["8a1f"] = types.SimpleNamespace(
        h3_index="8a1f", land=None, zone_type="residential", name="Territory",
    )
    land = make_land(
        realm, h3_indexes=["8a1f"], name="Parcel", land_type="commercial",
    )
    assert land["land_type"] == "residential"


def test_parcel_follows_territory_zone_type_change(realm):
    realm.zones["8a1f"] = types.SimpleNamespace(
        h3_index="8a1f", land=None, zone_type="residential", name="Territory",
    )
    make_land(realm, h3_indexes=["8a1f"], name="Parcel")
    assert realm.lands[0].land_type == "residential"

    realm.zones["8a1f"].zone_type = "commercial"
    lb.sync_parcels_covering(["8a1f"])
    assert realm.lands[0].land_type == "commercial"


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


def test_ownership_cannot_be_set_through_land_update(realm):
    """The reason writes are typed: ownership must not be a field."""
    make_land(realm, x_coordinate=1, y_coordinate=1)
    with pytest.raises(ValueError, match="ownership changes go through"):
        call()("land_registry", "land.update", {
            "land_id": "1", "owner_user": "alice",
        })


def test_a_parcel_has_at_most_one_owner(realm):
    make_land(realm, x_coordinate=1, y_coordinate=1,
              land_type="residential")
    with pytest.raises(ValueError, match="both user and organization"):
        call()("land_registry", "land.set_owner", {
            "land_id": "1", "owner_user_id": "alice",
            "owner_organization_id": "Acme",
        })


def test_members_may_own_only_residential_land(realm):
    make_land(realm, x_coordinate=1, y_coordinate=1, land_type="commercial")
    with pytest.raises(ValueError, match="only own residential land"):
        call()("land_registry", "land.set_owner", {
            "land_id": "1", "owner_user_id": "alice",
        })


def test_organizations_may_not_own_residential_land(realm):
    make_land(realm, x_coordinate=1, y_coordinate=1, land_type="residential")
    with pytest.raises(ValueError, match="cannot own residential land"):
        call()("land_registry", "land.set_owner", {
            "land_id": "1", "owner_organization_id": "Acme",
        })


def test_transfer_clears_the_previous_owner(realm):
    make_land(realm, x_coordinate=1, y_coordinate=1, land_type="residential")
    result = call()("land_registry", "land.set_owner", {
        "land_id": "1", "owner_user_id": "alice",
    })
    assert result["owner_user_id"] == "alice"

    cleared = call()("land_registry", "land.set_owner", {"land_id": "1"})
    assert cleared["owner_user_id"] is None
    assert cleared["owner_organization_id"] is None


def test_parcels_owned_by_an_organization_are_listed(realm):
    make_land(realm, x_coordinate=1, y_coordinate=1, land_type="commercial")
    call()("land_registry", "land.set_owner", {
        "land_id": "1", "owner_organization_id": "Acme",
    })
    land = call()("land_registry", "land.list", {})["lands"][0]
    assert land["owner_organization_id"] == "Acme"
    assert land["owner_organization_name"] == "Acme"


def test_unknown_owner_is_rejected(realm):
    make_land(realm, x_coordinate=1, y_coordinate=1, land_type="residential")
    with pytest.raises(ValueError, match="User not found"):
        call()("land_registry", "land.set_owner", {
            "land_id": "1", "owner_user_id": "ghost",
        })


# ---------------------------------------------------------------------------
# NFT bookkeeping
# ---------------------------------------------------------------------------


def test_nft_is_minted_once(realm):
    make_land(realm, x_coordinate=1, y_coordinate=1)
    call()("land_registry", "land.set_nft_token", {
        "land_id": "1", "nft_token_id": "tok-1",
    })
    with pytest.raises(ValueError, match="refusing to overwrite"):
        call()("land_registry", "land.set_nft_token", {
            "land_id": "1", "nft_token_id": "tok-2",
        })


def test_resetting_the_same_token_is_idempotent(realm):
    make_land(realm, x_coordinate=1, y_coordinate=1)
    for _ in range(2):
        result = call()("land_registry", "land.set_nft_token", {
            "land_id": "1", "nft_token_id": "tok-1",
        })
    assert result["nft_token_id"] == "tok-1"


def test_prepare_refuses_an_already_minted_parcel(realm):
    make_land(realm, x_coordinate=1, y_coordinate=1)
    call()("land_registry", "land.set_nft_token", {
        "land_id": "1", "nft_token_id": "tok-1",
    })
    with pytest.raises(ValueError, match="already has NFT minted"):
        call()("land_registry", "land.prepare_nft", {
            "land_id": "1", "owner_principal": "alice",
        })


def test_prepare_activates_and_attributes_to_the_caller(realm):
    make_land(realm, x_coordinate=1, y_coordinate=1)
    result = call("admin")("land_registry", "land.prepare_nft", {
        "land_id": "1", "owner_principal": "alice",
    })
    assert result["requires_mint"] is True
    assert realm.lands[0].status == "active"
    assert realm.lands[0].registered_by == "admin"


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def test_missing_parcel_is_an_error(realm):
    realm.granted.add("realm.admin")
    with pytest.raises(ValueError, match="Land not found"):
        call()("land_registry", "land.get", {"land_id": "nope"})


def test_page_size_is_capped_host_side(realm):
    """A sandboxed extension cannot ask for the whole table at once."""
    for i in range(5):
        make_land(realm, x_coordinate=i, y_coordinate=0)
    result = call()("land_registry", "land.list",
                    {"page_size": 10_000_000})
    assert result["count"] == 5
    assert result["has_more"] is False


def test_map_filters_to_the_requested_window(realm):
    make_land(realm, x_coordinate=5, y_coordinate=5)
    make_land(realm, x_coordinate=50, y_coordinate=50)
    result = call()("land_registry", "land.map",
                    {"min_x": 0, "max_x": 10, "min_y": 0, "max_y": 10})
    assert list(result["lands"]) == ["5,5"]
