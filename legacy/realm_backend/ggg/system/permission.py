from ic_python_db import Entity, ManyToMany, String, TimestampedMixin
from ic_python_logging import get_logger

logger = get_logger("entity.permission")


class Permission(Entity, TimestampedMixin):
    """Fine-grained permission grant.

    Permissions supplement the coarse profile-level access (UserProfile.allowed_to).
    They can be assigned directly to Users or to UserProfiles. The access
    decorator checks both profile operations and per-user Permission entities.
    """
    __alias__ = "name"
    name = String(max_length=256)
    description = String(max_length=256)
    category = String(max_length=64)
    scope = String(max_length=256)
    users = ManyToMany(["User"], "permissions")
    profiles = ManyToMany(["UserProfile"], "permissions")
    departments = ManyToMany(["Department"], "permissions")
