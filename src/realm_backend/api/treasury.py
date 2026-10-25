import hashlib
import json

from core import candid
from core import chain
from core.db import Fail, each, find, next_id, page, view
from ggg import (AllocationRule, Budget, Contract, Department, FinancialReport, FiscalPeriod, Fund, Invoice,
                 LedgerEntry, NFTToken, Organization, PaymentAccount, Proposal, Realm, TaxDeclaration, Token,
                 Transfer, Treasury)
from core.op import op
from api.proposals import realm_wide, root

CONFIG = ("epoch", "anchor_month", "source_fund", "auto_allocate")


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
    return next((t.id for t in each(Token)), "")


def owner_of(fund):
    return fund.department or root()


def same(a, b):
    return (a.id if a else None) == (b.id if b else None)


def period():
    """The fiscal period covering now; refuses if it is closed (I-FIN-5)."""
    now = chain.now()
    for p in each(FiscalPeriod):
        if p.starts_at <= now and (not p.ends_at or now < p.ends_at):
            if p.status != "open":
                raise Fail(f"fiscal period {p.id} is {p.status}")
            return p
    return None


def post(p, lines, symbol, description="", **refs):
    """One balanced transaction: lines are (kind, category, debit, credit, fund)."""
    tx = f"T-{LedgerEntry.max_id() + 1}"
    for kind, category, debit, credit, fund in lines:
        LedgerEntry(id=f"LE-{LedgerEntry.max_id() + 1}", transaction=tx, kind=kind, category=category,
                    debit=debit, credit=credit, symbol=symbol, fund=fund, period=p, at=chain.now(),
                    description=description, **refs)
        if kind == "expense" and fund is not None:
            for b in fund.budgets:
                if b.status == "adopted" and b.category == category and same(b.period, p):
                    b.actual += debit - credit
    return tx


def balance(fund, symbol):
    return sum(e.debit - e.credit for e in fund.ledger_entries if e.kind == "asset" and e.symbol == symbol)


def adopted_rule(source):
    return next((r for r in each(AllocationRule) if r.source_fund == source and r.status == "adopted"), None)


def replaying(c, name):
    """The proposal whose replay is running this call, if any."""
    if c.origin != "replay":
        return ""
    return next((p.id for p in each(Proposal, "status", "executed")
                 if p.result is None and (p.action or {}).get("op") == name), "")


# --- transfers ------------------------------------------------------------------------------------

def address(to, symbol):
    for owner in (to, f"User:{to}", f"Organization:{to}"):
        for a in each(PaymentAccount, "owner", owner):
            if a.symbol == symbol:
                return a.address
    return to


def reserve(p, fund, to, symbol, amount, category, description="", **refs):
    t = Transfer(id=next_id(Transfer, "TR"), sender=f"Fund:{fund.id}", to=to, symbol=symbol, amount=amount,
                 status="pending", at=chain.now())
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
    reply = yield from chain.call(token.ledger, "icrc1_transfer", arg)
    tx = outcome(reply)
    if tx is None:
        reverse(t, period(), reply[:200])
        return {"transfer": t.id, "status": t.status, "error": reply[:200]}
    t.status, t.ledger_tx = "completed", tx
    return {"transfer": t.id, "status": t.status, "ledger_tx": tx}


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
    rule = adopted_rule(source.id) if config.get("auto_allocate") else None
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
    inv = new_invoice(args["payer"], positive(args), args.get("symbol") or currency(), args.get("due_at", 0),
                      args.get("reason", ""))
    return {"invoice": inv.id, "pay_to": pay_to(inv)}


@op("self.invoice_refresh", delegable=True)
def invoice_check(c, args):
    inv = find(Invoice, args["invoice"])
    if not owns(inv, c.user):
        raise Fail(f"{inv.id} is not yours")
    period()
    return {"invoice": inv.id, "status": (yield from refresh(inv))}


@op("invoice.refresh")
def invoice_refresh(c, args):
    period()
    if args.get("invoice"):
        rows = [find(Invoice, args["invoice"])]
    else:
        rows = [i for i in each(Invoice) if i.status in ("pending", "overdue")]
    statuses = {}
    for inv in rows:
        statuses[inv.id] = yield from refresh(inv)
    return {"checked": len(rows), "paid": sum(s == "paid" for s in statuses.values()), "invoices": statuses}


@op("self.data_view", read=True, delegable=True)
def my_invoices(c, args):
    return {"invoices": [show(i) for i in each(Invoice, "payer", f"User:{c.user}")]}


# --- treasury -------------------------------------------------------------------------------------

