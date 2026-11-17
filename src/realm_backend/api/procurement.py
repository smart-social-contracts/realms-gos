import hashlib

from core import chain
from core.db import Conflict, Fail, Forbidden, find, next_id, page, view
from ggg import Bid, BidScore, Contract, Department, External, Organization, Tender
from api.external import REALM_JOURNAL
from api.justice import people
from api.treasury import currency
from core.op import op
from core.pending import quoted, you_sign
from core.ratio import ONE, PERCENT

MAX_MARK = 10
MAX_LOTS = 64  # the lots of a tender (ggg I-PRC-5)
LISTED = ("id", "title", "kind", "procedure", "estimated_value", "status", "opens_at", "closes_at", "notices", "lots")


def seal(price, nonce):
    """The commitment a vendor publishes with its encrypted bid; the opening is revealed after closes_at."""
    return "sha256:" + hashlib.sha256(f"{int(price)}:{nonce}".encode()).hexdigest()


def requester(args):
    return find(Tender, args["tender"]).requester


def vendor_people(args):
    return people(f"Organization:{find(Bid, args['bid']).vendor.id}")


def representing(c, org):
    if c.user not in people(f"Organization:{org.id}"):
        raise Forbidden(f"you do not represent {org.id}")
    if org.status != "active":
        raise Conflict(f"{org.id} is {org.status}")


def shown(bid):
    data = {"id": bid.id, "vendor": bid.vendor.id, "submitted_at": bid.submitted_at, "sealed": bool(bid.sealed),
            "payload_hash": bid.payload_hash, "lot": bid.lot}
    if not bid.sealed:
        data.update(payload_ref=bid.payload_ref, price=bid.price, score=bid.score)
    return data


def total(bid, rubric):
    marks = {}
    for s in bid.scores:
        marks.setdefault(s.criterion, []).append(s.score)
    # A weight in hundred-millionths over PERCENT is its whole percent, so the score is out of 100.
    return round(sum(w * sum(marks[k]) / len(marks[k]) for k, w in rubric.items() if k in marks) / (MAX_MARK * PERCENT))


def moving(args, before, after):
    t = find(Tender, args["tender"])
    if t.status not in before:
        raise Conflict(f"{t.id} is {t.status}")
    t.status = after
    return t


def rubric_of(args):
    """The ``rubric`` of a ``tender_create`` call: criterion weights in hundred-millionths, positive, adding up to 100 %."""
    rubric = {k: int(w) for k, w in (args.get("rubric") or {}).items()}
    if not rubric or min(rubric.values()) <= 0 or sum(rubric.values()) != ONE:
        raise Fail(f"rubric weights must be positive and add up to {ONE} (100 %)")
    return rubric


def lots_of(args):
    """The ``lots`` of a ``tender_create`` call as stored, all open: each has an ``id`` named once in the tender, a
    ``title`` and an optional ``estimated_value``, a whole number; at most 64 (ggg I-PRC-5)."""
    lots = args.get("lots") or []
    if not isinstance(lots, list) or len(lots) > MAX_LOTS or any(
            not isinstance(lot, dict) or not isinstance(lot.get("id"), str) or not lot["id"]
            or not isinstance(lot.get("title"), str)
            or not (lot.get("estimated_value") is None or type(lot["estimated_value"]) is int and lot["estimated_value"] >= 0)
            for lot in lots):
        raise Fail(f"a tender has at most {MAX_LOTS} lots, each with an id, a title and, if it has one, a whole estimated_value")
    if len({lot["id"] for lot in lots}) != len(lots):
        raise Fail("each lot is named once in the tender")
    return [{"id": lot["id"], "title": lot["title"], "estimated_value": lot.get("estimated_value"), "status": "open",
             "winner": None, "contract": None} for lot in lots]


def tender_terms(args):
    rubric_of(args)
    lots_of(args)


