import hashlib
import json

from core import candid
from core import chain
from core.db import Fail, each, find, first, next_id, page, page_by, view, window
from ggg import (AllocationRule, Budget, Contract, Department, FinancialReport, FiscalPeriod, Fund, Invoice,
                 LedgerEntry, NFTToken, Organization, PaymentAccount, Realm, TaxDeclaration, Token,
                 Transfer, Treasury)
from core.op import op
from api.proposals import realm_wide, root

CONFIG = ("epoch", "anchor_month", "source_fund", "auto_allocate")
REGISTRY = 100  # tokens, and NFT collections, a realm can register: treasury_view lists them all
REFRESH = 20  # invoices one invoice_refresh checks, one ledger call each


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


def outcome(reply):
    # 17_724 is how an unnamed candid decoder labels the Ok case.
    found = candid.number(reply, "Ok =") if "Ok =" in reply else candid.number(reply, "17_724 =")
    return None if found is None else str(found)


# --- books ----------------------------------------------------------------------------------------

def currency():
    realm = Realm["1"]
    if realm and realm.accounting_currency:
        return realm.accounting_currency
    oldest = Token.load_some(1, 1)
    return oldest[0].id if oldest else ""


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
        raise Fail(f"fiscal period {p.id} is {p.status}")
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


def post(p, lines, symbol, description="", **refs):
    """One balanced transaction: lines are (kind, category, debit, credit, fund). The only place entries are
    booked, so it keeps fund balances, adopted budgets' actuals and the period's statements."""
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
                if b.status == "adopted":
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


def reserve(p, fund, to, symbol, amount, category, description="", payroll=None, **refs):
    t = Transfer(id=next_id(Transfer, "TR"), sender=f"Fund:{fund.id}", to=to, symbol=symbol, amount=amount,
                 status="sending", at=chain.now(), payroll=payroll)
    fee = find(Token, symbol).fee
    post(p, [("expense", category, amount, 0, fund), ("expense", "ledger_fee", fee, 0, fund),
             ("asset", "cash", 0, amount + fee, fund)], symbol, description or f"{t.id} to {to}", transfer=t, **refs)
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
        reply, rejected = (yield from chain.call(token.ledger, "icrc1_transfer", arg)), False
    except Exception as e:
        reply, rejected = str(e), True
    tx = None if rejected else outcome(reply)
    if t.status == "sending":
        if rejected:
            t.status = "pending"
        elif tx is None:
            reverse(t, period(), reply[:200])
        else:
            t.status, t.ledger_tx = "completed", tx
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


@op("transfer.create", delegable=True, governed=fund_department, parties=lambda a: {a.get("to", "")})
def transfer_create(c, args):
    """Pay ``amount`` of ``symbol`` (default the realm currency) from ``fund`` to ``to`` (principal, user or org); the
    fund must also cover the ledger fee. Optional ``category`` (default transfer), ``description``, ``contract``.
    Returns ``transfer``, ``status``. Example: ``{"fund": "ROADS", "to": "buildco", "amount": 50000}``."""
    fund, amount = find(Fund, args["fund"]), positive(args)
    symbol = args.get("symbol") or currency()
    need = amount + find(Token, symbol).fee
    if balance(fund, symbol) < need:
        raise Fail(f"fund {fund.id} holds {balance(fund, symbol)} {symbol}, less than {need} with the ledger fee")
    p = period()
    refs = {"contract": find(Contract, args["contract"])} if args.get("contract") else {}
    t = reserve(p, fund, args["to"], symbol, amount, args.get("category", "transfer"), args.get("description", ""),
                **refs)
    return (yield from send(t))


@op("transfer.delete", governed=lambda a: owner_of(transfer_fund(find(Transfer, a["transfer"]))))
def transfer_revert(c, args):
    """Revert a pending ``transfer`` (the ledger rejected its call), refunding its fund; optional ``reason``.
    A transfer still ``sending`` (its ledger call in flight) cannot be reverted."""
    t = find(Transfer, args["transfer"])
    if t.status != "pending":
        raise Fail(f"{t.id} is {t.status}; only a pending transfer can be reverted")
    reverse(t, period(), args.get("reason", "reverted"))
    return {"transfer": t.id, "status": t.status}


