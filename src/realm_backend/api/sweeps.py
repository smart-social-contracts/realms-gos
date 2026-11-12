"""Money paid in a quarter reaches the capital's funds (ggg D36, I-FED-5 to I-FED-7).

The capital sets the sweep rules by vote (``treasury_configure``) and sends them to its quarters with ``quarter_sync``. A
quarter pushes: when its treasury epoch ends, and whenever ``quarter_sweep`` asks, it records one sweep per rule and epoch,
sends its ICRC-1 transfer, books it when the ledger gives a block and reports it to the capital, which books it once. The
transfer is fixed when the sweep is recorded (its memo and ``created_at_time`` included), so a ledger that has applied it
answers ``Duplicate`` with the block and the money moves once."""

from api import quarters, treasury
from core import candid, chain
from core.db import Conflict, Fail, each, find, next_id, page, view
from core.op import op
from ggg import Fund, Sweep, Token, Treasury

# A quarter sends a sweep's transfer again only until 23 hours after it was recorded, an hour inside the 24 hours in which
# an ICRC-1 ledger recognises a repeat (ggg I-FED-6).
RESEND_LIMIT = 23 * 3600
CATEGORY = "sweep"  # the category of a sweep in the books: money moved between a quarter and its capital, neither revenue nor spending
RULE = ("fund", "symbol", "into", "keep")
SWEEP = ("id", "quarter", "fund", "symbol", "into", "to", "epoch", "amount", "fee", "created_at", "block", "status", "reason",
         "reported_at")
# Whether this heap has armed the end-of-epoch timer: one chain of them, armed again after an upgrade (resume).
_timer = {"armed": False}


# --- the capital's rules --------------------------------------------------------------------------

def refuse_bad_rules(rules):
    """Refuse the ``sweep`` of a treasury_configure call that is not a capital's list of rules: each is a ``fund`` and
    ``symbol`` of a quarter, the capital fund ``into`` that is a fund of its own, in a token it registered, and what the
    quarter ``keep``s, and a quarter fund and token has one."""
    if not quarters.is_capital():
        raise Fail("only a capital sets sweep rules")
    if not isinstance(rules, list) or not all(isinstance(r, dict) and set(r) == set(RULE) for r in rules):
        raise Fail(f"sweep is a list of rules, each with {', '.join(RULE)}")
    seen = set()
    for rule in rules:
        if type(rule["keep"]) is not int or rule["keep"] < 0:
            raise Fail("what a quarter keeps is a whole number, not negative")
        find(Fund, rule["into"])
        find(Token, rule["symbol"])
        if rule_key(rule) in seen:
            raise Fail(f"two sweep rules for {rule['fund']} in {rule['symbol']}")
        seen.add(rule_key(rule))


def rule_key(rule):
    return f"{rule['fund']}:{rule['symbol']}"


def rules_for_quarters():
    """What the capital sends each quarter with quarter_sync: its sweep rules and the account that receives sweeps."""
    treasury_row = Treasury["1"]
    rules = treasury_row.config.get("sweep", []) if treasury_row else []
    return {"to": {"owner": chain.self_id(), "subaccount": None}, "rules": rules}


# --- a quarter's rules and its timer --------------------------------------------------------------

def held():
    """The rules and the capital's account this quarter last received, None before it heard from its capital."""
    return (quarters.realm().sync_state or {}).get("sweep")


def keep(config):
    """On a quarter, keep the rules the capital sent: the last ones it received stand until it hears again."""
    r = quarters.realm()
    r.sync_state = {**(r.sync_state or {}), "sweep": config}
    resume()


def epoch_length():
    """The seconds of a treasury epoch, 0 for an epoch of no length (every moment is its own)."""
    row = Treasury["1"]
    epoch = (row.config if row else {}).get("epoch", "monthly")
    length = treasury.EPOCHS.get(epoch, epoch)
    return length if type(length) is int and length > 0 else 0


def epoch_start(t):
    length = epoch_length()
    return t - t % length if length else t


def resume():
    """On a quarter with sweep rules, arm the timer that sweeps when its treasury epoch ends, unless it is armed already.
    Timers do not survive an upgrade, so this runs again when the realm starts."""
    length = epoch_length()
    if quarters.federated() and (held() or {}).get("rules") and length and not _timer["armed"]:
        _timer["armed"] = True
        chain.set_timer(length - chain.now() % length, end_of_epoch)


def end_of_epoch():
    """The timer: sweep, and arm the next one first, so that a sweep that fails is tried again at the end of the next
    epoch. The error is kept in ``sync_state`` until a sweep succeeds."""
    _timer["armed"] = False
    resume()
    try:
        yield from sweep_now()
        error = ""
    except Exception as e:  # noqa: BLE001  a capital or a ledger that is down: the next epoch tries again
        error = str(e)[:300]
    r = quarters.realm()
    r.sync_state = {**(r.sync_state or {}), "sweep_error": error}


# --- sweeping -------------------------------------------------------------------------------------