@op("tender.manage", governed=lambda args: find(Department, args["requester"]), returns="tender", check=tender_terms)
def tender_create(c, args):
    """Draft a tender ``title`` for department ``requester``, with optional ``description``, the times it ``opens_at``
    and ``closes_at`` (seconds, closing after opening), and ``rubric``, criterion weights in hundred-millionths that
    are positive and add up to 100000000 (e.g. ``{"price": 60000000, "quality": 40000000}`` for 60 % and 40 %). The
    realm's own words say what is tendered (``kind``: works, supplies, services) and by which ``procedure``; the
    ``estimated_value`` is in the accounting currency. Optional ``lots`` divide it into parts awarded on their own, at
    most 64, each ``{id, title, estimated_value}`` with its ``id`` named once. Returns ``tender``."""
    rubric = rubric_of(args)
    opens, closes = int(args["opens_at"]), int(args["closes_at"])
    if closes <= opens:
        raise Fail("a tender closes after it opens")
    t = Tender(id=next_id(Tender, "T"), title=args["title"], description=args.get("description", ""),
               requester=find(Department, args["requester"]), kind=args.get("kind", ""),
               procedure=args.get("procedure", ""), estimated_value=args.get("estimated_value"), opens_at=opens,
               closes_at=closes, rubric=rubric, lots=lots_of(args))
    return {"tender": t.id}


@op("tender.manage", governed=requester)
def tender_open(c, args):
    """Open the draft ``tender``; it takes bids between its opening and closing times."""
    moving(args, ("draft",), "open")


@op("tender.manage", governed=requester)
def tender_close(c, args):
    """Close the open ``tender`` to bids now, bringing its closing time forward if it was later."""
    t = moving(args, ("open",), "closed")
    t.closes_at = min(t.closes_at, chain.now())


@op("tender.manage", governed=requester)
def tender_cancel(c, args):
    """Cancel ``tender`` while it is a draft or open."""
    moving(args, ("draft", "open"), "cancelled")


def lot_named(t, args):
    """The lot of tender ``t`` that ``args`` name, or None for a tender that has none: a tender with lots needs one, and
    one without refuses it (ggg I-PRC-5)."""
    name = args.get("lot")
    if not t.lots:
        if name:
            raise Fail(f"{t.id} has no lots")
        return None
    if not name:
        raise Fail(f"{t.id} has lots: name one")
    lot = next((x for x in t.lots if x["id"] == name), None)
    if lot is None:
        raise Fail(f"{t.id} has no lot {name}")
    return lot


@op("tender.manage", returns="tender")
def tender_notice(c, args):
    """Record that a notice of ``tender`` appeared in ``journal``: an outside body of kind journal, or ``realm`` for the
    realm's own, at the time ``at`` (default now, never in the future) with an optional ``ref`` to the issue.
    Legislation reads the journals of a tender's notices as ``published_in`` when it is awarded. Returns ``tender``."""
    t, now = find(Tender, args["tender"]), chain.now()
    if args["journal"] != REALM_JOURNAL and find(External, args["journal"]).kind != "journal":
        raise Fail(f"{args['journal']} is not a journal")
    at = int(args.get("at") or now)
    if at > now:
        raise Fail("a notice cannot have appeared in the future")
    t.notices = t.notices + [{"journal": args["journal"], "published_at": at, "ref": args.get("ref")}]
    return {"tender": t.id}


@op("realm.data_view", read=True, returns="tenders")
def tender_list(c, args):
    """List tenders, or only those whose ``status`` is draft, open, closed, evaluation, contracted or cancelled, from
    row ``from`` (default 1) over ``count`` rows (default 100, at most 500); a filtered page may hold fewer.
    Returns ``tenders`` and ``next``, the ``from`` of the next page (null at the end)."""
    rows, nxt = page(Tender, args, keep=lambda t: not args.get("status") or t.status == args["status"])
    return {"tenders": [{**view(t, *LISTED), "requester": t.requester.id} for t in rows], "next": nxt}


@op("realm.data_view", read=True)
def tender_get(c, args):
    """Show ``tender`` with its rubric and bids; a sealed bid shows only its vendor, time and commitment hash.
    Returns ``tender``."""
    t = find(Tender, args["tender"])
    data = view(t, *LISTED, "description", "rubric", "winner", "contract")
    return {"tender": {**data, "requester": t.requester.id, "bids": [shown(b) for b in t.bids]}}