@op("treasury.view", read=True)
def treasury_view(c, args):
    funds = {f.id: {} for f in each(Fund)}
    for e in each(LedgerEntry):
        if e.kind == "asset" and e.fund is not None:
            funds[e.fund.id][e.symbol] = funds[e.fund.id].get(e.symbol, 0) + e.debit - e.credit
    t = Treasury["1"]
    return {"funds": funds, "config": t.config if t else {},
            "tokens": [view(k) for k in each(Token)], "nft_tokens": [view(k) for k in each(NFTToken)]}


@op("treasury.view", read=True)
def ledger_view(c, args):
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
    if args.get("report"):
        return {"report": view(find(FinancialReport, args["report"]))}
    return {"reports": [view(r, "id", "kind", "as_of", "issued_by", "supersedes") for r in each(FinancialReport)]}


@op("treasury.manage", governed=realm_wide)
def treasury_configure(c, args):
    t = Treasury["1"] or Treasury(id="1")
    unknown = set(args["config"]) - set(CONFIG)
    if unknown:
        raise Fail(f"unknown treasury settings: {', '.join(sorted(unknown))}")
    if "source_fund" in args["config"]:
        find(Fund, args["config"]["source_fund"])
    t.config = {**(t.config or {}), **args["config"]}
    return {"config": t.config}


@op("treasury.manage", governed=realm_wide)
def token_register(c, args):
    t = Token[args["symbol"]] or Token(id=args["symbol"])
    t.ledger, t.indexer = args["ledger"], args.get("indexer", t.indexer)
    t.decimals, t.fee = int(args.get("decimals", t.decimals)), int(args.get("fee", t.fee))
    return {"token": t.id}


@op("treasury.manage", governed=realm_wide)
def nft_token_register(c, args):
    t = NFTToken[args["symbol"]] or NFTToken(id=args["symbol"])
    t.canister = args["canister"]
    for k in ("name", "description", "required_license"):
        setattr(t, k, args.get(k, getattr(t, k)))
    t.transferable = bool(args.get("transferable", t.transferable))
    t.supply_cap = int(args.get("supply_cap", t.supply_cap))
    return {"nft_token": t.id}


# --- funds, periods, budgets, allocation ----------------------------------------------------------

@op("org.manage_budget", governed=realm_wide)
def fund_create(c, args):
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
    starts, ends = int(args["starts_at"]), int(args["ends_at"])
    if FiscalPeriod[args["period"]] or ends <= starts:
        raise Fail(f"period {args['period']} exists or ends before it starts")
    for p in each(FiscalPeriod):
        if p.starts_at < ends and starts < (p.ends_at or ends + 1):
            raise Fail(f"overlaps fiscal period {p.id}")
    p = FiscalPeriod(id=args["period"], name=args.get("name", args["period"]), starts_at=starts, ends_at=ends)
    return {"period": p.id}


@op("org.manage_budget", governed=realm_wide)
def period_close(c, args):
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
    for old in b.fund.budgets:
        if old.id != b.id and old.status == "adopted" and old.category == b.category and same(old.period, b.period):
            old.status = status
            b.actual += old.actual


@op("org.manage_budget", governed=fund_department)
def budget_draft(c, args):
    fund, p = find(Fund, args["fund"]), find(FiscalPeriod, args["period"])
    if p.status != "open":
        raise Fail(f"{p.id} is {p.status}")
    b = Budget(id=next_id(Budget, "B"), fund=fund, period=p, category=args["category"],
               kind=args.get("kind", "expense"), planned=int(args["planned"]))
    return {"budget": b.id}


@op("org.manage_budget", governed=budget_department)
def budget_propose(c, args):
    b = find(Budget, args["budget"])
    if b.status != "draft" or b.period.status != "open":
        raise Fail(f"{b.id} is {b.status}; only a draft for an open period can be adopted")
    supersede(b, "amended")
    b.status, b.proposal = "adopted", replaying(c, "budget_propose")
    return {"budget": b.id, "status": b.status}


@op("org.manage_budget", governed=budget_department)
def budget_amend(c, args):
    old = find(Budget, args["budget"])
    if old.status != "adopted":
        raise Fail(f"{old.id} is {old.status}; only an adopted budget can be amended")
    b = Budget(id=next_id(Budget, "B"), fund=old.fund, period=old.period, category=old.category, kind=old.kind,
               planned=int(args["planned"]), proposal=replaying(c, "budget_amend"))
    supersede(b, "amended")
    b.status = "adopted"
    return {"budget": b.id, "amends": old.id}


def rule_department(args):
    rule = AllocationRule[args["rule"]] if "rule" in args else None
    return owner_of(find(Fund, rule.source_fund if rule else args["source_fund"]))


@op("org.manage_budget", governed=rule_department)
def allocation_rule_draft(c, args):
    source, rules = find(Fund, args["source_fund"]), {k: int(v) for k, v in args["rules"].items()}
    for code in rules:
        find(Fund, code)
    if any(v < 0 for v in rules.values()) or sum(rules.values()) > 100:
        raise Fail("shares must be non-negative percentages adding up to at most 100")
    r = AllocationRule(id=next_id(AllocationRule, "AR"), source_fund=source.id, rules=rules)
    return {"rule": r.id}


