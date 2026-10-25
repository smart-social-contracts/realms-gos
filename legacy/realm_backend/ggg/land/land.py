from ic_python_db import Entity, Integer, ManyToOne, OneToMany, String, TimestampedMixin
from ic_python_logging import get_logger

logger = get_logger("entity.land")


class LandType:
    RESIDENTIAL = "residential"
    AGRICULTURAL = "agricultural"
    INDUSTRIAL = "industrial"
    COMMERCIAL = "commercial"
    UNASSIGNED = "unassigned"


class LandStatus:
    ACTIVE = "active"
    DISPUTED = "disputed"
    TRANSFERRED = "transferred"
    REVOKED = "revoked"


class Land(Entity, TimestampedMixin):
    __owner_field__ = "owner_user"  # realms#282 — SecureORM ownership stamp/protect
    __alias__ = "id"
    id = String()
    x_coordinate = Integer()
    y_coordinate = Integer()
    land_type = String(max_length=64, default=LandType.UNASSIGNED)
    owner_user = ManyToOne("User", "owned_lands")
    owner_organization = ManyToOne("Organization", "owned_lands")
    size_width = Integer(default=1)
    size_height = Integer(default=1)
    metadata = String(max_length=512, default="{}")
    zones = OneToMany("Zone", "land")
    
    # NFT integration fields (per realms#94)
    status = String(max_length=16, default=LandStatus.ACTIVE)
    registered_by = String(max_length=256, default="")  # Authority/notary who registered
    nft_token_id = String(max_length=64, default="")    # Link to LAND NFT token ID

    @staticmethod
    def land_registered_posthook(land: "Land") -> None:
        """Hook called after land registered. Override via Codex."""
        pass

    @staticmethod
    def land_transfer_posthook(land: "Land", from_owner: str, to_owner: str) -> None:
        """Hook called after land transfer. Override via Codex."""
        pass