@op("bid.submit", returns="bid")
def bid_submit(c, args):
    """For ``vendor``, an active organization you represent, bid on the open ``tender``: ``payload_ref`` (the encrypted
    bid) and ``payload_hash``, the ``sha256:`` of ``price:nonce``. One bid per vendor and tender, or per lot: a bid
    prices the one ``lot`` it names, which a tender with lots needs and one without refuses. Returns ``bid``."""
    t, org = find(Tender, args["tender"]), find(Organization, args["vendor"])
    representing(c, org)
    now = chain.now()
    if t.status != "open" or not t.opens_at <= now < t.closes_at:
        raise Conflict(f"{t.id} does not accept bids now")
    lot = lot_named(t, args)
    where = lot["id"] if lot else ""
    if any(b.vendor.id == org.id and b.lot == where for b in t.bids):
        raise Conflict(f"{org.id} already bid on {t.id}" + (f", lot {where}" if where else ""))
    if not args.get("payload_ref") or not str(args.get("payload_hash", "")).startswith("sha256:"):
        raise Fail("a bid is an encrypted reference with its sha256 commitment")
    b = Bid(id=next_id(Bid, "B"), tender=t, vendor=org, submitted_at=now, payload_ref=args["payload_ref"],
            payload_hash=args["payload_hash"], lot=where)
    return {"bid": b.id}


@op("bid.submit", returns="bid")
def bid_withdraw(c, args):
    """Withdraw ``bid``, made for a vendor you represent, while its tender still takes bids. Returns the ``bid``
    as it was."""
    b = find(Bid, args["bid"])
    representing(c, b.vendor)
    if b.tender.status != "open" or chain.now() >= b.tender.closes_at:
        raise Conflict(f"{b.tender.id} is closed")
    gone = view(b)
    b.delete()
    return {"bid": gone}


@op("tender.manage", governed=requester)
def bids_reveal(c, args):
    """Once ``tender`` has closed, unseal all its bids and start evaluation; ``openings`` maps a bid id to
    ``{price, nonce}``, which must match its hash and sets its price. Returns ``revealed``, the number of bids."""
    t = find(Tender, args["tender"])
    if t.status not in ("open", "closed") or chain.now() < t.closes_at:
        raise Conflict(f"bids on {t.id} stay sealed until {t.closes_at}")
    bids, openings = {b.id: b for b in t.bids}, args.get("openings", {})
    for key, o in openings.items():
        if key not in bids or seal(o["price"], o["nonce"]) != bids[key].payload_hash:
            raise Fail(f"the opening of {key} does not match its sealed bid")
    for b in bids.values():
        b.sealed = 0
        if b.id in openings:
            b.price = int(openings[b.id]["price"])
    t.status = "evaluation"
    return {"revealed": len(bids)}


def marks_of(bid, args):
    """The marks ``args`` give ``bid``, which must be on the criteria of its tender's rubric."""
    rubric = bid.tender.rubric
    marks = {k: int(v) for k, v in (args.get("scores") or {}).items()}
    if not marks or any(k not in rubric or not 0 <= v <= MAX_MARK for k, v in marks.items()):
        raise Fail(f"scores are 0-{MAX_MARK} on the rubric's criteria: {', '.join(rubric)}")
    return marks


def scoring(user, args, grant):
    b = find(Bid, args["bid"])
    marks = ", ".join(f"{quoted(k)} {v}" for k, v in sorted(marks_of(b, args).items()))
    return you_sign(f"Scores bid {b.id} of {quoted(b.vendor.id)} on tender {b.tender.id} {quoted(b.tender.title)} "
                    f"with {marks} (marks out of {MAX_MARK}); your earlier marks on it are replaced")


@op("bid.evaluate", signing=True, parties=vendor_people, returns="bid", summary=scoring, title="Score a bid")
def bid_score(c, args):
    """Score the revealed ``bid``: ``scores`` maps rubric criteria to marks from 0 to 10; scoring again replaces your
    marks. Returns ``bid`` and its weighted ``score`` out of 100, averaged over evaluators."""
    b = find(Bid, args["bid"])
    rubric = b.tender.rubric
    if b.tender.status != "evaluation" or b.sealed:
        raise Conflict(f"{b.id} is not open for evaluation")
    marks = marks_of(b, args)
    for k, v in marks.items():
        key = f"{b.id}:{c.user}:{k}"
        s = BidScore[key] or BidScore(id=key, bid=b, evaluator=c.user, criterion=k)
        s.score, s.scored_at = v, chain.now()
    b.score = total(b, rubric)
    return {"bid": b.id, "score": b.score}


def journals_of_notices(args, proposal=""):
    """The facts of a tender_award call that are not its arguments (GGG's derived facts): ``published_in``, the journals
    of the tender's notices."""
    journals = []
    for notice in find(Tender, args["tender"]).notices:
        if notice["journal"] not in journals:
            journals.append(notice["journal"])
    return {"published_in": journals}


