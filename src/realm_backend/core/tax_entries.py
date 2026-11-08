"""A tax policy's typed entries (GGG's ``TaxEntry``) and how the previous release's tax documents become them.

The previous release kept a policy's taxes inside its ``document``, each with a ``base`` (who is taxed) and a
``method`` (how the amount is worked out). An entry says the same with GGG's fields: the ``subject`` follows the base,
``amount`` or ``rate`` follows the method, and every other setting (brackets, exemptions, due days) is the entry's
``facts``.
"""

# What a tax is assessed on: members, the owners of parcels, or what the taxpayer declares.
SUBJECTS = ("User", "Land", "Declaration")
SUBJECT_OF_BASE = {"flat": "User", "land_area": "Land", "declared": "Declaration"}
RETIRED = ("base", "method", "rate", "amount")


def entry_from_tax(tax):
    """The entry for one tax of an old document: ``{code, kind, subject, rate | amount, facts}``."""
    entry = {"code": tax.get("code", ""), "kind": "tax", "subject": SUBJECT_OF_BASE.get(tax.get("base"), "User")}
    method = tax.get("method", "fixed")
    if method == "fixed":
        entry["amount"] = int(tax.get("amount") or 0)
    elif method == "proportional" and tax.get("rate") is not None:
        entry["rate"] = tax["rate"]
    entry["facts"] = {k: v for k, v in tax.items() if k not in ("code", *RETIRED)}
    return entry


def split_document(document):
    """An old policy document as the pair (the document without its taxes, the taxes as entries)."""
    rest = {k: v for k, v in document.items() if k != "taxes"}
    return rest, [entry_from_tax(tax) for tax in document.get("taxes") or []]
