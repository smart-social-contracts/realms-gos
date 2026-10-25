import hashlib

from core import chain
from core.db import Fail, find, next_id, page, view
from ggg import Bid, BidScore, Contract, Department, Organization, Tender
from api.justice import people
from core.op import op

MAX_MARK = 10


def seal(price, nonce):
    """The commitment a vendor publishes with its encrypted bid; the opening is revealed after closes_at."""
    return "sha256:" + hashlib.sha256(f"{int(price)}:{nonce}".encode()).hexdigest()


def requester(args):
    return find(Tender, args["tender"]).requester


def vendor_people(args):
    return people(f"Organization:{find(Bid, args['bid']).vendor.id}")


def representing(c, org):
    if c.user not in people(f"Organization:{org.id}"):
        raise Fail(f"you do not represent {org.id}")
    if org.status != "active":
        raise Fail(f"{org.id} is {org.status}")


def shown(bid):
    data = {"id": bid.id, "vendor": bid.vendor.id, "submitted_at": bid.submitted_at, "sealed": bool(bid.sealed),
            "payload_hash": bid.payload_hash}
    if not bid.sealed:
        data.update(payload_ref=bid.payload_ref, price=bid.price, score=bid.score)
    return data


def total(bid, rubric):
    marks = {}
    for s in bid.scores:
        marks.setdefault(s.criterion, []).append(s.score)
    return round(sum(w * sum(marks[k]) / len(marks[k]) for k, w in rubric.items() if k in marks) / MAX_MARK)


def moving(args, before, after):
    t = find(Tender, args["tender"])
    if t.status not in before:
        raise Fail(f"{t.id} is {t.status}")
    t.status = after
    return t


@op("tender.manage", governed=lambda args: find(Department, args["requester"]))
def tender_create(c, args):
    """Draft a tender ``title`` for department ``requester``, with optional ``description``, the times it ``opens_at``
    and ``closes_at`` (seconds, closing after opening), and ``rubric``, criterion weights that are positive and add up
    to 100 (e.g. ``{"price": 60, "quality": 40}``). Returns ``tender``."""
    rubric = {k: int(w) for k, w in (args.get("rubric") or {}).items()}
    if not rubric or min(rubric.values()) <= 0 or sum(rubric.values()) != 100:
        raise Fail("rubric weights must be positive and add up to 100")
    opens, closes = int(args["opens_at"]), int(args["closes_at"])
    if closes <= opens:
        raise Fail("a tender closes after it opens")
    t = Tender(id=next_id(Tender, "T"), title=args["title"], description=args.get("description", ""),
               requester=find(Department, args["requester"]), opens_at=opens, closes_at=closes, rubric=rubric)
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


@op("realm.data_view", read=True)
def tender_list(c, args):
    """List tenders, or only those whose ``status`` is draft, open, closed, evaluation, contracted or cancelled, from
    row ``from`` (default 1) over ``count`` rows (default 100, at most 500); a filtered page may hold fewer.
    Returns ``tenders`` and ``next``, the ``from`` of the next page (null at the end)."""
    rows, nxt = page(Tender, args, keep=lambda t: not args.get("status") or t.status == args["status"])
    return {"tenders": [{**view(t, "id", "title", "status", "opens_at", "closes_at"), "requester": t.requester.id}
                        for t in rows], "next": nxt}


@op("realm.data_view", read=True)
def tender_get(c, args):
    """Show ``tender`` with its rubric and bids; a sealed bid shows only its vendor, time and commitment hash.
    Returns ``tender``."""
    t = find(Tender, args["tender"])
    data = view(t, "id", "title", "description", "status", "opens_at", "closes_at", "rubric", "winner", "contract")
    return {"tender": {**data, "requester": t.requester.id, "bids": [shown(b) for b in t.bids]}}


