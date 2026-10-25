from ic_python_db import Integer, ManyToOne, OneToMany, String

from db import Json, Row


class Contract(Row):
    __alias__ = "id"
    id = String()
    title = String(default="")
    parties = Json([])  # ["Organization:buildco", "Department:infrastructure"]
    mandate = ManyToOne("Mandate", "contracts")
    terms_ref = String(default="")
    terms_hash = String(default="")
    status = String(default="draft")  # draft | active | fulfilled | terminated | disputed
    starts_at = Integer(default=0)
    ends_at = Integer(default=0)
    trades = OneToMany("Trade", "contract")
    ledger_entries = OneToMany("LedgerEntry", "contract")
