from core import chain
from core.db import Fail, each, find, next_id
from ggg import Land, Member, Organization, TaxDeclaration, TaxPolicy, User
from core.op import op
from api.proposals import realm_wide
from api.treasury import currency, new_invoice, replaying

BASES = ("flat", "land_area", "declared")
METHODS = ("fixed", "proportional", "progressive")
MAX_BP = 10_000


def rate(value, where):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_BP:
        raise Fail(f"{where}: rate_bp must be a whole number of basis points between 0 and {MAX_BP}")
    return value


def check(document):
    taxes, codes = document.get("taxes"), set()
    if not isinstance(taxes, list) or not taxes:
        raise Fail("a tax policy needs a list of taxes")
    for tax in taxes:
        code = tax.get("code", "")
        if not code or code in codes:
            raise Fail(f"tax code '{code}' is missing or used twice")
        codes.add(code)
        if tax.get("base", "flat") not in BASES or tax.get("method", "fixed") not in METHODS:
            raise Fail(f"tax '{code}': base must be one of {BASES}, method one of {METHODS}")
        if tax.get("method") == "proportional":
            rate(tax.get("rate_bp"), code)
        if tax.get("method") == "progressive":
            brackets = tax.get("brackets") or []
            if not brackets or brackets[-1].get("up_to") is not None:
                raise Fail(f"tax '{code}': progressive brackets must end open (up_to null)")
            bounds = [b["up_to"] for b in brackets[:-1]]
            if bounds != sorted(set(bounds)) or any(not isinstance(b, int) or b <= 0 for b in bounds):
                raise Fail(f"tax '{code}': bracket bounds must be increasing positive integers")
            for b in brackets:
                rate(b.get("rate_bp"), code)
    return document


def owed(tax, value):
    """The reference calculation: what a taxable ``value`` owes under one tax (integer, rates in bp)."""
    if value <= 0 or value < int(tax.get("exempt_below") or 0):
        return 0
    method = tax.get("method", "fixed")
    if method == "fixed":
        return int(tax.get("amount") or 0)
    if method == "proportional":
        return value * int(tax["rate_bp"]) // MAX_BP
    total, lower = 0, 0
    for b in tax["brackets"]:
        upper = value if b.get("up_to") is None else min(value, b["up_to"])
        if upper > lower:
            total += (upper - lower) * int(b["rate_bp"]) // MAX_BP
        if b.get("up_to") is None or value <= b["up_to"]:
            break
        lower = b["up_to"]
    return total


def adopted():
    p = next(each(TaxPolicy, "status", "adopted"), None)
    if p is None:
        raise Fail("no tax policy is adopted")
    return p


def tax_of(policy, code):
    tax = next((t for t in policy.document.get("taxes", []) if t.get("code") == code), None)
    if tax is None or tax.get("enabled") is False:
        raise Fail(f"the adopted tax policy has no tax '{code}'")
    return tax


def declared(declarant, period, code):
    return any(d.period == period and d.base == code for d in each(TaxDeclaration, "declarant", declarant))


def levy(policy, tax, declarant, period, value):
    """Record what ``declarant`` owes for ``period`` and bill it."""
    d = TaxDeclaration(id=next_id(TaxDeclaration, "TD"), declarant=declarant, period=period, base=tax["code"],
                       amount=value, status="assessed")
    amount = owed(tax, value)
    if amount > 0:
        due = chain.now() + int(tax.get("due_days", 30)) * 86400
        d.invoice = new_invoice(declarant, amount, policy.document.get("symbol") or currency(), due,
                                f"TaxDeclaration:{d.id}").id
    else:
        d.status = "settled"
    return d


def bases(tax):
    """Each taxpayer's taxable value: 1 per active member for flat taxes, area × unit value for land."""
    if tax.get("base", "flat") == "flat":
        exempt = set(tax.get("exempt_profiles") or [])
        out = {}
        for m in each(Member):
            user = User[m.id]
            if m.status == "active" and not (user and exempt & {p.id for p in user.profiles}):
                out[f"User:{m.id}"] = 1
        return out
    unit, out = int(tax.get("unit_value", 1)), {}
    for parcel in each(Land):
        if parcel.status == "active" and parcel.owner:
            out[parcel.owner] = out.get(parcel.owner, 0) + parcel.width * parcel.height * unit
    return out


@op("tax.manage", governed=realm_wide)
def tax_policy_draft(c, args):
    """Draft a tax policy from ``document`` (optional symbol, list of taxes) and optional ``description``. Each tax
    has a unique code, a base (flat, land_area or declared) and a method: fixed (amount), proportional (rate_bp,
    basis points) or progressive (brackets of up_to and rate_bp, the last with up_to null). Returns ``policy``."""
    p = TaxPolicy(id=next_id(TaxPolicy, "TAX-POLICY"), document=check(args["document"]),
                  description=args.get("description", ""))
    return {"policy": p.id}


@op("tax.manage", governed=realm_wide)
def tax_policy_propose(c, args):
    """Adopt the draft tax ``policy``, superseding the one in force."""
    p = find(TaxPolicy, args["policy"])
    if p.status != "draft":
        raise Fail(f"{p.id} is {p.status}; only a draft can be adopted")
    for old in list(each(TaxPolicy, "status", "adopted")):
        old.status = "superseded"
    p.status, p.adopted_at, p.proposal = "adopted", chain.now(), replaying(c, "tax_policy_propose")
    return {"policy": p.id, "status": p.status}


@op("tax.assess")
def tax_assess(c, args):
    """Bill every taxpayer for ``period`` (a label) under the adopted policy: tax ``tax`` only, or by default every
    enabled tax that is not self-declared. Those already billed are skipped. Returns ``invoices``, ``skipped``."""
    policy, period = adopted(), args["period"]
    taxes = [tax_of(policy, args["tax"])] if args.get("tax") else [
        t for t in policy.document["taxes"] if t.get("enabled", True) and t.get("base", "flat") != "declared"]
    invoices, skipped = [], 0
    for tax in taxes:
        for declarant, value in sorted(bases(tax).items()):
            if declared(declarant, period, tax["code"]):
                skipped += 1
                continue
            d = levy(policy, tax, declarant, period, value)
            if d.invoice:
                invoices.append(d.invoice)
    return {"policy": policy.id, "invoices": invoices, "count": len(invoices), "skipped": skipped}


@op("tax.declare", delegable=True)
def tax_declare(c, args):
    """Declare ``amount`` for the self-declared tax ``base`` (its code) and ``period``, once, for yourself or an
    ``organization`` you administer, and get billed what it owes. Returns ``declaration``, ``invoice``, ``owed``.
    Example: ``{"organization": "clinic-b", "period": "2026-Q3", "base": "revenue", "amount": 182000}``."""
    policy, period, amount = adopted(), args["period"], int(args["amount"])
    tax = tax_of(policy, args["base"])
    if tax.get("base") != "declared":
        raise Fail(f"tax '{tax['code']}' is assessed by the realm, not declared")
    declarant = f"User:{c.user}"
    if args.get("organization"):
        if c.user not in (find(Organization, args["organization"]).admins or []):
            raise Fail(f"you do not administer {args['organization']}")
        declarant = f"Organization:{args['organization']}"
    if amount < 0 or declared(declarant, period, tax["code"]):
        raise Fail(f"{declarant} has already declared {tax['code']} for {period}" if amount >= 0
                   else "a declared amount cannot be negative")
    d = levy(policy, tax, declarant, period, amount)
    return {"declaration": d.id, "invoice": d.invoice, "owed": owed(tax, amount)}
