from ic_python_db import (
    Entity,
    Integer,
    ManyToMany,
    ManyToOne,
    OneToMany,
    OneToOne,
    String,
    TimestampedMixin,
)
from ic_python_logging import get_logger

logger = get_logger("entity.trade")


class Trade(Entity, TimestampedMixin):
    contract = ManyToOne("Contract", "trades")
    metadata = String(max_length=256)
    transfer_1 = OneToOne("Transfer", "trade")
    transfer_2 = OneToOne("Transfer", "trade")
