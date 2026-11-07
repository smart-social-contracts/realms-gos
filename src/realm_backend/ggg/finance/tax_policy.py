from ic_python_db import Integer, String

from core.db import Json, Row
from core.ratio import BASIS_POINT


class TaxPolicy(Row):
    __alias__ = "id"
    __version__ = 2  # 2: rates in hundred-millionths under the name rate, not basis points under rate_bp
    id = String()
    status = String(default="draft", indexed=True)  # draft | proposed | adopted | superseded
    document = Json({})
    description = String(default="")
    proposal = String(default="")
    adopted_at = Integer(default=0)

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        # rate_bp, the old key, shows a rate still in basis points.
        for tax in (obj.get("document") or {}).get("taxes", []):
            for rated in [tax, *tax.get("brackets", [])]:
                if "rate_bp" in rated:
                    rated["rate"] = rated.pop("rate_bp") * BASIS_POINT
        return obj
