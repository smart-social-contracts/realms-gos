from ic_python_db import Boolean, Entity, ManyToOne, String, TimestampedMixin
from ic_python_logging import get_logger

logger = get_logger("entity.menu_department_visibility")


class MenuDepartmentVisibility(Entity, TimestampedMixin):
    """Per-department extension visibility in sidebar.

    When visible=False, users in this department will not see the
    extension in their sidebar, regardless of other access rules.
    """

    __alias__ = "extension_name"
    extension_name = String(max_length=256)
    department = ManyToOne("Department", "menu_visibility_rules")
    visible = Boolean(default=True)

    def __repr__(self):
        return f"MenuDepartmentVisibility(extension={self.extension_name!r}, visible={self.visible})"
