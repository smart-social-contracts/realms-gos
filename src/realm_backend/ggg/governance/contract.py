from ic_python_db import Integer, ManyToOne, OneToMany, String

from core.db import Json, Row


class Contract(Row):
    __alias__ = "id"
    __version__ = 2  # 2: split
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
    trades = OneToMany("Trade", "contract")
    ledger_entries = OneToMany("LedgerEntry", "contract")

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        obj.setdefault("split", [])  # a Json field a stored row lacks reads as the text of its default
        return obj
