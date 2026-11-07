from ic_python_db import String

from core.db import Json, Row
from core.ratio import PERCENT


class AllocationRule(Row):
    __alias__ = "id"
    __version__ = 2  # 2: shares in hundred-millionths, not whole percents
    id = String()
    source_fund = String(default="ROOT")
    rules = Json({})  # {"WELFARE": 30_000_000, "ROADS": 20_000_000, "GENERAL": 50_000_000}
    status = String(default="draft")  # draft | proposed | adopted | superseded
    proposal = String(default="")

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        shares = obj.get("rules") or {}
        # Shares add up to at most 100 % (100 whole percents, 100 000 000 hundred-millionths): a total of 100 or less is
        # not converted yet.
        if sum(shares.values()) <= 100:
            obj["rules"] = {fund: share * PERCENT for fund, share in shares.items()}
        return obj
