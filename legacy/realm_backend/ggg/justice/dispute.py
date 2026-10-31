from ic_python_db import Entity, ManyToOne, String, TimestampedMixin
from ic_python_logging import get_logger

from ..system.constants import STATUS_MAX_LENGTH

logger = get_logger("entity.dispute")


class Dispute(Entity, TimestampedMixin):
    __alias__ = "dispute_id"
    dispute_id = String(max_length=64)
    requester = ManyToOne("User", "disputes_requested")
    defendant = ManyToOne("User", "disputes_defendant")
    case_title = String(max_length=256)
    description = String(max_length=2048)
    status = String(max_length=STATUS_MAX_LENGTH)
    verdict = String(max_length=1024)
    actions_taken = String(max_length=2048)  # JSON array as string
    metadata = String(max_length=256)
