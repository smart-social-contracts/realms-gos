from core import chain
from core.db import DRAFTS, Conflict, Fail, Forbidden, each, find, first, latest, next_id, page_by, view, window
from ggg import Land, Member, Organization, TaxDeclaration, TaxPolicy, Token, User
from core.op import op
from core.ratio import PERCENT, RATE_MAX
from core.tax_entries import SUBJECTS
from api import codex
from api.proposals import realm_wide
from api.treasury import checked_total, currency, decimals_of, invoice_item, new_invoice, total

ENTRY_FIELDS = ("code", "kind", "subject", "rate", "amount", "facts")


def whole(value, where):
    if type(value) is not int or value < 0:
        raise Fail(f"{where} must be a whole number, not negative")
    return value


def rate(value, where):
    if type(value) is not int or not 0 <= value <= RATE_MAX:
        raise Fail(f"{where}: rate must be a whole number of hundred-millionths between 0 and {RATE_MAX}")
    return value


def refuse_rate_bp(rated, where):
    """A bracket still written in basis points: its rate would be missing, or read as a tiny one."""
    if "rate_bp" in rated:
        raise Fail(f"{where}: rate_bp is now rate, in hundred-millionths: 1 % is {PERCENT}")


def check_brackets(code, brackets):
    if not isinstance(brackets, list) or not brackets or brackets[-1].get("up_to") is not None:
        raise Fail(f"tax '{code}': progressive brackets must end open (up_to null)")
    bounds = [b["up_to"] for b in brackets[:-1]]
    if bounds != sorted(set(bounds)) or any(type(b) is not int or b <= 0 for b in bounds):
        raise Fail(f"tax '{code}': bracket bounds must be increasing positive integers")
    for b in brackets:
        refuse_rate_bp(b, code)
        rate(b.get("rate"), code)


def entry_of_draft(entry):
    """One entry of a draft as it is stored (the fields it states, ``facts`` always), refused if it is not a tax: it
    names a ``kind`` (the realm's word) and a ``subject``, and charges by at most one of ``amount``, ``rate`` or the
    ``brackets`` of its facts."""
    if not isinstance(entry, dict):
        raise Fail("a tax is a map with code, kind, subject, rate or amount, and facts")
    entry = {k: v for k, v in entry.items() if v is not None}
    code = entry.get("code", "")
    unknown = set(entry) - set(ENTRY_FIELDS)
    if unknown:
        raise Fail(f"tax '{code}': {', '.join(sorted(unknown))} is not a field of a tax; the settings go in facts")
    if not isinstance(entry.get("kind"), str) or not entry["kind"]:
        raise Fail(f"tax '{code}': kind is the realm's word for it, such as tax or fee")
    if entry.get("subject") not in SUBJECTS:
        raise Fail(f"tax '{code}': subject must be one of {', '.join(SUBJECTS)}")
    facts = entry.get("facts") or {}
    if not isinstance(facts, dict):
        raise Fail(f"tax '{code}': facts is a map")
    charged_by = [name for name, given in (("amount", "amount" in entry), ("rate", "rate" in entry),
                                           ("brackets", "brackets" in facts)) if given]
    if len(charged_by) > 1:
        raise Fail(f"tax '{code}' charges by {' and '.join(charged_by)}: give one")
    if "amount" in entry:
        whole(entry["amount"], f"tax '{code}': amount")
    if "rate" in entry:
        rate(entry["rate"], code)
    if "brackets" in facts:
        check_brackets(code, facts["brackets"])
    return {**entry, "facts": facts}


def draft_of(args):
    """The ``document`` and the typed ``taxes`` of a tax_policy_draft call, checked."""
    document, taxes = args["document"], args.get("taxes")
    if not isinstance(document, dict):
        raise Fail("a tax policy's document is a map")
    if taxes is None and "taxes" in document:
        raise Fail("the taxes are a list of their own, taxes, beside the document: each has a code, kind and subject")
    if not isinstance(taxes, list) or not taxes:
        raise Fail("a tax policy needs a list of taxes")
    entries, codes = [entry_of_draft(entry) for entry in taxes], set()
    for entry in entries:
        code = entry.get("code", "")
        if not code or code in codes:
            raise Fail(f"tax code '{code}' is missing or used twice")
        codes.add(code)
    return document, entries