@op("org.manage_budget", governed=rule_department)
def allocation_rule_propose(c, args):
    r = find(AllocationRule, args["rule"])
    if r.status != "draft":
        raise Fail(f"{r.id} is {r.status}; only a draft can be adopted")
    old = adopted_rule(r.source_fund)
    if old:
        old.status = "superseded"
    r.status, r.proposal = "adopted", replaying(c, "allocation_rule_propose")
    return {"rule": r.id, "status": r.status}


# --- payroll and reports --------------------------------------------------------------------------

@op("org.manage_budget", governed=lambda a: find(Department, a["department"]))
def payroll_run(c, args):
    dept, label = find(Department, args["department"]), args["period"]
    fund, symbol = dept.fund, args.get("symbol") or currency()
    if fund is None:
        raise Fail(f"{dept.id} has no fund")
    note = f"payroll {label} {dept.id}"
    if any(e.description.startswith(note) for e in fund.ledger_entries):
        raise Fail(f"{dept.id} payroll for {label} already ran")
    seats = [(a.user, pos.salary) for pos in dept.positions if pos.salary > 0
             for a in pos.appointments if a.status == "active" and not a.agent]
    total = sum(s for _, s in seats)
    need = total + len(seats) * find(Token, symbol).fee
    if balance(fund, symbol) < need:
        raise Fail(f"fund {fund.id} holds {balance(fund, symbol)} {symbol}, payroll needs {need}")
    p = period()
    batch = [reserve(p, fund, user, symbol, salary, "payroll", f"{note}: {user}", user=user) for user, salary in seats]
    results = []
    for t in batch:
        results.append((yield from send(t)))
    return {"transfers": results, "seats": len(batch), "total": total}


def statements(rows):
    totals, funds = {}, {}
    for e in rows:
        key = f"{e.kind}:{e.category}:{e.symbol}"
        d, cr = totals.get(key, (0, 0))
        totals[key] = (d + e.debit, cr + e.credit)
        if e.kind == "asset" and e.fund is not None:
            k = f"{e.fund.id}:{e.symbol}"
            funds[k] = funds.get(k, 0) + e.debit - e.credit
    return {"accounts": {k: {"debit": d, "credit": cr} for k, (d, cr) in sorted(totals.items())},
            "fund_balances": dict(sorted(funds.items())),
            "debit": sum(e.debit for e in rows), "credit": sum(e.credit for e in rows)}


@op("treasury.manage", signing=True)
def report_issue(c, args):
    p, kind = find(FiscalPeriod, args["period"]), args.get("kind", "period_close")
    rows = sorted(p.ledger_entries, key=lambda e: int(e.id.split("-")[1]))
    source = json.dumps([[e.id, e.transaction, e.debit, e.credit, e.symbol] for e in rows]).encode()
    previous = [r for r in p.reports if r.kind == kind]
    r = FinancialReport(id=next_id(FinancialReport, "FR"), kind=kind, period=p, as_of=chain.now(),
                        statements=statements(rows), source_hash="sha256:" + hashlib.sha256(source).hexdigest(),
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
    a = PaymentAccount(id=next_id(PaymentAccount, "PA"), owner=account_owner(c, args), network=args["network"],
                       address=args["address"], symbol=args.get("symbol", ""), label=args.get("label", ""))
    return {"account": a.id}


@op("self.data_manage", delegable=True)
def payment_account_remove(c, args):
    a = find(PaymentAccount, args["account"])
    if a.owner != account_owner(c, args):
        raise Fail(f"{a.id} is not yours")
    a.delete()


# --- court-ordered token actions ------------------------------------------------------------------

@op("token.force_transfer", signing=True, governed=realm_wide, parties=lambda a: {a.get("from", ""), a.get("to", "")})
def token_force_transfer(c, args):
    token, amount = find(Token, args["symbol"]), positive(args)
    p = period()
    t = Transfer(id=next_id(Transfer, "TR"), sender=args["from"], to=args["to"], symbol=token.id, amount=amount,
                 status="pending", at=chain.now())
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
    token = find(Token, args["symbol"])
    method = "freeze_account" if args.get("frozen", True) else "unfreeze_account"
    reply = yield from chain.call(token.ledger, method,
                                  f'(record {{ account = {account(args["account"])}; '
                                  f'reason = opt {text(args.get("reason", "")[:64])} }})')
    if outcome(reply) is None:
        raise Fail(f"{method} refused: {reply[:200]}")
    return {"account": args["account"], "frozen": method == "freeze_account"}