def awarded(t, lot, args):
    """The bid that wins ``tender``, or its ``lot`` (None when it has none), as ``args`` ask, the best-scored one, and
    the justification given for the choice."""
    where = lot["id"] if lot else ""
    if t.status != "evaluation" or (lot and lot["status"] != "open"):
        raise Conflict(f"{t.id}{f', lot {where},' if lot else ''} is not open for an award")
    bids = [b for b in t.bids if not b.sealed and b.lot == where]
    if not bids:
        raise Conflict(f"{t.id}{f', lot {where},' if lot else ''} has no evaluated bids")
    best = max(bids, key=lambda b: b.score)
    b = find(Bid, args["bid"]) if args.get("bid") else best
    if b.id not in {x.id for x in bids}:
        raise Fail(f"{b.id} is not a revealed bid on {t.id}" + (f" for lot {where}" if lot else ""))
    why = args.get("justification", "")
    if b.score < best.score and not why:
        raise Fail(f"{best.id} scored {best.score}, more than {b.id}'s {b.score}: justify the deviation")
    return b, best, why


def awarding(user, args, grant):
    t = find(Tender, args["tender"])
    lot = lot_named(t, args)
    b, best, why = awarded(t, lot, args)
    part = f", lot {lot['id']} {quoted(lot['title'])}," if lot else ""
    return you_sign(f"Awards tender {t.id} {quoted(t.title)}{part} to bid {b.id} of {quoted(b.vendor.id)}, which scored "
                    f"{b.score} (the best scored {best.score}), and signs an active contract between "
                    f"{quoted('Department:' + t.requester.id)} and {quoted('Organization:' + b.vendor.id)}",
                    ("Contract terms", f"price {b.price}, terms kept at {quoted(b.payload_ref)}, "
                                       f"hash {quoted(b.payload_hash)}"),
                    ("Award justification", quoted(why) if why else "none"))


@op("tender.manage", signing=True, governed=requester, derived=journals_of_notices, returns="contract",
    summary=awarding, title="Award a tender")
def tender_award(c, args):
    """Award ``tender`` to ``bid`` (default the best-scored revealed bid), signing an active contract between the
    requester and the vendor, worth the bid's price in the realm's currency; a lower-scored bid needs a
    ``justification``. A tender with lots is awarded one ``lot`` at a
    time, to the best-scored bid of that lot (a named ``bid`` must be one of it), so its lots may go to different vendors,
    each with a contract of its own. Returns ``winner``, ``score``, ``contract``."""
    t = find(Tender, args["tender"])
    lot = lot_named(t, args)
    b, _, why = awarded(t, lot, args)
    title = f"{t.title}: {lot['title']}" if lot else t.title
    k = Contract(id=next_id(Contract, "C"), title=title, parties=[f"Department:{t.requester.id}",
                 f"Organization:{b.vendor.id}"], kind=t.kind, procedure=t.procedure, tender=t.id,
                 value=b.price or 0, symbol=currency(), terms_ref=b.payload_ref,
                 terms_hash=b.payload_hash, status="active", starts_at=chain.now(), lot=lot["id"] if lot else "")
    if lot:
        decided(t, lot, "awarded", winner=b.id, contract=k.id)
    else:
        t.winner, t.contract, t.status = b.id, k.id, "contracted"
    return {"winner": b.id, "score": b.score, "contract": k.id, "justification": why}


def decided(t, lot, status, **fields):
    """Record the outcome of ``lot``, and move the tender on once no lot is open (ggg I-PRC-5): it is cancelled when
    every lot is void, else contracted, since an award signs its contract."""
    t.lots = [{**x, "status": status, **fields} if x["id"] == lot["id"] else x for x in t.lots]
    if all(x["status"] != "open" for x in t.lots):
        t.status = "cancelled" if all(x["status"] == "void" for x in t.lots) else "contracted"


@op("tender.manage", governed=requester)
def tender_lot_void(c, args):
    """Leave ``lot`` of ``tender`` with no award, for lack of a bid or of an acceptable one (the optional ``reason`` is
    in the audit log). The tender must be in evaluation and the lot open. Once no lot is open the tender is cancelled if
    every lot is void."""
    t = find(Tender, args["tender"])
    lot = lot_named(t, args)
    if lot is None or t.status != "evaluation" or lot["status"] != "open":
        raise Conflict(f"{t.id} has no open lot {args.get('lot')} to leave void")
    decided(t, lot, "void")
