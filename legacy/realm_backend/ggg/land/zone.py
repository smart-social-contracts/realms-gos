from ic_python_db import Entity, ManyToOne, String, TimestampedMixin
from ic_python_logging import get_logger

logger = get_logger("entity.zone")


class Zone(Entity, TimestampedMixin):
    __owner_field__ = "user"  # realms#282 — SecureORM ownership stamp/protect
    """
    Geographic zone of influence, stored as an H3 cell index.

    Geometry (cell center, boundary, resolution) is computed in the browser by
    the consumer extensions (zone_selector, land_registry, public_dashboard,
    registry globe) using h3-js. The backend is intentionally geometry-free:
    it only enforces one territory zone per H3 cell via the ``h3_index`` alias
    and stores descriptive metadata (name, description, zone_type).

    v2: removed latitude/longitude/resolution; added zone_type.
    """

    __version__ = 2
    __alias__ = "h3_index"
    h3_index = String(max_length=32)  # H3 cell index (e.g., "861203a4fffffff")
    name = String(max_length=256)
    description = String(max_length=1024)
    zone_type = String(max_length=32, default="unassigned")
    metadata = String(max_length=2048, default="{}")

    # Relationships - a zone can belong to a user or land
    user = ManyToOne("User", "zones")
    land = ManyToOne("Land", "zones")

    @classmethod
    def migrate(cls, obj: dict, from_version: int, to_version: int) -> dict:
        """Drop legacy geometry fields; they are now computed on the frontend."""
        if from_version < 2 and to_version >= 2:
            obj.pop("latitude", None)
            obj.pop("longitude", None)
            obj.pop("resolution", None)
            obj.setdefault("zone_type", "unassigned")
        return obj

    def __repr__(self):
        return f"Zone(h3_index={self.h3_index!r}, name={self.name!r})"
