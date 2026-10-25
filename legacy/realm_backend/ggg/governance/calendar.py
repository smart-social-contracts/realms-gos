from ic_python_db import Entity, Integer, OneToOne, String, TimestampedMixin
from ic_python_logging import get_logger

logger = get_logger("entity.calendar")

# Default cycle durations in seconds
# Production defaults (can be overridden per-realm via manifest)
DEFAULTS = {
    "fiscal_period": 7_776_000,         # 90 days
    "voting_window": 604_800,           # 7 days
    "codex_release_cycle": 1_209_600,   # 14 days
    "benefit_cycle": 2_592_000,         # 30 days
    "service_payment_cycle": 2_592_000, # 30 days
    "license_review_cycle": 7_776_000,  # 90 days
}


class Calendar(Entity, TimestampedMixin):
    """
    Realm Calendar - defines all governance time cycles.

    The calendar is the master schedule of a realm. It defines the rhythms
    at which governance operations occur: fiscal periods, voting windows,
    benefit payments, service payments, license reviews, and codex releases.

    All durations are in seconds. Codexes and extensions reference these
    by their standard names:

        calendar = Realm.load("1").calendar
        calendar.fiscal_period        # e.g. 600 (10min demo) or 7776000 (90 days)
        calendar.benefit_cycle        # e.g. 300 (5min demo) or 2592000 (30 days)

    A demo realm might use short cycles for fast test runs:
        {"fiscal_period": 600, "benefit_cycle": 300, "voting_window": 120}

    Custom/extension-defined cycles go in custom_cycles as JSON:
        {"land_tax": 86400, "court_session": 604800}
    """
    __alias__ = "name"
    name = String(max_length=256)
    realm = OneToOne("Realm", "calendar")

    # Epoch - when this realm's calendar starts (Unix seconds, 0 = use realm creation time)
    epoch = Integer()

    # Core governance cycles (all in seconds)
    fiscal_period = Integer()          # Budget/accounting cycle
    voting_window = Integer()          # How long a proposal is open for voting
    codex_release_cycle = Integer()    # How often new codex versions are released
    benefit_cycle = Integer()          # Social benefit payment interval
    service_payment_cycle = Integer()  # External service payment interval
    license_review_cycle = Integer()   # License renewal review interval

    # Custom cycles as JSON for extensions
    # e.g., '{"land_tax": 86400, "court_session": 604800}'
    custom_cycles = String(max_length=2048)

    def current_period(self, cycle_name: str) -> int:
        """Which period number are we in for a given cycle?

        E.g., current_period("fiscal_period") -> 7 means we're in the 7th fiscal period.
        Returns 0 if the cycle duration is not set or is zero.
        """
        from _cdk import ic
        now = int(ic.time() / 1_000_000_000)
        epoch = self.epoch if self.epoch else 0
        duration = getattr(self, cycle_name, 0)
        if not duration:
            # Try custom_cycles
            import json
            try:
                custom = json.loads(self.custom_cycles or "{}")
                duration = custom.get(cycle_name, 0)
            except (json.JSONDecodeError, TypeError):
                duration = 0
        return (now - epoch) // duration if duration else 0

    def seconds_until_next(self, cycle_name: str) -> int:
        """Seconds remaining until the next cycle boundary.

        Useful for scheduling tasks aligned to cycle boundaries.
        """
        from _cdk import ic
        now = int(ic.time() / 1_000_000_000)
        epoch = self.epoch if self.epoch else 0
        duration = getattr(self, cycle_name, 0)
        if not duration:
            import json
            try:
                custom = json.loads(self.custom_cycles or "{}")
                duration = custom.get(cycle_name, 0)
            except (json.JSONDecodeError, TypeError):
                duration = 0
        if not duration:
            return 0
        elapsed = now - epoch
        return duration - (elapsed % duration)

    def get_cycle(self, cycle_name: str) -> int:
        """Get the duration (in seconds) of a named cycle.

        Checks built-in fields first, then custom_cycles JSON.
        Returns 0 if not found.
        """
        val = getattr(self, cycle_name, 0)
        if val:
            return val
        import json
        try:
            custom = json.loads(self.custom_cycles or "{}")
            return custom.get(cycle_name, 0)
        except (json.JSONDecodeError, TypeError):
            return 0
