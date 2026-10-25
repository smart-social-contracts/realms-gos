from ic_python_db import Entity, Integer, OneToOne, String, TimestampedMixin
from ic_python_logging import get_logger

logger = get_logger("entity.registry")


class Registry(Entity, TimestampedMixin):
    __alias__ = "name"
    name = String(min_length=2, max_length=256)
    description = String(max_length=256)
    principal_id = String(max_length=64)
