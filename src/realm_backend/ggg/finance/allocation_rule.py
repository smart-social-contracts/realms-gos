from ic_python_db import String

from core.db import Json, Row
from core.ratio import shares_from_percents


class AllocationRule(Row):
    __alias__ = "id"
    __version__ = 2  # 2: a list of {fund, share} in hundred-millionths, not a map of fund to whole percents
    id = String()
    source_fund = String(default="ROOT")
    rules = Json([])  # [{"fund": "WELFARE", "share": 30_000_000}, {"fund": "ROADS", "share": 20_000_000}]
    status = String(default="draft")  # draft | proposed | adopted | superseded
    proposal = String(default="")

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        if isinstance(obj.get("rules"), dict):  # the old map; the list is the current form
            obj["rules"] = shares_from_percents(obj["rules"])
        return obj