def owed(entry, value, decimals):
    """The reference calculation: what a taxable ``value`` owes under one entry when no codex computes it, in the token's
    smallest unit, rounded to the cent as GGG rounds (integer; rates in hundred-millionths). The brackets of a
    progressive tax are added up exactly and the total is rounded once."""
    from realms.legislation.rates import times  # the layered codex library's one rounding rule, compiled on first use

    facts = entry["facts"]
    if value <= 0 or value < int(facts.get("exempt_below") or 0):
        return 0
    if "amount" in entry:
        return entry["amount"]
    if "rate" in entry:
        return times(value, entry["rate"], decimals)
    exact, lower = 0, 0  # in hundred-millionths of the smallest unit
    for b in facts.get("brackets") or []:
        upper = value if b.get("up_to") is None else min(value, b["up_to"])
        if upper > lower:
            exact += (upper - lower) * int(b["rate"])
        if b.get("up_to") is None or value <= b["up_to"]:
            break
        lower = b["up_to"]
    return times(exact, 1, decimals)


def adopted():
    p = first(TaxPolicy, "status", "adopted")
    if p is None:
        raise Conflict("no tax policy is adopted")
    return p


def enabled(entry):
    return entry["facts"].get("enabled") is not False


def entry_of(policy, code):
    entry = next((t for t in policy.taxes if t["code"] == code), None)
    if entry is None or not enabled(entry):
        raise Fail(f"the adopted tax policy has no tax '{code}'")
    return entry


def symbol_of(policy, accounting=None):
    """The token ``policy`` bills in: its own, or the accounting currency (``accounting``, when the caller has read it)."""
    return policy.document.get("symbol") or accounting or currency()


def key(declarant, code, period):
    return f"{declarant}|{code}|{period}"


def declared(declarant, period, code):
    return first(TaxDeclaration, "key", key(declarant, code, period)) is not None


def levy(symbol, entry, declarant, period, base, items):
    """Record that ``declarant`` owes ``items`` for ``period`` on ``base``, and bill them in token ``symbol``: an invoice
    for what they come to, none when it is nothing. Nothing is written if the items are not an amount of money."""
    did, amount = next_id(TaxDeclaration, "TD"), total(items)
    invoice = None
    if amount != 0:
        due = chain.now() + int(entry["facts"].get("due_days", 30)) * 86400
        invoice = new_invoice(declarant, items, symbol, due, f"TaxDeclaration:{did}")
    return TaxDeclaration(id=did, declarant=declarant, period=period, base=entry["code"],
                          key=key(declarant, entry["code"], period), amount=base,
                          status="assessed" if invoice else "settled", invoice=invoice.id if invoice else "")


def reference_items(entry, base, decimals):
    """The items of the entry's own charge on ``base``: one line, none when nothing is owed."""
    amount = owed(entry, base, decimals)
    return [{"concept": entry["code"], "amount": amount}] if amount > 0 else []


def subjects_of(entry, payer):
    """The subjects ``payer`` holds under ``entry``, as references: each of their active parcels, or themselves."""
    if entry["subject"] == "Land":
        return [f"Land:{p.id}" for p in each(Land, "owner", payer) if p.status == "active"]
    return [payer]


def assessment(entry, payer, base, symbol, decimals, accounting):
    """The items ``payer`` owes under ``entry``, billed in token ``symbol`` of ``decimals``: what the active codex
    computes for each subject they hold under it, or, when no codex answers, the charge of the entry's own rate, amount
    or brackets on ``base``. The amounts a chapter reads (a parcel's value) are in the ``accounting`` currency, so a codex
    does not compute a tax billed in another token, whatever its subject: that refuses the call."""
    answers = {}
    for subject in subjects_of(entry, payer):
        answers[subject] = yield from codex.compute("tax_assess", {"entry": entry, "subject": subject}, decimals=decimals)
        if answers[subject] is not None and symbol != accounting:
            raise Fail(f"tax '{entry['code']}' is billed in {symbol}, but a codex computes a tax in the accounting "
                       f"currency, {accounting}, only: the amounts it reads are in it")
    if all(answer is None for answer in answers.values()):
        return reference_items(entry, base, decimals)
    items = []
    for subject, answer in answers.items():
        if not isinstance(answer, list):  # a codex that computes some subjects computes all of them
            raise Fail(f"the codex answered {answer!r} for {entry['code']} on {subject}, not a list of invoice items")
        items += [invoice_item(item) for item in answer]
    return items


