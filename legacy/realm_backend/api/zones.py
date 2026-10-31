"""Zone API for map display.

The backend stores only H3 cell indices. All geometry (center, boundary,
resolution) is computed by the browser using h3-js. See issue #254.
"""

from typing import Any, Dict

from ggg import Zone
from ic_python_logging import get_logger

logger = get_logger("api.zones")


def get_zone_aggregation(resolution: int = 6) -> Dict[str, Any]:
    """Return territory zones for the registry map / zone consumers.

    Returns a list of zones with only the fields the frontend needs to render
    geometry from h3-js. Territory zones are those not linked to a Land parcel
    (``zone.land is None``); parcel zones are owned by the Land Registry.

    Args:
        resolution: kept for API compatibility only; no longer used.

    Returns:
        Dictionary with zone data:
        - zones: list of {h3_index, name, zone_type, user_count}
        - total_users: total users with zones
        - resolution: passed through
    """
    try:
        zones = []
        unique_users = set()

        for zone in Zone.instances():
            # Territory zones only; land-linked zones are parcel geometry.
            try:
                if zone.land is not None:
                    continue
            except Exception:
                pass

            h3_index = zone.h3_index
            if not h3_index:
                continue

            zones.append({
                "h3_index": h3_index,
                "name": zone.name or "Zone",
                "zone_type": getattr(zone, "zone_type", None) or "unassigned",
                "user_count": 1,
            })

            user = getattr(zone, "user", None)
            if user:
                unique_users.add(user.id)

        return {
            "success": True,
            "zones": zones,
            "total_users": len(unique_users),
            "resolution": resolution,
        }

    except Exception as e:
        logger.error(f"Error getting zones: {e}")
        return {
            "success": False,
            "error": str(e),
            "zones": [],
            "total_users": 0,
            "resolution": resolution,
        }
