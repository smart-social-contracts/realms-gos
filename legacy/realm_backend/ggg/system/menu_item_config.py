from ic_python_db import Entity, Integer, String, TimestampedMixin
from ic_python_logging import get_logger

logger = get_logger("entity.menu_item_config")


class MenuItemConfig(Entity, TimestampedMixin):
    """Custom extension placement in sidebar. Overrides manifest defaults.

    When present for an extension, the category_id and position here
    take priority over the extension's manifest values.
    """

    __alias__ = "extension_name"
    extension_name = String(max_length=256)
    category_id = String(max_length=64)
    position = Integer(default=0)

    def __repr__(self):
        return f"MenuItemConfig(extension={self.extension_name!r}, category={self.category_id!r})"
