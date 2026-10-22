from ic_python_db import Entity, Integer, ManyToOne, String, TimestampedMixin
from ic_python_logging import get_logger
from ..system.constants import STATUS_MAX_LENGTH

logger = get_logger("entity.quarter")


class QuarterStatus:
    SETUP = "setup"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    SPLITTING = "splitting"
    MERGING = "merging"


class Quarter(Entity, TimestampedMixin):
    __alias__ = "name"
    __version__ = 3

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        if from_version < 2:
            obj.setdefault("reported_codex_id", "")
            obj.setdefault("reported_codex_version", "")
            obj.setdefault("last_sync_ballot_id", "")
            obj.setdefault("last_sync_ballot_status", "")
        if from_version < 3:
            obj.setdefault("last_activity_at", 0)
        return obj

    name = String(min_length=2, max_length=256)
    canister_id = String(max_length=64)  # backend canister principal
    federation = ManyToOne("Realm", "quarter_ids")
    population = Integer(default=0)
    status = String(max_length=STATUS_MAX_LENGTH, default=QuarterStatus.SETUP)
    # Stable, human-friendly quarter number within the federation catalog.
    # Assigned monotonically at register_quarter time; the capital is index 0.
    # Lets users recover their home quarter by remembering a small integer
    # without any central per-user location index.
    index = Integer(default=0)
    # Gossip-reported codex + last sync ballot (issue #295). Filled by the
    # capital when merging a peer's ``get_quarter_directory`` ``self`` block.
    reported_codex_id = String(max_length=128, default="")
    reported_codex_version = String(max_length=64, default="")
    last_sync_ballot_id = String(max_length=64, default="")
    last_sync_ballot_status = String(max_length=32, default="")
    # The quarter's own core.activity time, from the same gossip ``self`` block.
    last_activity_at = Integer(default=0)