def payers(entry, start, count):
    """The taxpayers met in one id range: active, non-exempt members for a tax on users, owners of active parcels for
    one on land."""
    if entry["subject"] == "Land":
        return {p.owner for p in Land.load_some(start, count) if p.status == "active" and p.owner}
    exempt = set(entry["facts"].get("exempt_profiles") or [])
    return {f"User:{m.id}" for m in Member.load_some(start, count)
            if m.status == "active" and not (User[m.id] and exempt & {p.id for p in User[m.id].profiles})}


def taxable(entry, payer):
    """A taxpayer's taxable value: 1 for a tax on users, the area of all their active parcels × unit value for one on
    land."""
    if entry["subject"] != "Land":
        return 1
    return int(entry["facts"].get("unit_value", 1)) * sum(p.width * p.height for p in each(Land, "owner", payer)
                                                          if p.status == "active")


@op("tax.manage", governed=realm_wide, returns="policy", check=draft_of)
def tax_policy_draft(c, args):
    """Draft a tax policy from its typed ``taxes`` and a ``document`` (the policy's text and whatever else the realm
    records about it, such as the ``symbol`` of its currency), with optional ``description``. Each tax has a unique
    ``code``, a ``kind`` (the realm's word: tax, fee, ...), a ``subject`` (``User``: every active member;
    ``Land``: the owners of parcels; ``Declaration``: what the taxpayer declares) and what it charges when no codex
    computes it: an ``amount``, a ``rate`` (hundred-millionths: 1 % is 1000000), or progressive ``brackets`` in its
    ``facts`` (each ``up_to`` and ``rate``, the last with up_to null). Other ``facts`` it reads: ``unit_value`` (land),
    ``exempt_below``, ``exempt_profiles`` (users), ``due_days`` and ``enabled``. Returns ``policy``."""
    # The draft was checked by dispatch, before a governed call became a proposal: ``check=`` above.
    document, taxes = draft_of(args)
    p = TaxPolicy(id=next_id(TaxPolicy, "TAX-POLICY"), document=document, taxes=taxes,
                  description=args.get("description", ""))
    return {"policy": p.id}


@op("tax.manage", governed=realm_wide)
def tax_policy_propose(c, args):
    """Adopt the draft tax ``policy``, superseding the one in force."""
    p = find(TaxPolicy, args["policy"])
    if p.status != "draft":
        raise Conflict(f"{p.id} is {p.status}; only a draft can be adopted")
    for old in list(each(TaxPolicy, "status", "adopted")):
        old.status = "superseded"
    p.status, p.adopted_at, p.proposal = "adopted", chain.now(), c.proposal
    return {"policy": p.id, "status": p.status}


