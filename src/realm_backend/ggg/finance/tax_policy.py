from ic_python_db import Integer, String

from core.db import Json, Row
from core.ratio import upgrade_tax_document
from core.tax_entries import split_document


class TaxPolicy(Row):
    __alias__ = "id"
    # 2: rates in hundred-millionths under the name rate, not basis points under rate_bp
    # 3: the taxes are typed entries in ``taxes``, no longer a list in ``document``
    __version__ = 3
    id = String()
    status = String(default="draft", indexed=True)  # draft | proposed | adopted | superseded
    document = Json({})  # the policy's text and what the realm records about it; its entries are in ``taxes``
    taxes = Json([])  # GGG's TaxEntry: {code, kind, subject, rate | amount, facts}
    description = String(default="")
    proposal = String(default="")
    adopted_at = Integer(default=0)

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        document = upgrade_tax_document(obj.get("document") or {})  # rate_bp shows a rate in basis points
        if "taxes" in document:  # still the previous release's list
            obj["document"], obj["taxes"] = split_document(document)
        obj.setdefault("taxes", [])  # a Json field a stored row lacks would read as the text of its default
        return obj
