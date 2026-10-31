from ic_python_db import Entity, Float, OneToMany, OneToOne, String, TimestampedMixin
from ic_python_logging import get_logger

logger = get_logger("entity.human")


class Human(Entity, TimestampedMixin):
    __owner_field__ = "user"  # realms#282 — SecureORM ownership stamp/protect
    __alias__ = "name"
    name = String(max_length=256)
    date_of_birth = String(max_length=256)
    latitude = Float()
    longitude = Float()
    h3_index = String(max_length=20)
    user = OneToOne("User", "human")
    identities = OneToMany("Identity", "human")
