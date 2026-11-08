import hashlib
import json

from core import candid
from core import chain
from core.db import DRAFTS, Conflict, Fail, Forbidden, each, find, first, latest, next_id, page, page_by, view, window
from ggg import (AllocationRule, Budget, Contract, Department, FinancialReport, FiscalPeriod, Fund, Invoice,
                 LedgerEntry, Loan, NFTToken, Organization, PaymentAccount, Realm, TaxDeclaration, Token,
                 Transfer, Treasury)
from ggg.finance.budget import KINDS
from core.op import op
from core.ratio import ONE, PERCENT
from api.proposals import realm_wide, root

CONFIG = ("epoch", "anchor_month", "source_fund", "auto_allocate")
REGISTRY = 100  # tokens, and NFT collections, a realm can register: treasury_view lists them all
REFRESH = 20  # invoices one invoice_refresh checks, one ledger call each
MONEY_MAX = 2**53 - 1  # GGG's Money
FEE = "ledger_fee"  # the category of the ledger's fee on a payment: a cost of paying, not a budgeted one
IN_FORCE = ("adopted", "extended")  # the statuses of a budget line that caps spending
ITEM_FIELDS = ("concept", "amount", "quantity", "unit", "legislation")
PERIOD_UNITS = ("day", "working_day", "month", "year")


# --- ledger calls ---------------------------------------------------------------------------------

def blob(data):
    return 'blob "' + "".join(f"\\{b:02x}" for b in data) + '"'


def account(owner, sub=None):
    return f'record {{ owner = principal "{owner}"; subaccount = {"opt " + blob(sub) if sub else "null"} }}'


