from ic_python_db import Integer, ManyToOne, OneToMany, String

from core.db import Json, Row


class Contract(Row):
    __alias__ = "id"
    __version__ = 2  # 2: split, lot, framework
    id = String()
    title = String(default="")
    parties = Json([])  # ["Organization:buildco", "Department:infrastructure"]
    mandate = ManyToOne("Mandate", "contracts")
    kind = String(default="")  # what is contracted: works, supplies, services (the realm's words)
    procedure = String(default="")
    tender = String(default="")  # the tender it came from, which gave it its kind and procedure
    value = Integer(default=0)
    symbol = String(default="")
    terms_ref = String(default="")
    terms_hash = String(default="")
    status = String(default="draft")  # draft | active | fulfilled | terminated | disputed
    starts_at = Integer(default=0)
    ends_at = Integer(default=0)
    split = Json([])  # how value is spread over fiscal periods: [{"period": "FY2026", "amount": 30000}]; empty: not spread
    lot = String(default="")  # the lot of its tender it was awarded under, when the tender has lots
    # The framework agreement it is a call-off under, held by another body (ggg I-PRC-6): {"holder": {"External": id} |
    # {"Realm": the capital's principal}, "reference", "winners": [organization ids]}; None for a contract that is not one.
    framework = Json(None)
    trades = OneToMany("Trade", "contract")
    ledger_entries = OneToMany("LedgerEntry", "contract")

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        obj.setdefault("split", [])  # a Json field a stored row lacks reads as the text of its default
        obj.setdefault("framework", None)
        return obj
