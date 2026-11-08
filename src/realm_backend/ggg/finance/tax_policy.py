from ic_python_db import Integer, String

from core.db import Json, Row
from core.ratio import upgrade_tax_document


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
        obj["document"] = upgrade_tax_document(obj.get("document") or {})  # rate_bp shows a rate in basis points
        return obj
