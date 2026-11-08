"""Requests to the realm: a petition, a complaint, a request for information or for one's personal data (GGG §7).

The kinds are the realm's. Legislation sets a request's due date and says what silence means, recording it with
``request_decide``. A ``body`` or ``reply`` that holds personal data is the id of an encrypted Document (I-PRV-1)."""
from core import chain
from core.db import Conflict, Fail, Forbidden, NotFound, find, next_id, page, view
from ggg import Organization, Request
from api.licenses import administers, principals
from core.op import op


def filer(user, organization):
    """Who files: ``user``, or the ``organization`` they administer."""
    if not organization:
        return f"User:{user}"
    org = find(Organization, organization)
    if not administers(org, user):
        raise Forbidden(f"you do not administer {org.id}")
    return f"Organization:{org.id}"


def filed_by(args):
    return principals(find(Request, args["request"]).by)


def valid_filing(args):
    if not (isinstance(args["kind"], str) and args["kind"]):
        raise Fail("a request has a kind")


def valid_due(args):
    for key in ("due_at", "extension_limit"):
        if args.get(key) is not None and type(args[key]) is not int:
            raise Fail(f"{key} is a time in seconds")


@op("request.file", check=valid_filing, private=("body",), secret=("body",), returns="request")
def request_file(c, args):
    """File a request of ``kind`` (the realm's word, such as petition) with a ``body``, for you or for an
    ``organization`` you administer. A body that holds personal data is the id of an encrypted Document, never the
    data; the audit log keeps neither. Returns ``request``, filed."""
    r = Request(id=next_id(Request, "REQ"), kind=args["kind"], by=filer(c.user, args.get("organization")),
                body=args["body"], filed_at=chain.now())
    return {"request": r.id}


@op("self.data_view", read=True, returns="request")
def request_get(c, args):
    """``request`` (an id), if it is yours or your organization's; any other is not found. Anyone who decides
    requests lists them with request_list. Returns the ``request``."""
    r = find(Request, args["request"])
    if c.user not in principals(r.by):
        raise NotFound(f"unknown Request '{r.id}'")
    return {"request": view(r)}


@op("request.decide", read=True, returns="requests")
def request_list(c, args):
    """One page of requests (``from``, ``count``), optionally only those of ``kind`` and ``status`` (filed or
    decided); ``next`` continues it. A page can hold fewer matches than ``count``: follow ``next``."""
    want = {k: args[k] for k in ("kind", "status") if args.get(k)}
    rows, more = page(Request, args, lambda r: all(getattr(r, k) == v for k, v in want.items()))
    return {"requests": [view(r) for r in rows], "next": more}


def open_request(args):
    """The request a deadline or a decision is about: it must still be filed (I-LIC-3: a decided request is final)."""
    r = find(Request, args["request"])
    if r.status != "filed":
        raise Conflict(f"{r.id} is {r.status}")
    return r


@op("request.decide", parties=filed_by, check=valid_due, returns="request")
def request_set_due(c, args):
    """Set or move the deadline ``due_at`` of the filed ``request``, never past its ``extension_limit``. The limit
    can be given once, with the first deadline or later; a second one is a conflict."""
    r, given = open_request(args), args.get("extension_limit")
    if given and r.extension_limit:
        raise Conflict(f"{r.id} has its extension limit already")
    limit = given or r.extension_limit
    if limit and args["due_at"] > limit:
        raise Fail(f"the deadline of {r.id} cannot pass its extension limit")
    r.due_at, r.extension_limit = args["due_at"], limit
    return {"request": r.id}


@op("request.decide", parties=filed_by, private=("reply",), secret=("reply",), returns="request")
def request_decide(c, args):
    """Decide the filed ``request``: ``decision`` is the realm's word for the outcome (granted, refused,
    refused_by_silence), with an optional ``reply``; a reply that holds personal data is the id of an encrypted
    Document. A decided request is final."""
    r = open_request(args)
    if not (isinstance(args["decision"], str) and args["decision"]):
        raise Fail("a decision says what was decided")
    r.status, r.decision, r.reply, r.decided_at = "decided", args["decision"], args.get("reply", ""), chain.now()
    return {"request": r.id}