@op("treasury.manage", returns="sweeps")
def quarter_sweep(c, args):
    """On a quarter, sweep now, rather than when its treasury epoch ends: for each sweep rule of the capital (only that of
    ``fund`` when it is given), report the sweeps the capital has not booked, send again one whose outcome is unknown, or
    record this epoch's and send it. The quarter asks its capital for the rules first, and keeps the last ones it had when
    the capital does not answer. Returns the ``sweeps`` it touched."""
    if not quarters.federated():
        raise Conflict("only a quarter sweeps")
    touched = yield from sweep_now(args.get("fund"))
    return {"sweeps": [s.id for s in touched]}


def sweep_now(fund=None):
    """Sweep every rule, or the one of ``fund`` (ggg I-FED-5). A generator; returns the sweeps it touched."""
    try:
        yield from quarters.sync()
    except Exception:  # noqa: BLE001, S110  the capital cannot be reached: the rules it last sent stand
        pass
    config = held()
    touched = []
    for rule in (config or {}).get("rules", []):
        if fund in (None, rule["fund"]):
            touched += yield from sweep_rule(config["to"], rule)
    return touched


def of_rule(rule):
    return list(each(Sweep, "rule", rule_key(rule)))


def blocking(s):
    """A sweep that stops the next one of its rule: its outcome is not known, or a person must settle it (I-FED-5)."""
    return s.status in ("sending", "needs_check")


def sweep_rule(to, rule):
    """One rule's turn: report the sweeps the capital has not booked, then send again the one whose outcome is not known, or
    record this epoch's and send it. A generator; returns the sweeps it touched."""
    touched = []
    for s in of_rule(rule):
        if s.status == "sent":
            touched.append((yield from report(s)))
    open_sweeps = [s for s in of_rule(rule) if blocking(s)]
    due = open_sweeps[0] if open_sweeps else record(to, rule)
    if due is not None:
        touched.append((yield from attempt(due)))
    return touched


def record(to, rule):
    """This epoch's sweep of ``rule``, recorded before any money moves: what the fund holds above what it keeps and the
    ledger's fee, when that is more than nothing. A rule has at most one a treasury epoch (I-FED-5)."""
    epoch = epoch_start(chain.now())
    fund, token = Fund[rule["fund"]], Token[rule["symbol"]]
    if fund is None or token is None or any(s.epoch == epoch for s in of_rule(rule)):
        return None
    amount = treasury.balance(fund, token.id) - rule["keep"] - token.fee
    if amount <= 0:
        return None
    return Sweep(id=next_id(Sweep, "SW"), quarter=chain.self_id(), fund=fund.id, symbol=token.id, into=rule["into"],
                 rule=rule_key(rule), to=to, epoch=epoch, amount=amount, fee=token.fee, created_at=chain.now())


def attempt(s):
    """Send the ledger transfer of a sweep that is ``sending``, the same one every time (I-FED-5), and only until
    RESEND_LIMIT after it was recorded (I-FED-6). The answer of the ledger decides the sweep."""
    if s.status != "sending":
        return s
    if chain.now() >= s.created_at + RESEND_LIMIT:
        s.status = "needs_check"
        return s
    token = Token[s.symbol]
    sub = bytes.fromhex(s.to["subaccount"]) if s.to.get("subaccount") else None
    arg = treasury.icrc1_arg(treasury.account(s.to["owner"], sub), s.amount, s.id.encode(), s.fee, s.created_at)
    try:
        reply = yield from chain.call(token.ledger, "icrc1_transfer", arg)
    except Exception:  # noqa: BLE001  no answer: the outcome is unknown, and the same transfer is sent again at the next sweep
        return s
    if s.status != "sending":  # another call settled it while the ledger answered
        return s
    status, detail = answer(reply)
    if status == "sent":
        mark_sent(s, detail)
        return (yield from report(s))
    if status == "needs_check":
        s.status = status
    elif status == "failed":
        s.status, s.reason = status, detail
    return s


def answer(reply):
    """How the ledger answered the transfer of a sweep: ``("sent", block)`` when it gave a block or says it applied the
    transfer already (``Duplicate``), ``("needs_check", None)`` for ``TooOld``, which it will not recognise again,
    ``("failed", reason)`` for any other refusal, and ``("unknown", None)`` for an answer that says nothing."""
    try:
        (result,) = candid.Reader(reply).args()
    except Exception:  # noqa: BLE001  a hostile reply can also overflow or recurse; whatever goes wrong, it says nothing
        return "unknown", None
    ok, err = named(result).get(candid.idl_hash("Ok")), named(named(result).get(candid.idl_hash("Err")))
    if treasury.is_block_index(ok):
        return "sent", ok
    if candid.idl_hash("Duplicate") in err:
        block = named(err[candid.idl_hash("Duplicate")]).get(candid.idl_hash("duplicate_of"))
        return ("sent", block) if treasury.is_block_index(block) else ("unknown", None)
    if candid.idl_hash("TooOld") in err:
        return "needs_check", None
    return ("failed", reply[:200]) if err else ("unknown", None)


