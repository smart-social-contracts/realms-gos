from ic_python_db import Entity, String, TimestampedMixin
from ic_python_logging import get_logger

logger = get_logger("entity.guest_user")


class GuestUser(Entity, TimestampedMixin):
    """Lightweight presence of a user from another quarter.

    A GuestUser can transact and view content on this quarter but has
    no governance weight (no voting, no proposing).  Severed on
    secession of either the home or host quarter.
    """

    __alias__ = "principal"
    principal = String()  # Visitor's IC principal
    home_quarter = String(max_length=64)  # Canister ID of their home quarter
    permissions = String(max_length=256, default="view,transact")