def text(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def subaccount(invoice_id):
    return invoice_id.encode().ljust(32, b"\0")


def nat(reply):
    return candid.number(reply)


def balance_of(token, sub):
    reply = yield from chain.call(token.ledger, "icrc1_balance_of", f"({account(chain.self_id(), sub)})")
    return nat(reply)


def is_block_index(value):
    # bool is an int in Python, but ``true`` is not a block index
    return type(value) is int and value >= 0


def outcome(reply):
    """How a ledger answered a payout: ``(status, ledger_tx)``. The ledger speaks the standard ICRC-1 result.
    ``completed``: ``Ok`` with the block index. ``failed``: ``Err``; only then did the ledger certainly not move the
    tokens. Anything else is ``unconfirmed``: the tokens may have left, so the books must not be reversed until
    someone checks the ledger."""
    try:
        (answer,) = candid.Reader(reply).args()
    except Exception:  # a hostile reply can also overflow or recurse; whatever goes wrong, the answer is unclear
        return "unconfirmed", None
    if not isinstance(answer, dict):
        return "unconfirmed", None
    # An unnamed decoder prints a field's hash for its name, so look fields up by hash.
    fields = {candid.idl_hash(k) if isinstance(k, str) else k: v for k, v in answer.items()}
    ok = fields.get(candid.idl_hash("Ok"))
    if is_block_index(ok):
        return "completed", str(ok)
    if candid.idl_hash("Err") in fields:
        return "failed", None
    return "unconfirmed", None


# --- books ----------------------------------------------------------------------------------------

def currency():
    realm = Realm["1"]
    if realm and realm.accounting_currency:
        return realm.accounting_currency
    oldest = Token.load_some(1, 1)
    return oldest[0].id if oldest else ""


def decimals_of(symbol):
    token = Token[symbol] if symbol else None
    return token.decimals if token else 0


def owner_of(fund):
    return fund.department or root()


def period_at(t):
    """The fiscal period covering ``t``, if any. Periods open in order, so their ids follow time."""
    lo, hi, found = 1, FiscalPeriod.max_id(), None
    while lo <= hi:
        mid = (lo + hi) // 2
        p = FiscalPeriod.load_some(mid, 1)[0]
        if p.starts_at <= t:
            found, lo = p, mid + 1
        else:
            hi = mid - 1
    return found if found and t < found.ends_at else None


def period():
    """The fiscal period covering now; refuses if it is closed (I-FIN-5)."""
    p = period_at(chain.now())
    if p is not None and p.status != "open":
        raise Conflict(f"fiscal period {p.id} is {p.status}")
    return p


def slot(fund, p, category):
    return f"{fund.id}:{p.id}:{category}"


def tally(p, e):
    """Add ``e`` to its period's running statements and chain it into the period's source hash."""
    s = p.statements
    key = f"{e.kind}:{e.category}:{e.symbol}"
    a = s["accounts"].get(key, {"debit": 0, "credit": 0})
    s["accounts"][key] = {"debit": a["debit"] + e.debit, "credit": a["credit"] + e.credit}
    if e.kind == "asset" and e.fund is not None:
        k = f"{e.fund.id}:{e.symbol}"
        s["fund_balances"][k] = s["fund_balances"].get(k, 0) + e.debit - e.credit
    s["debit"], s["credit"] = s["debit"] + e.debit, s["credit"] + e.credit
    p.statements = s
    line = json.dumps([e.id, e.transaction, e.debit, e.credit, e.symbol])
    p.source_hash = hashlib.sha256((p.source_hash + line).encode()).hexdigest()


def caps_spending(b):
    """A line caps spending when it is in force and plans an expense: a revenue line caps nothing."""
    return b.status in IN_FORCE and b.kind == "expense"


def has_adopted_budget(fund, p):
    """The fund has an adopted line in the period, of either kind: its budget is adopted."""
    return any(b.status == "adopted" and b.fund.id == fund.id for b in p.budgets)


def refuse_overspend(p, lines):
    """A budget line's planned amount is a ceiling: refuse lines that would take its actual past it (members amend the
    budget to spend more). An adopted budget is exhaustive: once a fund has one for the period, spending in a category it
    has no line for is refused too (not the ledger's fee). Only spending counts: a reversal frees budget and is never
    refused."""
    spent = {}
    for kind, category, debit, credit, fund in lines:
        if kind == "expense" and fund is not None and p is not None:
            key = slot(fund, p, category)
            spent[key] = (fund, category, spent.get(key, (None, None, 0))[2] + debit - credit)
    for key, (fund, category, asked) in spent.items():
        if asked <= 0:
            continue
        in_force = [b for b in each(Budget, "slot", key) if caps_spending(b)]
        for b in in_force:
            if b.actual + asked > b.planned:
                raise Conflict(f"budget {b.id} plans {b.planned} for {b.category} of fund {b.fund.id}, "
                               f"{b.actual} is spent and {asked} was asked: amend the budget to spend more")
        if not in_force and category != FEE and has_adopted_budget(fund, p):
            raise Conflict(f"the adopted budget of fund {fund.id} for {p.id} has no expense line for {category}: amend "
                           f"the budget to spend on it")


def post(p, lines, symbol, description="", **refs):
    """One balanced transaction: lines are (kind, category, debit, credit, fund). The only place entries are
    booked, so it keeps fund balances, adopted budgets' actuals and the period's statements, and it refuses
    what would overspend an adopted budget."""
    refuse_overspend(p, lines)
    tx = f"T-{LedgerEntry.max_id() + 1}"
    for kind, category, debit, credit, fund in lines:
        e = LedgerEntry(id=f"LE-{LedgerEntry.max_id() + 1}", transaction=tx, kind=kind, category=category,
                        debit=debit, credit=credit, symbol=symbol, fund=fund, period=p, at=chain.now(),
                        description=description, **refs)
        if kind == "asset" and fund is not None:
            held = fund.balances
            fund.balances = {**held, symbol: held.get(symbol, 0) + debit - credit}
        if p is None:
            continue
        tally(p, e)
        if kind == "expense" and fund is not None:
            for b in each(Budget, "slot", slot(fund, p, category)):
                if caps_spending(b):
                    b.actual += debit - credit
    return tx


def balance(fund, symbol):
    return fund.balances.get(symbol, 0)


def adopted_rule(fund):
    return AllocationRule[fund.allocation_rule] if fund.allocation_rule else None


# --- transfers ------------------------------------------------------------------------------------

def address(to, symbol):
    for owner in (to, f"User:{to}", f"Organization:{to}"):
        for a in each(PaymentAccount, "owner", owner):
            if a.symbol == symbol:
                return a.address
    return to.removeprefix("User:")  # a user without an account is paid at their principal


def spending(fund, category, amount, fee):
    return [("expense", category, amount, 0, fund), ("expense", FEE, fee, 0, fund)]


def reserve(p, fund, to, symbol, amount, category, description="", payroll=None, **refs):
    fee = find(Token, symbol).fee
    lines = spending(fund, category, amount, fee) + [("asset", "cash", 0, amount + fee, fund)]
    refuse_overspend(p, lines)  # post refuses too, but only after the transfer row below is written
    t = Transfer(id=next_id(Transfer, "TR"), sender=f"Fund:{fund.id}", to=to, symbol=symbol, amount=amount,
                 status="sending", at=chain.now(), payroll=payroll)
    post(p, lines, symbol, description or f"{t.id} to {to}", transfer=t, **refs)
    return t


def reverse(t, p, why):
    lines = [(e.kind, e.category, e.credit, e.debit, e.fund) for e in t.ledger_entries]
    post(p, lines, t.symbol, f"reversal of {t.id}: {why}", transfer=t)
    t.status = "failed"


def send(t):
    token = find(Token, t.symbol)
    arg = (f"(record {{ to = {account(address(t.to, t.symbol))}; amount = {t.amount} : nat; fee = null; "
           f"memo = opt {blob(t.id.encode())}; from_subaccount = null; created_at_time = null }})")
    try:
        reply = yield from chain.call(token.ledger, "icrc1_transfer", arg)
    except Exception as e:
        reply, status, tx = str(e), "pending", None
    else:
        status, tx = outcome(reply)
    if t.status == "sending":
        if status == "failed":
            reverse(t, period(), reply[:200])
        else:
            t.status, t.ledger_tx, t.reply = status, tx or "", reply[:200]
    return {"transfer": t.id, "status": t.status, **({"ledger_tx": tx} if tx else {"error": reply[:200]})}


def fund_department(args):
    return owner_of(find(Fund, args["fund"]))


def transfer_fund(t):
    kind, _, key = t.sender.partition(":")
    if kind != "Fund":
        raise Fail(f"{t.id} was not paid from a fund")
    return find(Fund, key)


def positive(args, key="amount"):
    amount = int(args[key])
    if amount <= 0:
        raise Fail(f"{key} must be positive")
    return amount


@op("transfer.create", delegable=True, governed=fund_department, parties=lambda a: {a.get("to", "")},
    returns="transfer")
def transfer_create(c, args):
    """Pay ``amount`` of ``symbol`` (default the realm currency) from ``fund`` to ``to`` (principal, user or org); the
    fund must also cover the ledger fee. Optional ``category`` (default transfer), ``description``, ``contract``.
    A payment that takes the adopted budget of its fund and category past its plan is refused: amend the budget to
    spend more. Returns ``transfer``, ``status``. Example: ``{"fund": "ROADS", "to": "buildco", "amount": 50000}``."""
    fund, amount = find(Fund, args["fund"]), positive(args)
    symbol = args.get("symbol") or currency()
    need = amount + find(Token, symbol).fee
    if balance(fund, symbol) < need:
        raise Conflict(f"fund {fund.id} holds {balance(fund, symbol)} {symbol}, less than {need} with the ledger fee")
    p = period()
    refs = {"contract": find(Contract, args["contract"])} if args.get("contract") else {}
    t = reserve(p, fund, args["to"], symbol, amount, args.get("category", "transfer"), args.get("description", ""),
                **refs)
    return (yield from send(t))


@op("transfer.delete", governed=lambda a: owner_of(transfer_fund(find(Transfer, a["transfer"]))))
def transfer_revert(c, args):
    """Revert a pending ``transfer`` (the ledger rejected its call), refunding its fund; optional ``reason``.
    A transfer still ``sending`` (its ledger call in flight) or ``unconfirmed`` (the ledger's answer was unclear, the
    tokens may have left) cannot be reverted."""
    t = find(Transfer, args["transfer"])
    if t.status != "pending":
        raise Conflict(f"{t.id} is {t.status}; only a pending transfer can be reverted")
    reverse(t, period(), args.get("reason", "reverted"))
    return {"transfer": t.id, "status": t.status}


# --- invoices -------------------------------------------------------------------------------------

def payer_ref(payer):
    return payer if ":" in payer else f"User:{payer}"


def invoice_item(item):
    """One line of an invoice as it is stored: a ``concept`` and an ``amount`` (negative for a discount), and optionally
    a ``quantity``, a ``unit`` and the ``legislation`` that charges it."""
    if not isinstance(item, dict) or set(item) - set(ITEM_FIELDS):
        raise Fail(f"an invoice item has {', '.join(ITEM_FIELDS)}")
    if not isinstance(item.get("concept"), str) or not item["concept"]:
        raise Fail("an invoice item names its concept")
    if type(item.get("amount")) is not int or abs(item["amount"]) > MONEY_MAX:
        raise Fail(f"item '{item['concept']}': amount is a whole number of the token's smallest unit, at most "
                   f"{MONEY_MAX} either way")
    quantity = item.get("quantity")
    if quantity is not None and (type(quantity) is not int or quantity < 0):
        raise Fail(f"item '{item['concept']}': quantity is a whole number, not negative")
    for key in ("unit", "legislation"):
        if item.get(key) is not None and type(item[key]) is not str:
            raise Fail(f"item '{item['concept']}': {key} is text")
    return {k: v for k, v in item.items() if v is not None}


def invoice_items(items):
    """The lines of an invoice, each checked: there is at least one (I-FIN-7)."""
    if not isinstance(items, list) or not items:
        raise Fail("an invoice has at least one item")
    return [invoice_item(item) for item in items]


def total(items):
    return sum(item["amount"] for item in items)


def checked_total(items):
    """The sum of the ``items`` of an invoice, which is never negative (I-FIN-7) and is an amount of money."""
    amount = total(items)
    if not 0 <= amount <= MONEY_MAX:
        raise Fail(f"the items of an invoice sum to {amount}, which is not an amount of money")
    return amount


def new_invoice(payer, items, symbol, due_at, reason="", facts=None):
    """A pending invoice for the sum of its ``items``."""
    find(Token, symbol)
    amount = checked_total(items)
    return Invoice(id=next_id(Invoice, "INV"), payer=payer_ref(payer), amount=amount, items=items, facts=facts or {},
                   symbol=symbol, due_at=int(due_at), reason=reason, status="pending")


def pay_to(inv):
    return {"owner": chain.self_id(), "subaccount": subaccount(inv.id).hex()}


def show(inv):
    return {**view(inv, "id", "payer", "amount", "items", "symbol", "due_at", "status", "reason", "paid_at"),
            "pay_to": pay_to(inv)}


def owns(inv, user):
    kind, _, key = inv.payer.partition(":")
    if kind == "User":
        return key == user
    org = Organization[key] if kind == "Organization" else None
    return bool(org and user in (org.admins or []))


def receive(inv, received):
    """Book a paid invoice as revenue of the source fund, then split it by the adopted allocation rule."""
    p = period()
    config = Treasury["1"].config if Treasury["1"] else {}
    source = find(Fund, config.get("source_fund", "ROOT"))
    category = inv.reason.partition(":")[0].lower() or "invoice"
    lines = [("asset", "cash", inv.amount, 0, source), ("revenue", category, 0, inv.amount, source)]
    rule = adopted_rule(source) if config.get("auto_allocate") else None
    for allocation in (rule.rules if rule else []):
        code, share = allocation["fund"], inv.amount * allocation["share"] // ONE
        if share and code != source.id:
            lines += [("asset", "cash", 0, share, source), ("asset", "cash", share, 0, find(Fund, code))]
    kind, _, key = inv.payer.partition(":")
    who = {"user": key} if kind == "User" else {"organization": Organization[key]} if kind == "Organization" else {}
    post(p, lines, inv.symbol, f"payment of {inv.id}", invoice=inv, **who)
    inv.status, inv.paid_at, inv.payment = "paid", chain.now(), {"received": received, "subaccount": pay_to(inv)}
    if inv.reason.startswith("TaxDeclaration:"):
        find(TaxDeclaration, inv.reason.partition(":")[2]).status = "settled"


def refresh(inv):
    if inv.status not in ("pending", "overdue"):
        return inv.status
    received = yield from balance_of(find(Token, inv.symbol), subaccount(inv.id))
    if received >= inv.amount:
        receive(inv, received)
    elif inv.due_at and chain.now() > inv.due_at:
        inv.status = "overdue"
    return inv.status


@op("treasury.manage")
def invoice_create(c, args):
    """Bill ``payer`` (user id or ``Entity:id``) ``amount`` of ``symbol`` (default the realm currency) in ``items``,
    which sum to ``amount``: each has a ``concept`` and an ``amount`` (negative for a discount), optionally a
    ``quantity``, ``unit`` and ``legislation``. Optionally due at ``due_at``, for ``reason``, with ``facts`` for
    legislation. Returns ``invoice`` and ``pay_to``, the ledger account to pay it into."""
    items, amount = invoice_items(args["items"]), positive(args)
    if total(items) != amount:
        raise Fail(f"the items sum to {total(items)}, not to the amount {amount}")
    inv = new_invoice(args["payer"], items, args.get("symbol") or currency(), args.get("due_at", 0),
                      args.get("reason", ""), args.get("facts"))
    return {"invoice": inv.id, "pay_to": pay_to(inv)}


@op("treasury.manage", returns="invoice")
def invoice_item_add(c, args):
    """Add the line ``item`` (as in ``invoice_create``) to the pending ``invoice``; its amount rises by the line's, or
    falls for a discount, and stays within zero and the largest amount of money. Returns ``invoice``."""
    inv = find(Invoice, args["invoice"])
    if inv.status != "pending":
        raise Conflict(f"{inv.id} is {inv.status}; a line joins only a pending invoice")
    item = invoice_item(args["item"])
    amount = inv.amount + item["amount"]
    if not 0 <= amount <= MONEY_MAX:
        raise Fail(f"{inv.id} would be {amount}, which is not an amount of money")
    inv.items, inv.amount = [*inv.items, item], amount
    return {"invoice": inv.id}


@op("self.invoice_refresh", delegable=True, returns="invoice")
def invoice_check(c, args):
    """Check the ledger for a payment of ``invoice`` (yours or your organization's). Returns its ``status``."""
    inv = find(Invoice, args["invoice"])
    if not owns(inv, c.user):
        raise Forbidden(f"{inv.id} is not yours")
    period()
    return {"invoice": inv.id, "status": (yield from refresh(inv))}


def open_invoices(args):
    """One batch of pending or overdue invoices, in id order from ``from``, and the cursor continuing it."""
    start, count = window(args, REFRESH)
    rows, more = [], False
    for status in ("pending", "overdue"):
        found, nxt = Invoice.find_by("status", status, from_id=start, count=count)
        rows, more = rows + found, more or nxt is not None
    rows.sort(key=lambda i: int(i._id))
    more = more or len(rows) > count
    rows = rows[:count]
    return rows, (int(rows[-1]._id) + 1 if more else None)


@op("invoice.refresh")
def invoice_refresh(c, args):
    """Check the ledger for payments of ``invoice``, or, when omitted, of one batch of pending or overdue invoices
    (``from``, ``count``, at most 20: one ledger call each); ``next`` continues it.
    Returns ``checked``, ``paid`` and each invoice's status in ``invoices``."""
    period()
    rows, nxt = ([find(Invoice, args["invoice"])], None) if args.get("invoice") else open_invoices(args)
    statuses = {}
    for inv in rows:
        statuses[inv.id] = yield from refresh(inv)
    return {"checked": len(rows), "paid": sum(s == "paid" for s in statuses.values()), "invoices": statuses,
            "next": nxt}


@op("self.data_view", read=True, delegable=True)
def my_invoices(c, args):
    """One page of your ``invoices`` (``from``, ``count``), each with its status and ``pay_to``, the ledger account to
    pay it into; ``next`` continues it."""
    rows, nxt = page_by(Invoice, "payer", f"User:{c.user}", args)
    return {"invoices": [show(i) for i in rows], "next": nxt}


# --- treasury -------------------------------------------------------------------------------------

@op("treasury.view", read=True)
def treasury_view(c, args):
    """One page of funds (``from``, ``count``) with each one's balance per token (``funds``); ``next`` continues it.
    Also the treasury ``config``, and the registered tokens and NFTs."""
    rows, nxt = page(Fund, args)
    t = Treasury["1"]
    return {"funds": {f.id: f.balances for f in rows}, "next": nxt, "config": t.config if t else {},
            "tokens": [view(k) for k in Token.load_some(1, REGISTRY)],
            "nft_tokens": [view(k) for k in NFTToken.load_some(1, REGISTRY)]}


@op("treasury.view", read=True, returns="entries")
def ledger_view(c, args):
    """One page of ledger entries (``from``, ``count``), optionally filtered by ``transaction``, ``symbol``, ``kind``,
    ``fund`` and ``period``; ``next`` continues it. A page can hold fewer matches than ``count``: follow ``next``."""
    keys = {k: args[k] for k in ("transaction", "symbol", "kind") if k in args}

    def keep(e):
        return (all(getattr(e, k) == v for k, v in keys.items())
                and (not args.get("fund") or (e.fund and e.fund.id == args["fund"]))
                and (not args.get("period") or (e.period and e.period.id == args["period"])))
    rows, nxt = page(LedgerEntry, args, keep)
    return {"entries": [{**view(e, "id", "transaction", "kind", "category", "debit", "credit", "symbol", "at",
                                "description", "user"),
                         "fund": e.fund.id if e.fund else "", "period": e.period.id if e.period else ""}
                        for e in rows], "next": nxt}


@op("treasury.view", read=True, returns="reports")
def report_view(c, args):
    """The financial ``report`` in full (also as a page of one) or, when ``report`` is omitted, a summary of one
    page of ``reports`` (``from``, ``count``); ``next`` continues it."""
    if args.get("report"):
        report = view(find(FinancialReport, args["report"]))
        return {"report": report, "reports": [report], "next": None}
    rows, nxt = page(FinancialReport, args)
    return {"reports": [view(r, "id", "kind", "as_of", "issued_by", "supersedes") for r in rows], "next": nxt}


@op("treasury.manage", governed=realm_wide)
def treasury_configure(c, args):
    """Update the treasury settings in ``config``: ``epoch``, ``anchor_month``, ``source_fund`` (receives invoice
    payments) and ``auto_allocate`` (split them by its adopted allocation rule). Returns the merged ``config``."""
    t = Treasury["1"] or Treasury(id="1")
    unknown = set(args["config"]) - set(CONFIG)
    if unknown:
        raise Fail(f"unknown treasury settings: {', '.join(sorted(unknown))}")
    if "source_fund" in args["config"]:
        find(Fund, args["config"]["source_fund"])
    t.config = {**(t.config or {}), **args["config"]}
    return {"config": t.config}


def registered(cls, symbol):
    """The entry for ``symbol`` in the token or NFT registry, added if the registry has room."""
    if cls[symbol] is None and cls.count() >= REGISTRY:
        raise Conflict(f"{cls.__name__} registry is full ({REGISTRY})")
    return cls[symbol] or cls(id=symbol)


@op("treasury.manage", governed=realm_wide, returns="token")
def token_register(c, args):
    """Register or update the fungible token ``symbol``: its ``ledger`` canister, optional ``indexer``, ``decimals``
    (default 8) and ``fee`` (default 0). Omitted options keep their current value. At most 100 tokens."""
    t = registered(Token, args["symbol"])
    t.ledger, t.indexer = args["ledger"], args.get("indexer", t.indexer)
    t.decimals, t.fee = int(args.get("decimals", t.decimals)), int(args.get("fee", t.fee))
    return {"token": t.id}


@op("treasury.manage", governed=realm_wide, returns="nft_token")
def nft_token_register(c, args):
    """Register or update the NFT collection ``symbol`` at ``canister``, with optional ``name``, ``description``,
    ``required_license`` (licence kind needed to mint), ``transferable`` and ``supply_cap``; omitted ones are kept.
    At most 100 collections."""
    t = registered(NFTToken, args["symbol"])
    t.canister = args["canister"]
    for k in ("name", "description", "required_license"):
        setattr(t, k, args.get(k, getattr(t, k)))
    t.transferable = bool(args.get("transferable", t.transferable))
    t.supply_cap = int(args.get("supply_cap", t.supply_cap))
    return {"nft_token": t.id}


# --- funds, periods, budgets, allocation ----------------------------------------------------------

@op("org.manage_budget", governed=realm_wide, returns="fund")
def fund_create(c, args):
    """Create the fund with code ``fund``, optional ``name``, ``kind`` (default general) and owning ``department``.
    A department owns at most one fund."""
    if Fund[args["fund"]]:
        raise Conflict(f"fund {args['fund']} exists")
    dept = find(Department, args["department"]) if args.get("department") else None
    if dept is not None and dept.fund is not None:
        raise Conflict(f"{dept.id} already owns fund {dept.fund.id}")
    f = Fund(id=args["fund"], name=args.get("name", args["fund"]), kind=args.get("kind", "general"))
    f.department = dept
    return {"fund": f.id}


@op("org.manage_budget", governed=realm_wide, returns="period")
def period_open(c, args):
    """Open the fiscal ``period`` from ``starts_at`` to ``ends_at`` (seconds), optional ``name``. Periods open in
    order: it starts no earlier than the latest period ends."""
    starts, ends = int(args["starts_at"]), int(args["ends_at"])
    if ends <= starts:
        raise Fail(f"period {args['period']} ends before it starts")
    if FiscalPeriod[args["period"]]:
        raise Conflict(f"period {args['period']} exists")
    latest = FiscalPeriod.load_some(FiscalPeriod.max_id(), 1) if FiscalPeriod.max_id() else []
    if latest and starts < latest[0].ends_at:
        raise Conflict(f"starts before fiscal period {latest[0].id} ends")
    p = FiscalPeriod(id=args["period"], name=args.get("name", args["period"]), starts_at=starts, ends_at=ends)
    return {"period": p.id}


@op("org.manage_budget", governed=realm_wide)
def period_close(c, args):
    """Close the fiscal ``period`` and its adopted budgets. Nothing can be booked while a closed period covers now."""
    p = find(FiscalPeriod, args["period"])
    if p.status != "open":
        raise Conflict(f"{p.id} is {p.status}")
    p.status = "closed"
    for b in p.budgets:
        if b.status in IN_FORCE:
            b.status = "closed"
    return {"period": p.id, "status": p.status}


def budget_department(args):
    return owner_of(find(Budget, args["budget"]).fund)


def supersede(b):
    """Replace the line of the same slot that is in force, adopted or extended: it becomes ``amended``, and the spending
    booked against it carries over."""
    for old in each(Budget, "slot", b.slot):
        if old.id != b.id and old.status in IN_FORCE:
            old.status = "amended"
            b.actual += old.actual


def other_kind_in_slot(fund, p, category, kind):
    """A line of the other kind in the slot of ``fund``, ``p`` and ``category``, whatever its status, if any."""
    return next((b for b in each(Budget, "slot", slot(fund, p, category)) if b.kind != kind), None)


@op("org.manage_budget", governed=fund_department, returns="budget")
def budget_draft(c, args):
    """Draft a budget line for ``fund`` in the open ``period``: ``planned`` amount for ``category``, of ``kind``
    ``revenue`` or ``expense``; a category is one kind in a fund and period. Returns ``budget``."""
    fund, p = find(Fund, args["fund"]), find(FiscalPeriod, args["period"])
    if p.status != "open":
        raise Conflict(f"{p.id} is {p.status}")
    if args["kind"] not in KINDS:
        raise Fail(f"kind is one of {', '.join(KINDS)}")
    other = other_kind_in_slot(fund, p, args["category"], args["kind"])
    if other:  # a line's slot has no kind: a category is revenue or expense, not both
        raise Fail(f"{args['category']} is a {other.kind} category of fund {fund.id} in {p.id}, not {args['kind']}")
    b = Budget(id=next_id(Budget, "B"), fund=fund, period=p, category=args["category"],
               slot=slot(fund, p, args["category"]), kind=args["kind"], planned=int(args["planned"]))
    return {"budget": b.id}


def proposed_lines(args):
    """The lines a budget_propose call proposes: every draft line of its period."""
    return [b for b in find(FiscalPeriod, args["period"]).budgets if b.status == "draft"]


def budget_totals(args):
    """The facts of a budget_propose call that are not its arguments (GGG's derived facts): ``revenue`` and ``expense``,
    the sums of ``planned`` over the lines it proposes of each kind."""
    totals = {"revenue": 0, "expense": 0}
    for b in proposed_lines(args):
        totals[b.kind] += b.planned
    return totals


def check_budget_proposal(args):
    """A proposal of a budget the period has no lines for, of no years, or with a total out of range files nothing."""
    p = find(FiscalPeriod, args["period"])
    if not proposed_lines(args):
        raise Conflict(f"{p.id} has no draft budget lines to propose")
    if args.get("years") is not None and int(args["years"]) < 1:
        raise Fail("a budget covers at least one year")
    for name, amount in budget_totals(args).items():
        if amount > MONEY_MAX:
            raise Fail(f"the planned {name} of {p.id} is {amount}, above the largest amount of money {MONEY_MAX}")


@op("org.manage_budget", governed=realm_wide, check=check_budget_proposal, derived=budget_totals)
def budget_propose(c, args):
    """Adopt every draft line of the open fiscal ``period`` together; ``years`` says how many periods the plan covers,
    when more than one (a fact for legislation). Each line replaces the adopted or extended line of its fund and
    category, and the spending booked against that carries over; every extended line of a fund whose lines it adopts
    is replaced (``amended``), whether the new budget names its category or not, and the extensions of the other
    funds stand. Returns ``period``, ``budgets`` and the planned ``revenue`` and ``expense``."""
    p, lines = find(FiscalPeriod, args["period"]), proposed_lines(args)
    if p.status != "open" or not lines:
        raise Conflict(f"{p.id} is {p.status}; only draft lines for an open period can be adopted")
    totals = budget_totals(args)
    for b in lines:
        supersede(b)
        b.status, b.proposal = "adopted", c.proposal
    adopted_funds = {b.fund.id for b in lines}
    for b in p.budgets:  # a fund's adopted budget stands alone: no extended line of the fund stands beside it
        if b.status == "extended" and b.fund.id in adopted_funds:
            b.status = "amended"
    return {"period": p.id, "budgets": [b.id for b in lines], **totals}


def previous_period(p):
    """The fiscal period opened just before ``p``, if any. Periods open in order, so their rows do."""
    n = int(p._id) - 1
    return FiscalPeriod.load_some(n, 1)[0] if n >= 1 else None


def standing_at_end(p):
    """The budget of period ``p`` as it stood when it ended: of each fund and category, the newest line that was adopted
    or extended (``closed`` once the period is), never one a newer line replaced."""
    newest = {}
    for b in sorted(p.budgets, key=lambda line: int(line._id)):
        if b.status in (*IN_FORCE, "closed"):
            newest[b.slot] = b
    return list(newest.values())


@op("org.manage_budget", returns="extended")
def budget_extend(c, args):
    """Carry the budget of the period before the open fiscal ``period`` over into it, as ``extended`` lines, without a
    vote, fund by fund: for each fund that has no extended, proposed or adopted line in the period yet (draft lines do
    not count). They hold, and cap spending, until an adopted budget of the fund replaces them. Refused with Conflict
    when no fund is left to carry. The lines carried are those of the earlier period as they stood at its end,
    adopted, extended or closed with it, except a line whose category the period already holds as the other kind,
    even in a draft. Returns ``extended``, how many lines."""
    p = find(FiscalPeriod, args["period"])
    if p.status != "open":
        raise Conflict(f"{p.id} is {p.status}")
    held = {b.fund.id for b in p.budgets if b.status in ("extended", "proposed", "adopted")}
    before = previous_period(p)
    lines = [b for b in standing_at_end(before)
             if b.fund.id not in held and not other_kind_in_slot(b.fund, p, b.category, b.kind)] if before else []
    if not lines:
        raise Conflict(f"no fund is left to carry into {p.id}: the period before has no budget for a fund without a line "
                       f"in it")
    for b in lines:
        Budget(id=next_id(Budget, "B"), fund=b.fund, period=p, category=b.category, slot=slot(b.fund, p, b.category),
               kind=b.kind, planned=b.planned, status="extended")
    return {"period": p.id, "extended": len(lines)}


@op("org.manage_budget", governed=budget_department, returns="budget")
def budget_amend(c, args):
    """Replace the adopted ``budget`` with a new adopted version planning ``planned``. Returns the new ``budget``."""
    old = find(Budget, args["budget"])
    if old.status != "adopted":
        raise Conflict(f"{old.id} is {old.status}; only an adopted budget can be amended")
    b = Budget(id=next_id(Budget, "B"), fund=old.fund, period=old.period, category=old.category, slot=old.slot,
               kind=old.kind, planned=int(args["planned"]), proposal=c.proposal)
    supersede(b)
    b.status = "adopted"
    return {"budget": b.id, "amends": old.id}


def rule_department(args):
    rule = AllocationRule[args["rule"]] if "rule" in args else None
    return owner_of(find(Fund, rule.source_fund if rule else args["source_fund"]))


def shares_of(args):
    """The ``rules`` of an ``allocation_rule_draft`` call, a list of ``{fund, share}`` as GGG has it, refused if it is
    not one or its shares are not whole numbers of hundred-millionths adding up to at most 100 %."""
    rules = args.get("rules")
    if not isinstance(rules, list):
        raise Fail("rules is a list of {fund, share}, each share in hundred-millionths: 30 % of WELFARE is "
                   f'[{{"fund": "WELFARE", "share": {30 * PERCENT}}}]')
    shares = []
    for rule in rules:
        if not isinstance(rule, dict) or not isinstance(rule.get("fund"), str) or type(rule.get("share")) is not int:
            raise Fail("each rule is a {fund, share}: a fund code and a whole number of hundred-millionths")
        shares.append({"fund": rule["fund"], "share": rule["share"]})
    if len({s["fund"] for s in shares}) < len(shares) or any(s["share"] < 0 for s in shares) \
            or sum(s["share"] for s in shares) > ONE:
        raise Fail(f"each fund is named once, and the shares are not negative and add up to at most {ONE} (100 %)")
    return shares


@op("org.manage_budget", governed=rule_department, returns="rule", check=shares_of)
def allocation_rule_draft(c, args):
    """Draft how payments into ``source_fund`` are split: ``rules`` is a list of ``{fund, share}``, each share in
    hundred-millionths (30 % is 30000000), adding up to at most 100000000. Returns ``rule``."""
    source, shares = find(Fund, args["source_fund"]), shares_of(args)
    for share in shares:
        find(Fund, share["fund"])
    r = AllocationRule(id=next_id(AllocationRule, "AR"), source_fund=source.id, rules=shares)
    return {"rule": r.id}


@op("org.manage_budget", governed=rule_department)
def allocation_rule_propose(c, args):
    """Adopt the draft allocation ``rule``, superseding its source fund's adopted rule."""
    r = find(AllocationRule, args["rule"])
    if r.status != "draft":
        raise Conflict(f"{r.id} is {r.status}; only a draft can be adopted")
    source = find(Fund, r.source_fund)
    old = adopted_rule(source)
    if old:
        old.status = "superseded"
    source.allocation_rule = r.id
    r.status, r.proposal = "adopted", c.proposal
    return {"rule": r.id, "status": r.status}


@op("realm.data_view", read=True)
def budget_view(c, args):
    """How the realm budgets its money, for any member: one page of ``funds`` (``from``, ``count``; ``next`` continues
    it), each with its ``balances``, its adopted allocation ``rule`` and its ``budgets`` in force (adopted or extended)
    in one fiscal ``period`` (an id; default the one covering now, null if none does); the latest fiscal ``periods`` to
    choose from; the treasury ``config``; the accounting ``currency`` with its ``decimals``; and up to ten newest
    ``rule_drafts``. Single ledger entries stay with ``ledger_view``."""
    chosen = find(FiscalPeriod, args["period"]) if args.get("period") else period_at(chain.now())
    lines = {}  # fund id -> the budgets in force in the chosen period: one period's lines, however old the funds
    for b in chosen.budgets if chosen else []:
        if b.status in IN_FORCE:
            lines.setdefault(b.fund.id, []).append({**view(b, "id", "category", "kind", "planned", "actual", "status"),
                                                     "period": chosen.id})
    rows, nxt = page(Fund, args)
    funds = []
    for f in rows:
        rule = adopted_rule(f)
        funds.append({**view(f, "id", "name", "kind", "balances"), "department": owner_of(f).id,
                      "rule": view(rule, "id", "rules") if rule else None, "budgets": lines.get(f.id, [])})
    t, symbol = Treasury["1"], currency()
    drafts = [r for r in latest(AllocationRule) if r.status == "draft"][:DRAFTS]
    return {"funds": funds, "next": nxt, "currency": symbol, "decimals": decimals_of(symbol),
            "period": chosen.id if chosen else None,
            "periods": [view(p, "id", "name", "starts_at", "ends_at", "status") for p in reversed(latest(FiscalPeriod))],
            "config": t.config if t else {},
            "rule_drafts": [view(r, "id", "source_fund", "rules") for r in drafts]}


# --- payroll and reports --------------------------------------------------------------------------

@op("org.manage_budget", governed=lambda a: find(Department, a["department"]))
def payroll_run(c, args):
    """Pay each active, non-agent appointee of ``department`` their position's salary in ``symbol`` (default the realm
    currency) from its fund, once per ``period`` label (e.g. 2026-11). Returns ``transfers``, ``seats``, ``total``."""
    dept, label = find(Department, args["department"]), args["period"]
    fund, symbol = dept.fund, args.get("symbol") or currency()
    if fund is None:
        raise Conflict(f"{dept.id} has no fund")
    note = f"payroll {label} {dept.id}"
    if first(Transfer, "payroll", note):
        raise Conflict(f"{dept.id} payroll for {label} already ran")
    seats = [(a.user, pos.salary) for pos in dept.positions if pos.salary > 0
             for a in pos.appointments if a.status == "active" and not a.agent]
    total = sum(s for _, s in seats)
    fees = len(seats) * find(Token, symbol).fee
    need = total + fees
    if balance(fund, symbol) < need:
        raise Conflict(f"fund {fund.id} holds {balance(fund, symbol)} {symbol}, payroll needs {need}")
    p = period()
    refuse_overspend(p, spending(fund, "payroll", total, fees))  # all or none
    batch = [reserve(p, fund, user, symbol, salary, "payroll", f"{note}: {user}", payroll=note, user=user)
             for user, salary in seats]
    results = []
    for t in batch:
        results.append((yield from send(t)))
    return {"transfers": results, "seats": len(batch), "total": total}


@op("treasury.manage", signing=True, returns="report")
def report_issue(c, args):
    """Freeze the statements of fiscal ``period`` as a report of ``kind`` (default period_close), with a hash chained
    over its ledger entries in id order, superseding the previous one of that kind. The issuer may add figures the
    ledger does not keep, as a map of ``adjustments``; they stand beside the ledger's in the statements and outside
    the hash. Returns ``report``, ``source_hash``, ``supersedes``."""
    p, kind = find(FiscalPeriod, args["period"]), args.get("kind", "period_close")
    s = p.statements
    statements = {"accounts": dict(sorted(s["accounts"].items())),
                  "fund_balances": dict(sorted(s["fund_balances"].items())), "debit": s["debit"], "credit": s["credit"]}
    if args.get("adjustments") is not None:
        if not isinstance(args["adjustments"], dict):
            raise Fail("adjustments is a map of the figures the issuer adds")
        statements["adjustments"] = args["adjustments"]
    previous = [r for r in p.reports if r.kind == kind]
    r = FinancialReport(id=next_id(FinancialReport, "FR"), kind=kind, period=p, as_of=chain.now(),
                        statements=statements, source_hash="sha256:" + p.source_hash,
                        issued_by=c.user, supersedes=previous[-1].id if previous else "")
    return {"report": r.id, "source_hash": r.source_hash, "supersedes": r.supersedes}


# --- loans ----------------------------------------------------------------------------------------

def period_of(term):
    """A loan's ``term`` as the Period it is: a ``count`` of ``unit``, one of day, working_day, month or year."""
    if not isinstance(term, dict) or type(term.get("count")) is not int or term.get("unit") not in PERIOD_UNITS:
        raise Fail(f"a term is a count of {', '.join(PERIOD_UNITS)}")
    return {"count": term["count"], "unit": term["unit"]}


def treasury_row():
    return Treasury["1"] or Treasury(id="1")


@op("org.manage_budget", governed=realm_wide, returns="loan")
def loan_create(c, args):
    """Record that the realm borrowed ``amount`` from the outside body ``lender`` for ``term`` at ``rate``,
    for the fund ``fund``, in the accounting currency, with optional ``facts`` for legislation. The money moves
    outside the realm; this records the debt. Returns ``loan``."""
    amount, symbol = positive(args), currency()
    if not symbol:
        raise Conflict("the realm has no accounting currency")
    find(Fund, args["fund"])
    if treasury_row().debt_outstanding + amount > MONEY_MAX:
        raise Fail(f"the debt of the realm would pass the largest amount of money, {MONEY_MAX}")
    loan = Loan(id=next_id(Loan, "LOAN"), lender=args["lender"], amount=amount, symbol=symbol,
                term=period_of(args["term"]), rate=int(args["rate"]), fund=args["fund"], outstanding=amount,
                facts=args.get("facts") or {})
    treasury_row().debt_outstanding += amount
    return {"loan": loan.id}


@op("org.manage_budget", returns="loan")
def loan_repay(c, args):
    """Record that ``amount`` of the ``loan`` was repaid, with optional ``reason``. A repayment never exceeds what is
    outstanding; the loan that reaches zero is repaid. Returns ``loan``."""
    loan, amount = find(Loan, args["loan"]), positive(args)
    if amount > loan.outstanding:
        raise Conflict(f"{loan.id} has {loan.outstanding} outstanding, less than {amount}")
    loan.outstanding -= amount
    if loan.outstanding == 0:
        loan.status = "repaid"
    treasury_row().debt_outstanding -= amount
    return {"loan": loan.id}


@op("treasury.view", read=True, returns="loans")
def loan_list(c, args):
    """One page of the realm's loans (``from``, ``count``), optionally only those with ``status`` active or repaid;
    ``next`` continues it. A page can hold fewer matches than ``count``: follow ``next``."""
    rows, nxt = page(Loan, args, lambda loan: args.get("status") in (None, loan.status))
    return {"loans": [view(loan) for loan in rows], "next": nxt}


# --- payment accounts -----------------------------------------------------------------------------

def account_owner(c, args):
    org = args.get("organization")
    if not org:
        return f"User:{c.user}"
    if c.user not in (find(Organization, org).admins or []):
        raise Forbidden(f"you do not administer {org}")
    return f"Organization:{org}"


@op("self.data_manage", returns="account")
def payment_account_add(c, args):
    """Add a payout account (``network``, ``address``, optional ``symbol`` and ``label``) for you, or for
    ``organization`` if you administer it. Transfers in ``symbol`` are paid to it. Returns ``account``."""
    a = PaymentAccount(id=next_id(PaymentAccount, "PA"), owner=account_owner(c, args), network=args["network"],
                       address=args["address"], symbol=args.get("symbol", ""), label=args.get("label", ""))
    return {"account": a.id}


@op("self.data_manage", returns="account")
def payment_account_remove(c, args):
    """Remove your payout ``account``, or with ``organization`` one of an organization you administer. Returns the
    ``account`` as it was."""
    a = find(PaymentAccount, args["account"])
    if a.owner != account_owner(c, args):
        raise Forbidden(f"{a.id} is not yours")
    gone = view(a)
    a.delete()
    return {"account": gone}


# --- court-ordered token actions ------------------------------------------------------------------

@op("token.force_transfer", signing=True, governed=realm_wide, parties=lambda a: {a.get("from", ""), a.get("to", "")},
    returns="transfer")
def token_force_transfer(c, args):
    """By court order, move ``amount`` of ``symbol`` from principal ``from`` to principal ``to`` on the token's ledger,
    with optional ``reason`` as memo. Returns ``transfer``, ``status`` and ``ledger_tx``."""
    token, amount = find(Token, args["symbol"]), positive(args)
    p = period()
    t = Transfer(id=next_id(Transfer, "TR"), sender=args["from"], to=args["to"], symbol=token.id, amount=amount,
                 status="sending", at=chain.now())
    arg = (f'(record {{ from = {account(args["from"])}; to = {account(args["to"])}; amount = {amount} : nat; '
           f'memo = opt {text(args.get("reason", "")[:64])} }})')
    reply = yield from chain.call(token.ledger, "force_transfer", arg)
    status, tx = outcome(reply)
    if status != "completed":
        t.status, t.reply = status, reply[:200]
        return {"transfer": t.id, "status": t.status, "error": reply[:200]}
    post(p, [("asset", "custody", amount, 0, None), ("asset", "custody", 0, amount, None)], token.id,
         f"court-ordered {t.id}: {args['from']} to {args['to']}", transfer=t)
    t.status, t.ledger_tx = "completed", tx
    return {"transfer": t.id, "status": t.status, "ledger_tx": tx}


@op("token.freeze", signing=True, governed=realm_wide, parties=lambda a: {a.get("account", "")})
def token_freeze(c, args):
    """Freeze principal ``account`` on the ``symbol`` ledger with optional ``reason``; ``frozen`` false unfreezes it."""
    token = find(Token, args["symbol"])
    method = "freeze_account" if args.get("frozen", True) else "unfreeze_account"
    reply = yield from chain.call(token.ledger, method,
                                  f'(record {{ account = {account(args["account"])}; '
                                  f'reason = opt {text(args.get("reason", "")[:64])} }})')
    if outcome(reply)[0] != "completed":
        raise Fail(f"{method} not confirmed: {reply[:200]}")
    return {"account": args["account"], "frozen": method == "freeze_account"}
