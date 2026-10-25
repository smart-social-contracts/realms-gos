from ic_python_db import Entity, ManyToMany, String, TimestampedMixin
from ic_python_logging import get_logger

logger = get_logger("entity.extension")


class Extension(Entity, TimestampedMixin):
    """Represents an installed extension with access control relationships.

    Extension visibility is determined by the union of:
      - Direct user grants (user.extensions — unidirectional, issue #242;
        use ``self.reverse_count("users")`` for the grant count and
        core.membership.users_with_extension to list grantees)
      - Department membership (extension.departments → user.departments)
      - Profile-level baseline (extension.profiles)
    """

    __alias__ = "name"
    name = String(max_length=256)
    description = String(max_length=512)
    departments = ManyToMany(["Department"], "extensions")
    profiles = ManyToMany(["UserProfile"], "extensions")

    def __repr__(self):
        return f"Extension(name={self.name!r})"