def named(value):
    """The fields of a parsed record or variant by their Candid number: a decoder that does not know the names prints a
    number for each."""
    if not isinstance(value, dict):
        return {}
    return {candid.idl_hash(k) if isinstance(k, str) else k: v for k, v in value.items()}


def mark_sent(s, block):
    """The ledger gave a block: the quarter books the sweep, ``amount`` out of its fund in the category ``sweep`` and the
    ledger's ``fee`` in ``ledger_fee`` (ggg I-FED-7). The money has moved, so the entries are booked even where the period
    that covers now has closed."""
    fund = Fund[s.fund]
    lines = [("asset", "cash", 0, s.amount + s.fee, fund), ("equity", CATEGORY, s.amount, 0, fund),
             ("expense", treasury.FEE, s.fee, 0, fund)]
    treasury.post(treasury.booking_period(), lines, s.symbol, f"sweep {s.id} (block {block})", sweep=s)
    s.status, s.block = "sent", str(block)


def report(s):
    """Tell the capital about a ``sent`` sweep: it is ``booked`` once the capital says it booked it. A capital that does
    not answer has the money on its account already and only its books wait: the quarter reports again at its next
    sweep."""
    try:
        reply = yield from quarters.ask(quarters.capital(), "quarter_sweep_report",
                                        {**view(s, *SWEEP), "block": int(s.block)})
    except Exception:  # noqa: BLE001  the capital cannot be reached
        return s
    if reply.get("ok") and s.status == "sent":
        s.status, s.reported_at = "booked", chain.now()
    return s


@op("quarter.sync", returns="sweep")
def quarter_sweep_report(c, args):
    """On the capital, from a registered quarter: book its sweep into the capital fund ``into``, once. The sweep is as
    the quarter keeps it: ``id``, ``fund`` and ``symbol`` it left, ``to`` the capital's account, ``epoch``, ``amount``,
    ``fee``, ``created_at`` and, since it is ``sent``, the ``block`` the ledger gave (the ``status`` says so). A second
    report of a booked sweep returns the capital's record; one that differs from it is a Conflict. The capital books on the
    quarter's word: it does not look the block up on the ledger."""
    quarter = quarters.registered(c.by)
    if args.get("status") != "sent" or args.get("block") is None:
        raise Fail("only a sent sweep, with its block, is reported")
    key = f"{quarter.id}/{args['id']}"
    moved = {"fund": args["fund"], "symbol": args["symbol"], "into": args["into"], "to": account_of(args["to"]),
             "epoch": args["epoch"], "amount": args["amount"], "fee": args["fee"], "created_at": args["created_at"],
             "block": str(args["block"])}
    earlier = Sweep[key]
    if earlier is not None:
        if {k: getattr(earlier, k) for k in moved} != moved:
            raise Conflict(f"the sweep {key} was booked with other figures")
        return {"sweep": earlier.id}
    into = find(Fund, args["into"])
    booked = Sweep(id=key, quarter=quarter.id, rule=f"{args['fund']}:{args['symbol']}", status="booked",
                   reported_at=chain.now(), **moved)
    lines = [("asset", "cash", booked.amount, 0, into), ("equity", CATEGORY, 0, booked.amount, into)]
    treasury.post(treasury.booking_period(), lines, booked.symbol, f"sweep {booked.id} (block {booked.block})",
                  sweep=booked)
    return {"sweep": booked.id}


def account_of(account):
    return {"owner": str(account["owner"]), "subaccount": account.get("subaccount")}


# --- settling and reading -------------------------------------------------------------------------

def needing_check(args):
    s = find(Sweep, args["sweep"])
    if s.status != "needs_check":
        raise Conflict(f"{s.id} is {s.status}; only a sweep that needs checking is resolved")
    return s


def swept_fund_department(args):
    return treasury.owner_of(find(Fund, needing_check(args).fund))


@op("treasury.manage", governed=swept_fund_department, check=needing_check)
def sweep_resolve(c, args):
    """Settle the ``sweep`` that needs checking, from what the ledger shows: the ``block`` of its transfer makes it
    ``sent``, the quarter books it and reports it at its next sweep; without one it is ``failed``, since nothing moved.
    Governed by the owner of the swept fund."""
    s = needing_check(args)
    if args.get("block") is None:
        s.status, s.reason = "failed", "resolved: the ledger holds no such transfer"
    else:
        mark_sent(s, args["block"])
    return {"sweep": s.id, "status": s.status}


@op("treasury.view", read=True, returns="sweeps")
def sweep_list(c, args):
    """One page of sweeps (``from``, ``count``), optionally only a ``quarter``'s or with ``status``: a quarter's own, and
    on the capital the ones its quarters reported; ``next`` continues it. A page can hold fewer matches than ``count``:
    follow ``next``."""
    def wanted(s):
        return args.get("quarter") in (None, s.quarter) and args.get("status") in (None, s.status)
    rows, nxt = page(Sweep, args, wanted)
    return {"sweeps": [view(s, *SWEEP) for s in rows], "next": nxt}
