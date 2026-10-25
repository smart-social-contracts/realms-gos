from ic_python_db import Entity, ManyToOne, String, TimestampedMixin
from ic_python_logging import get_logger

logger = get_logger("entity.service")


class Service(Entity, TimestampedMixin):
    __owner_field__ = "user"  # realms#282 — SecureORM ownership stamp/protect
    """Public service entity for citizen services"""

    __alias__ = "service_id"
    service_id = String(max_length=64)
    name = String(max_length=256)
    description = String(max_length=2048)
    provider = String(max_length=256)
    status = String(max_length=32)  # Active, Pending, Expired
    due_date = String(max_length=64)  # ISO format timestamp
    link = String(max_length=512)
    user = ManyToOne("User", "services")
    metadata = String(max_length=256)
