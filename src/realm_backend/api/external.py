"""Outside bodies (GGG §11): authorities, journals and lenders, which are outside the realm and outside the Internet
Computer. Nothing outside calls the realm and the realm calls no outside body. A notice to one is a Dispatch in an
outbox: an operator or a bridge delivers it and confirms it here with the receipt (``dispatch_confirm``). What an outside
body says enters only through ``external_report``, by a principal who received it (I-EXT-6)."""
from core import chain
from core.db import Conflict, Fail, find, next_id, page, view
from ggg import Dispatch, Document, External
from core.op import op
from api.proposals import realm_wide

# tender_notice names the realm's own journal with this word, so no outside body can take it.
REALM_JOURNAL = "realm"


def queue(external, topic, title, body, document="", codex=""):
    """Queue a notice for the outside body ``external`` (a row). Returns the dispatch."""
    return Dispatch(id=next_id(Dispatch, "DSP"), to=external.id, topic=topic, title=title, body=body,
                    document=document, codex=codex, queued_at=chain.now())


def valid_external(args):
    for key in ("external", "name", "kind"):
        if not (isinstance(args[key], str) and args[key]):
            raise Fail(f"an outside body has a {key}")
    if args["external"] == REALM_JOURNAL:
        raise Fail(f"'{REALM_JOURNAL}' names the realm's own journal")
    if not (args["channel"] == "manual" or args["channel"].startswith("https://")):
        raise Fail("a channel is manual, or an https URL a bridge delivers to")


@op("external.manage", governed=realm_wide, check=valid_external, returns="external")
def external_add(c, args):
    """Register the outside body ``external`` (an id) with its ``name``, ``kind`` (authority, journal, lender, ...) and
    ``channel``: ``manual`` when an operator delivers its dispatches, or an https URL when a bridge does. The same id
    again redefines the body. Returns ``external``."""
    body = External[args["external"]] or External(id=args["external"])
    body.name, body.kind, body.channel = args["name"], args["kind"], args["channel"]
    return {"external": body.id}


@op("realm.data_view", read=True, returns="externals")
def external_list(c, args):
    """One page of the outside bodies the realm deals with (``from``, ``count``); ``next`` continues it."""
    rows, nxt = page(External, args)
    return {"externals": [view(e) for e in rows], "next": nxt}


def valid_report(args):
    if not (isinstance(args["topic"], str) and args["topic"]):
        raise Fail("a report has a topic")


@op("external.report", check=valid_report)
def external_report(c, args):
    """Enter what the outside body ``external`` said or sent: its ``topic`` and ``body``, with the ``evidence_ref`` of
    the file you received. The call's audit event is the record, and legislation hooks it: the realm never takes an
    outside body's word without a principal's. The audit log keeps the arguments of a report only up to 2000
    characters of JSON, so a long body belongs in the file the ``evidence_ref`` names."""
    find(External, args["external"])


def outcome_of(args):
    """``(case, payload)`` of the ``outcome`` of a dispatch_confirm call: Confirmed with a record, or Failed with the reason."""
    outcome = args["outcome"]
    if not (isinstance(outcome, dict) and len(outcome) == 1 and next(iter(outcome)) in ("Confirmed", "Failed")):
        raise Fail("an outcome is Confirmed, with when and the receipt, or Failed, with the reason")
    case, payload = next(iter(outcome.items()))
    if case == "Failed" and not (isinstance(payload, str) and payload):
        raise Fail("a failed dispatch says why")
    if case == "Confirmed" and not isinstance(payload, dict):
        raise Fail("a confirmation is a record with when (at) and the receipt")
    return case, payload


def fill_publication(d, at, receipt):
    """A confirmed dispatch of a document to a journal gives the document's publication its date and reference."""
    doc = Document[d.document] if d.document else None
    if doc is not None:
        doc.publications = [{**p, "published_at": at, "ref": receipt or None} if p["dispatch"] == d.id else p
                            for p in doc.publications]


@op("dispatch.confirm", check=outcome_of, returns="dispatch")
def dispatch_confirm(c, args):
    """Give the queued ``dispatch`` its one outcome: ``outcome`` is ``{"Confirmed": {at, receipt}}``, where ``at`` is
    when it was delivered (default now, between the time it was queued and now) and ``receipt`` the bulletin number or
    filing reference that proves it, or ``{"Failed": reason}``. A second outcome is a conflict. A confirmed dispatch
    of a document to a journal fills the document's publication. Returns ``dispatch``."""
    d = find(Dispatch, args["dispatch"])
    if d.status != "queued":
        raise Conflict(f"{d.id} is already {d.status}")
    case, payload = outcome_of(args)
    if case == "Failed":
        d.status, d.failure = "failed", payload
        return {"dispatch": d.id}
    at, now = payload.get("at") or chain.now(), chain.now()
    if not d.queued_at <= at <= now:
        raise Fail(f"{d.id} was queued at {d.queued_at}: it cannot have been delivered at {at}, before then or after now")
    d.status, d.outcome_at, d.receipt = "confirmed", at, payload.get("receipt") or ""
    fill_publication(d, at, d.receipt)
    return {"dispatch": d.id}


@op("dispatch.confirm", read=True, returns="dispatches")
def dispatch_list(c, args):
    """The outbox: one page of dispatches (``from``, ``count``), optionally only those ``to`` an outside body or with a
    ``status`` (queued, confirmed or failed); ``next`` continues it. A page can hold fewer matches than ``count``:
    follow ``next``."""
    want = {k: args[k] for k in ("to", "status") if args.get(k)}
    rows, nxt = page(Dispatch, args, lambda d: all(getattr(d, k) == v for k, v in want.items()))
    return {"dispatches": [view(d) for d in rows], "next": nxt}
