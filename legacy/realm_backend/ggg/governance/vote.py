from ic_python_db import Entity, ManyToOne, String, TimestampedMixin
from ic_python_logging import get_logger

logger = get_logger("entity.vote")


class Vote(Entity, TimestampedMixin):
    __owner_field__ = "voter"  # realms#282 — SecureORM ownership stamp/protect
    """Individual vote entity for tracking votes on proposals"""

    proposal = ManyToOne("Proposal", "votes")
    voter = ManyToOne("User", "votes")
    vote_choice = String(max_length=16)  # 'yes', 'no', 'abstain'
    metadata = String(max_length=256)