@op("bid.submit", delegable=True)
def bid_submit(c, args):
    """For ``vendor``, an active organization you represent, bid on the open ``tender``: ``payload_ref`` (the encrypted
    bid) and ``payload_hash``, the ``sha256:`` of ``price:nonce``. One bid per vendor and tender. Returns ``bid``."""
    t, org = find(Tender, args["tender"]), find(Organization, args["vendor"])
    representing(c, org)
    now = chain.now()
    if t.status != "open" or not t.opens_at <= now < t.closes_at:
        raise Fail(f"{t.id} does not accept bids now")
    if any(b.vendor.id == org.id for b in t.bids):
        raise Fail(f"{org.id} already bid on {t.id}")
    if not args.get("payload_ref") or not str(args.get("payload_hash", "")).startswith("sha256:"):
        raise Fail("a bid is an encrypted reference with its sha256 commitment")
    b = Bid(id=next_id(Bid, "B"), tender=t, vendor=org, submitted_at=now, payload_ref=args["payload_ref"],
            payload_hash=args["payload_hash"])
    return {"bid": b.id}


@op("bid.submit", delegable=True)
def bid_withdraw(c, args):
    """Withdraw ``bid``, made for a vendor you represent, while its tender still takes bids."""
    b = find(Bid, args["bid"])
    representing(c, b.vendor)
    if b.tender.status != "open" or chain.now() >= b.tender.closes_at:
        raise Fail(f"{b.tender.id} is closed")
    b.delete()


@op("tender.manage", governed=requester)
def bids_reveal(c, args):
    """Once ``tender`` has closed, unseal all its bids and start evaluation; ``openings`` maps a bid id to
    ``{price, nonce}``, which must match its hash and sets its price. Returns ``revealed``, the number of bids."""
    t = find(Tender, args["tender"])
    if t.status not in ("open", "closed") or chain.now() < t.closes_at:
        raise Fail(f"bids on {t.id} stay sealed until {t.closes_at}")
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


@op("bid.evaluate", signing=True, parties=vendor_people)
def bid_score(c, args):
    """Score the revealed ``bid``: ``scores`` maps rubric criteria to marks from 0 to 10; scoring again replaces your
    marks. Returns ``bid`` and its weighted ``score`` out of 100, averaged over evaluators."""
    b = find(Bid, args["bid"])
    rubric = b.tender.rubric
    if b.tender.status != "evaluation" or b.sealed:
        raise Fail(f"{b.id} is not open for evaluation")
    marks = {k: int(v) for k, v in (args.get("scores") or {}).items()}
    if not marks or any(k not in rubric or not 0 <= v <= MAX_MARK for k, v in marks.items()):
        raise Fail(f"scores are 0-{MAX_MARK} on the rubric's criteria: {', '.join(rubric)}")
    for k, v in marks.items():
        key = f"{b.id}:{c.user}:{k}"
        s = BidScore[key] or BidScore(id=key, bid=b, evaluator=c.user, criterion=k)
        s.score, s.scored_at = v, chain.now()
    b.score = total(b, rubric)
    return {"bid": b.id, "score": b.score}


@op("tender.manage", signing=True, governed=requester)
def tender_award(c, args):
    """Award ``tender`` to ``bid`` (default the best-scored revealed bid), signing an active contract between the
    requester and the vendor; a lower-scored bid needs a ``justification``. Returns ``winner``, ``score``,
    ``contract``."""
    t = find(Tender, args["tender"])
    bids = [b for b in t.bids if not b.sealed]
    if t.status != "evaluation" or not bids:
        raise Fail(f"{t.id} has no evaluated bids")
    best = max(bids, key=lambda b: b.score)
    b = find(Bid, args["bid"]) if args.get("bid") else best
    if b.id not in {x.id for x in bids}:
        raise Fail(f"{b.id} is not a revealed bid on {t.id}")
    why = args.get("justification", "")
    if b.score < best.score and not why:
        raise Fail(f"{best.id} scored {best.score}, more than {b.id}'s {b.score}: justify the deviation")
    k = Contract(id=next_id(Contract, "C"), title=t.title, parties=[f"Department:{t.requester.id}",
                 f"Organization:{b.vendor.id}"], terms_ref=b.payload_ref, terms_hash=b.payload_hash,
                 status="active", starts_at=chain.now())
    t.winner, t.contract, t.status = b.id, k.id, "contracted"
    return {"winner": b.id, "score": b.score, "contract": k.id, "justification": why}
