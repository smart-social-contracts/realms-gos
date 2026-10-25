from ic_python_db import Entity, Integer, String, TimestampedMixin
from ic_python_logging import get_logger

logger = get_logger("entity.menu_category_config")


class MenuCategoryConfig(Entity, TimestampedMixin):
    """Custom sidebar category ordering. Overrides default hardcoded order.

    When no records exist, the system uses DEFAULT_CATEGORY_ORDER.
    Admin-created records take priority over defaults.
    """

    __alias__ = "category_id"
    category_id = String(max_length=64)
    label = String(max_length=128)
    position = Integer(default=0)

    def __repr__(self):
        return f"MenuCategoryConfig(category_id={self.category_id!r}, position={self.position})"