# --- invoices -------------------------------------------------------------------------------------

def payer_ref(payer):
    return payer if ":" in payer else f"User:{payer}"


def new_invoice(payer, amount, symbol, due_at, reason=""):
    find(Token, symbol)
    return Invoice(id=next_id(Invoice, "INV"), payer=payer_ref(payer), amount=amount, symbol=symbol,
                   due_at=int(due_at), reason=reason, status="pending")


def pay_to(inv):
    return {"owner": chain.self_id(), "subaccount": subaccount(inv.id).hex()}


def show(inv):
    return {**view(inv, "id", "payer", "amount", "symbol", "due_at", "status", "reason", "paid_at"),
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
    for code, percent in ((rule.rules or {}).items() if rule else ()):
        share = inv.amount * int(percent) // 100
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
    """Bill ``payer`` (user id or ``Entity:id``) ``amount`` of ``symbol`` (default the realm currency), optionally
    due at ``due_at`` and for ``reason``. Returns ``invoice`` and ``pay_to``, the ledger account to pay it into."""
    inv = new_invoice(args["payer"], positive(args), args.get("symbol") or currency(), args.get("due_at", 0),
                      args.get("reason", ""))
    return {"invoice": inv.id, "pay_to": pay_to(inv)}


@op("self.invoice_refresh", delegable=True)
def invoice_check(c, args):
    """Check the ledger for a payment of ``invoice`` (yours or your organization's). Returns its ``status``."""
    inv = find(Invoice, args["invoice"])
    if not owns(inv, c.user):
        raise Fail(f"{inv.id} is not yours")
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


@op("treasury.view", read=True)
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


@op("treasury.view", read=True)
def report_view(c, args):
    """The financial ``report`` in full or, when ``report`` is omitted, a summary of one page of reports (``from``,
    ``count``); ``next`` continues it."""
    if args.get("report"):
        return {"report": view(find(FinancialReport, args["report"]))}
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
        raise Fail(f"{cls.__name__} registry is full ({REGISTRY})")
    return cls[symbol] or cls(id=symbol)


@op("treasury.manage", governed=realm_wide)
def token_register(c, args):
    """Register or update the fungible token ``symbol``: its ``ledger`` canister, optional ``indexer``, ``decimals``
    (default 8) and ``fee`` (default 0). Omitted options keep their current value. At most 100 tokens."""
    t = registered(Token, args["symbol"])
    t.ledger, t.indexer = args["ledger"], args.get("indexer", t.indexer)
    t.decimals, t.fee = int(args.get("decimals", t.decimals)), int(args.get("fee", t.fee))
    return {"token": t.id}


@op("treasury.manage", governed=realm_wide)
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

@op("org.manage_budget", governed=realm_wide)
def fund_create(c, args):
    """Create the fund with code ``fund``, optional ``name``, ``kind`` (default general) and owning ``department``.
    A department owns at most one fund."""
    if Fund[args["fund"]]:
        raise Fail(f"fund {args['fund']} exists")
    dept = find(Department, args["department"]) if args.get("department") else None
    if dept is not None and dept.fund is not None:
        raise Fail(f"{dept.id} already owns fund {dept.fund.id}")
    f = Fund(id=args["fund"], name=args.get("name", args["fund"]), kind=args.get("kind", "general"))
    f.department = dept
    return {"fund": f.id}


@op("org.manage_budget", governed=realm_wide)
def period_open(c, args):
    """Open the fiscal ``period`` from ``starts_at`` to ``ends_at`` (seconds), optional ``name``. Periods open in
    order: it starts no earlier than the latest period ends."""
    starts, ends = int(args["starts_at"]), int(args["ends_at"])
    if FiscalPeriod[args["period"]] or ends <= starts:
        raise Fail(f"period {args['period']} exists or ends before it starts")
    latest = FiscalPeriod.load_some(FiscalPeriod.max_id(), 1) if FiscalPeriod.max_id() else []
    if latest and starts < latest[0].ends_at:
        raise Fail(f"starts before fiscal period {latest[0].id} ends")
    p = FiscalPeriod(id=args["period"], name=args.get("name", args["period"]), starts_at=starts, ends_at=ends)
    return {"period": p.id}


@op("org.manage_budget", governed=realm_wide)
def period_close(c, args):
    """Close the fiscal ``period`` and its adopted budgets. Nothing can be booked while a closed period covers now."""
    p = find(FiscalPeriod, args["period"])
    if p.status != "open":
        raise Fail(f"{p.id} is {p.status}")
    p.status = "closed"
    for b in p.budgets:
        if b.status == "adopted":
            b.status = "closed"
    return {"period": p.id, "status": p.status}


def budget_department(args):
    return owner_of(find(Budget, args["budget"]).fund)


def supersede(b, status):
    for old in each(Budget, "slot", b.slot):
        if old.id != b.id and old.status == "adopted":
            old.status = status
            b.actual += old.actual


@op("org.manage_budget", governed=fund_department)
def budget_draft(c, args):
    """Draft a budget for ``fund`` in the open ``period``: ``planned`` amount for ``category``, ``kind`` (default
    expense). Returns ``budget``."""
    fund, p = find(Fund, args["fund"]), find(FiscalPeriod, args["period"])
    if p.status != "open":
        raise Fail(f"{p.id} is {p.status}")
    b = Budget(id=next_id(Budget, "B"), fund=fund, period=p, category=args["category"],
               slot=slot(fund, p, args["category"]), kind=args.get("kind", "expense"), planned=int(args["planned"]))
    return {"budget": b.id}


@op("org.manage_budget", governed=budget_department)
def budget_propose(c, args):
    """Adopt the draft ``budget``, replacing the adopted one for the same fund, period and category.
    Spending booked against the replaced budget carries over."""
    b = find(Budget, args["budget"])
    if b.status != "draft" or b.period.status != "open":
        raise Fail(f"{b.id} is {b.status}; only a draft for an open period can be adopted")
    supersede(b, "amended")
    b.status, b.proposal = "adopted", c.proposal
    return {"budget": b.id, "status": b.status}


@op("org.manage_budget", governed=budget_department)
def budget_amend(c, args):
    """Replace the adopted ``budget`` with a new adopted version planning ``planned``. Returns the new ``budget``."""
    old = find(Budget, args["budget"])
    if old.status != "adopted":
        raise Fail(f"{old.id} is {old.status}; only an adopted budget can be amended")
    b = Budget(id=next_id(Budget, "B"), fund=old.fund, period=old.period, category=old.category, slot=old.slot,
               kind=old.kind, planned=int(args["planned"]), proposal=c.proposal)
    supersede(b, "amended")
    b.status = "adopted"
    return {"budget": b.id, "amends": old.id}


def rule_department(args):
    rule = AllocationRule[args["rule"]] if "rule" in args else None
    return owner_of(find(Fund, rule.source_fund if rule else args["source_fund"]))


@op("org.manage_budget", governed=rule_department)
def allocation_rule_draft(c, args):
    """Draft how payments into ``source_fund`` are split: ``rules`` maps fund codes to whole percentages adding up to at
    most 100. Returns ``rule``."""
    source, rules = find(Fund, args["source_fund"]), {k: int(v) for k, v in args["rules"].items()}
    for code in rules:
        find(Fund, code)
    if any(v < 0 for v in rules.values()) or sum(rules.values()) > 100:
        raise Fail("shares must be non-negative percentages adding up to at most 100")
    r = AllocationRule(id=next_id(AllocationRule, "AR"), source_fund=source.id, rules=rules)
    return {"rule": r.id}


@op("org.manage_budget", governed=rule_department)
def allocation_rule_propose(c, args):
    """Adopt the draft allocation ``rule``, superseding its source fund's adopted rule."""
    r = find(AllocationRule, args["rule"])
    if r.status != "draft":
        raise Fail(f"{r.id} is {r.status}; only a draft can be adopted")
    source = find(Fund, r.source_fund)
    old = adopted_rule(source)
    if old:
        old.status = "superseded"
    source.allocation_rule = r.id
    r.status, r.proposal = "adopted", c.proposal
    return {"rule": r.id, "status": r.status}


# --- payroll and reports --------------------------------------------------------------------------

@op("org.manage_budget", governed=lambda a: find(Department, a["department"]))
def payroll_run(c, args):
    """Pay each active, non-agent appointee of ``department`` their position's salary in ``symbol`` (default the realm
    currency) from its fund, once per ``period`` label (e.g. 2026-11). Returns ``transfers``, ``seats``, ``total``."""
    dept, label = find(Department, args["department"]), args["period"]
    fund, symbol = dept.fund, args.get("symbol") or currency()
    if fund is None:
        raise Fail(f"{dept.id} has no fund")
    note = f"payroll {label} {dept.id}"
    if first(Transfer, "payroll", note):
        raise Fail(f"{dept.id} payroll for {label} already ran")
    seats = [(a.user, pos.salary) for pos in dept.positions if pos.salary > 0
             for a in pos.appointments if a.status == "active" and not a.agent]
    total = sum(s for _, s in seats)
    need = total + len(seats) * find(Token, symbol).fee
    if balance(fund, symbol) < need:
        raise Fail(f"fund {fund.id} holds {balance(fund, symbol)} {symbol}, payroll needs {need}")
    p = period()
    batch = [reserve(p, fund, user, symbol, salary, "payroll", f"{note}: {user}", payroll=note, user=user)
             for user, salary in seats]
    results = []
    for t in batch:
        results.append((yield from send(t)))
    return {"transfers": results, "seats": len(batch), "total": total}


@op("treasury.manage", signing=True)
def report_issue(c, args):
    """Freeze the statements of fiscal ``period`` as a report of ``kind`` (default period_close), with a hash chained
    over its ledger entries in id order, superseding the previous one of that kind. Returns ``report``,
    ``source_hash``, ``supersedes``."""
    p, kind = find(FiscalPeriod, args["period"]), args.get("kind", "period_close")
    s = p.statements
    statements = {"accounts": dict(sorted(s["accounts"].items())),
                  "fund_balances": dict(sorted(s["fund_balances"].items())), "debit": s["debit"], "credit": s["credit"]}
    previous = [r for r in p.reports if r.kind == kind]
    r = FinancialReport(id=next_id(FinancialReport, "FR"), kind=kind, period=p, as_of=chain.now(),
                        statements=statements, source_hash="sha256:" + p.source_hash,
                        issued_by=c.user, supersedes=previous[-1].id if previous else "")
    return {"report": r.id, "source_hash": r.source_hash, "supersedes": r.supersedes}


# --- payment accounts -----------------------------------------------------------------------------

def account_owner(c, args):
    org = args.get("organization")
    if not org:
        return f"User:{c.user}"
    if c.user not in (find(Organization, org).admins or []):
        raise Fail(f"you do not administer {org}")
    return f"Organization:{org}"


@op("self.data_manage", delegable=True)
def payment_account_add(c, args):
    """Add a payout account (``network``, ``address``, optional ``symbol`` and ``label``) for you, or for
    ``organization`` if you administer it. Transfers in ``symbol`` are paid to it. Returns ``account``."""
    a = PaymentAccount(id=next_id(PaymentAccount, "PA"), owner=account_owner(c, args), network=args["network"],
                       address=args["address"], symbol=args.get("symbol", ""), label=args.get("label", ""))
    return {"account": a.id}


@op("self.data_manage", delegable=True)
def payment_account_remove(c, args):
    """Remove your payout ``account``, or with ``organization`` one of an organization you administer."""
    a = find(PaymentAccount, args["account"])
    if a.owner != account_owner(c, args):
        raise Fail(f"{a.id} is not yours")
    a.delete()


# --- court-ordered token actions ------------------------------------------------------------------

@op("token.force_transfer", signing=True, governed=realm_wide, parties=lambda a: {a.get("from", ""), a.get("to", "")})
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
    tx = outcome(reply)
    if tx is None:
        t.status = "failed"
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
    if outcome(reply) is None:
        raise Fail(f"{method} refused: {reply[:200]}")
    return {"account": args["account"], "frozen": method == "freeze_account"}
