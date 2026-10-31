from ic_python_db import Entity, String, TimestampedMixin
from ic_python_logging import get_logger

logger = get_logger("entity.mandate")


class Mandate(Entity, TimestampedMixin):
    __alias__ = "name"
    name = String(max_length=256)
    metadata = String(max_length=256)
