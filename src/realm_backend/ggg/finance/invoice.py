from ic_python_db import Integer, OneToMany, String

from core.db import Json, Row


class Invoice(Row):
    __alias__ = "id"
    __version__ = 2  # 2: the amount is the sum of the items
    id = String()
    payer = String(indexed=True)  # "User:alice" or "Organization:clinic-b"
    amount = Integer(default=0)
    items = Json([])  # GGG's InvoiceItem: {concept, amount, quantity, unit, legislation}; at least one
    facts = Json({})  # what legislation reads about the invoice beyond its items
    symbol = String(default="")
    due_at = Integer(default=0)
    status = String(default="pending", indexed=True)  # pending | paid | overdue | cancelled
    reason = String(default="")
    paid_at = Integer(default=0)
    payment = Json({})
    transfers = OneToMany("Transfer", "invoice")
    ledger_entries = OneToMany("LedgerEntry", "invoice")

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        if not obj.get("items"):
            # One line for the whole amount, named as the ledger names the revenue of the invoice (treasury.receive).
            concept = (obj.get("reason") or "").partition(":")[0].lower() or "invoice"
            obj["items"] = [{"concept": concept, "amount": obj.get("amount", 0)}]
        obj.setdefault("facts", {})  # a Json field a stored row lacks would read as the text of its default
        return obj