@op("tax.assess", returns="invoices")
def tax_assess(c, args):
    """Bill one batch of taxpayers for ``period`` (a label) under the adopted policy: tax ``tax`` only (not a
    self-declared one: those are billed by tax_declare), or by default every enabled tax that is not self-declared.
    The batch is the members (taxes on users) and parcels (taxes on land) with ids from ``from`` (default 1),
    ``count`` of each; call again with ``next`` until it is null. Those already billed for the period are skipped. A
    taxpayer's invoice has the items the active codex computes for each of their subjects under the tax, or, when
    it gives none, one item from the tax's own amount, rate or brackets. Returns ``invoices``, ``count`` (invoices
    issued), ``skipped`` and ``next``."""
    policy, period = adopted(), args["period"]
    if args.get("tax") and entry_of(policy, args["tax"])["subject"] == "Declaration":
        raise Fail(f"tax '{args['tax']}' is self-declared: tax_declare bills it")
    entries = [entry_of(policy, args["tax"])] if args.get("tax") else [
        t for t in policy.taxes if enabled(t) and t["subject"] != "Declaration"]
    start, count = window(args)
    accounting = currency()
    symbol = symbol_of(policy, accounting)
    find(Token, symbol)  # the invoices are in it
    decimals = decimals_of(symbol)
    invoices, skipped, due = [], 0, []
    # Everyone's items first: a codex answer that is not an invoice's items refuses the call before anyone is billed.
    for entry in entries:
        for declarant in sorted(payers(entry, start, count)):
            if declared(declarant, period, entry["code"]):
                skipped += 1
                continue
            base = taxable(entry, declarant)
            items = yield from assessment(entry, declarant, base, symbol, decimals, accounting)
            checked_total(items)
            due.append((entry, declarant, base, items))
    for entry, declarant, base, items in due:
        if declared(declarant, period, entry["code"]):  # a call that ran while the codex was asked billed them
            skipped += 1
            continue
        d = levy(symbol, entry, declarant, period, base, items)
        if d.invoice:
            invoices.append(d.invoice)
    last = max([(Land if t["subject"] == "Land" else Member).max_id() for t in entries], default=0)
    return {"policy": policy.id, "invoices": invoices, "count": len(invoices), "skipped": skipped,
            "next": start + count if start + count <= last else None}


def declarant_of(c, args):
    """Who declares: the caller, or the ``organization`` they administer."""
    if not args.get("organization"):
        return f"User:{c.user}"
    if c.user not in (find(Organization, args["organization"]).admins or []):
        raise Forbidden(f"you do not administer {args['organization']}")
    return f"Organization:{args['organization']}"


@op("tax.declare")
def tax_declare(c, args):
    """Declare ``amount`` for the self-declared tax ``base`` (its code) and ``period``, once, for yourself or an
    ``organization`` you administer, and get billed what it owes. Returns ``declaration``, ``invoice``, ``owed``.
    Example: ``{"organization": "clinic-b", "period": "2026-Q3", "base": "revenue", "amount": 182000}``."""
    policy, period, amount = adopted(), args["period"], int(args["amount"])
    entry = entry_of(policy, args["base"])
    if entry["subject"] != "Declaration":
        raise Fail(f"tax '{entry['code']}' is assessed by the realm, not declared")
    declarant = declarant_of(c, args)
    if amount < 0:
        raise Fail("a declared amount cannot be negative")
    if declared(declarant, period, entry["code"]):
        raise Conflict(f"{declarant} has already declared {entry['code']} for {period}")
    symbol = symbol_of(policy)
    items = reference_items(entry, amount, decimals_of(symbol))
    d = levy(symbol, entry, declarant, period, amount, items)
    return {"declaration": d.id, "invoice": d.invoice, "owed": total(items)}


@op("realm.data_view", read=True)
def tax_policy_view(c, args):
    """The adopted tax ``policy`` (null before the first) with the ``currency`` and ``decimals`` its amounts are in,
    up to ten newest ``drafts``, and one page of the ``declarations`` of the caller, or of an ``organization`` they
    administer (``from``, ``count``); ``next`` continues it. Members read the policy they are taxed under: everything
    else stays with the declarant."""
    declarant = declarant_of(c, args)
    current = first(TaxPolicy, "status", "adopted")
    symbol = symbol_of(current) if current else currency()
    drafts = [p for p in latest(TaxPolicy) if p.status == "draft"][:DRAFTS]
    rows, nxt = page_by(TaxDeclaration, "declarant", declarant, args)
    return {"policy": view(current, "id", "document", "taxes", "description", "adopted_at", "proposal")
            if current else None,
            "currency": symbol, "decimals": decimals_of(symbol),
            "drafts": [view(p, "id", "document", "taxes", "description") for p in drafts],
            "declarations": [view(d, "id", "period", "base", "amount", "status", "invoice") for d in rows],
            "next": nxt}
