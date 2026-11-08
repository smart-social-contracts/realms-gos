from core import auth
from core import chain
from core import poa
from core.db import Conflict, Fail, Forbidden, each, find, next_id, page_by, view, window as paging
from ggg import Delegation, DelegationRequest, Event, User
from core.op import op, ops, oldest_event

DAY = 86400
YEAR = 365 * DAY
REQUEST_TTL = 3600
OPEN_REQUESTS_MAX = 10
OPEN = ("pending", "active")


def delegable():
    """The permissions a grant that names permissions can carry: those of the operations it covers (core.poa.LEGACY)."""
    return {s["perm"] for n, s in ops().items() if poa.is_legacy(n, s) and not s["signing"] and s["perm"]}


def signing():
    return {s["perm"] for s in ops().values() if s["signing"]}


def check_scope(perms, grantor=None):
    if not perms:
        raise Fail("name at least one permission")
    held = auth.permissions(grantor) if grantor else set()
    for p in perms:
        if p == "all" or p in signing():
            raise Fail(f"{p} can never be delegated")
        if p not in delegable():
            raise Fail(f"{p} is not a delegable permission")
        if grantor and "all" not in held and p not in held:
            raise Forbidden(f"{grantor} does not hold {p}")


def scope_of(args):
    """The scope a grant or request names: ``groups`` or, as before groups, ``operations`` (permission names), not both."""
    groups, perms = args.get("groups"), args.get("operations")
    if groups and perms:
        raise Fail("name groups or permissions, not both")
    if groups:
        return {"groups": list(dict.fromkeys(groups))}
    return {"operations": list(dict.fromkeys(perms or []))}


def check_names(scope, grantor=None):
    """Refuse a scope that cannot be granted; ``grantor`` must hold what it names."""
    if "groups" in scope:
        poa.check_groups(scope["groups"], auth.permissions(grantor) if grantor else None)
    else:
        check_scope(scope["operations"], grantor)


def longest(scope):
    return poa.longest_grant(scope["groups"]) if "groups" in scope else YEAR


def too_long(lasts, limit):
    if lasts > limit:
        raise Fail(f"a delegation lasts at most {'one year' if limit == YEAR else str(limit // DAY) + ' days'}")


def window(args, limit=YEAR):
    starts = int(args.get("starts_at") or 0)
    begin = max(starts, chain.now())
    expires = int(args.get("expires_at") or 0) or begin + int(args.get("expires_in") or 0)
    if expires <= begin:
        raise Fail("set expires_at (or expires_in) after the start")
    too_long(expires - begin, limit)
    return starts, expires


def status(d):
    lapsed = d.status in OPEN and d.expires_at and chain.now() >= d.expires_at
    return "expired" if lapsed else d.status


def linked(grantor, delegate):
    """The grants from ``grantor`` to ``delegate`` not yet closed: the open one and any that lapsed since."""
    return list(each(Delegation, "link", f"{grantor}|{delegate}"))


def open_grant(grantor, delegate):
    return next((d for d in linked(grantor, delegate) if status(d) in OPEN), None)


def close(d, state, by=""):
    d.status, d.link = state, None
    if by:
        d.revoked_at, d.revoked_by = chain.now(), by


def open_requests(field, principal):
    # At most OPEN_REQUESTS_MAX rows each way: delegation_request deletes expired ones before adding one.
    return [r for r in each(DelegationRequest, field, principal) if r.request_expires_at > chain.now()]


def card(d):
    return {**view(d, "id", "grantor", "delegate", "scope", "label", "description", "starts_at", "expires_at",
                   "accepted_at", "revoked_at"), "status": status(d)}


def mine(principal, args=None):
    """A page of ``principal``'s grants each way; ``next`` maps grantor and delegate to the ``from`` continuing it."""
    args, out = args or {}, {"next": {}}
    for side in ("grantor", "delegate"):
        rows, out["next"][side] = page_by(Delegation, side, principal,
                                          {"from": args.get(f"from_{side}"), "count": args.get("count")})
        out[f"as_{side}"] = [card(d) for d in rows]
    return out


def issue(grantor, delegate, scope, starts, expires, by, args, acceptance):
    for d in linked(grantor, delegate):
        close(d, status(d))
    d = Delegation(id=next_id(Delegation, "D"), grantor=grantor, delegate=delegate, scope=scope,
                   link=f"{grantor}|{delegate}",
                   status="pending" if acceptance else "active", label=args.get("label", ""),
                   description=args.get("description", ""), requires_acceptance=acceptance, granted_by=by,
                   accepted_at=0 if acceptance else chain.now(), starts_at=starts, expires_at=expires)
    return {"delegation": d.id, "status": d.status}


@op("self.delegate", returns="delegation")
def delegation_grant(c, args):
    """Let ``delegate`` act for you with ``groups`` (ids from ``delegable_operations``, in each of which you hold an
    operation, or "*" for everything) or with ``operations`` (permission names you hold, as before groups), not both,
    from ``starts_at`` (default now) until ``expires_at`` or for ``expires_in`` seconds, at most the shortest of the
    groups' longest, and a year; optional ``label`` and ``description``.
    Returns ``delegation`` and ``status``: pending until a delegate who is a user accepts, otherwise active at once."""
    delegate, scope = args["delegate"], scope_of(args)
    if delegate == c.user:
        raise Fail("you cannot delegate to yourself")
    check_names(scope, c.user)
    starts, expires = window(args, longest(scope))
    if open_grant(c.user, delegate):
        raise Conflict(f"{delegate} already holds a grant from you; revoke it first")
    # An AI connection has no User row, hence no inbox: its grant is active at once.
    return issue(c.user, delegate, scope, starts, expires, c.by, args, User[delegate] is not None)


@op("self.delegate", returns="delegation")
def delegation_accept(c, args):
    """Accept ``delegation``, a pending grant to you that has not expired, so it becomes active."""
    d = find(Delegation, args["delegation"])
    if d.delegate != c.user or d.status != "pending":
        raise Conflict(f"{d.id} is not a pending grant to you")
    if status(d) == "expired":
        raise Conflict(f"{d.id} expired at {d.expires_at}")
    d.status, d.accepted_at = "active", chain.now()
    return {"delegation": d.id}


@op("self.delegate", returns="delegation")
def delegation_revoke(c, args):
    """End ``delegation`` as its grantor or delegate; a delegate turning down a pending grant declines it.
    Returns ``status``."""
    d = find(Delegation, args["delegation"])
    if c.user not in (d.grantor, d.delegate) and "all" not in auth.permissions(c.user):
        raise Forbidden(f"{d.id} is not yours")
    if status(d) not in OPEN:
        raise Conflict(f"{d.id} is already {status(d)}")
    close(d, "declined" if d.status == "pending" and c.user == d.delegate else "revoked", c.by)
    return {"delegation": d.id, "status": d.status}


@op("self.join", returns="request")
def delegation_request(c, args):
    """Ask ``grantor`` to let you act for them with ``groups`` (or "*") or ``operations`` (permission names), as
    ``delegation_grant`` takes them, from ``starts_at`` until ``expires_at`` or for ``expires_in`` seconds; optional
    ``label`` and ``description``. Returns ``request``.
    The request stays open for an hour; asking the same grantor again replaces it. A grantor can have at most 10
    open requests, and so can you."""
    grantor, scope = args["grantor"], scope_of(args)
    find(User, grantor)
    if grantor == c.user:
        raise Fail("you cannot ask yourself")
    check_names(scope)
    starts, expires = window(args, longest(scope))
    to_them = list(each(DelegationRequest, "grantor", grantor))
    from_me = list(each(DelegationRequest, "delegate", c.user))
    gone = {r.id: r for r in to_them + from_me
            if r.request_expires_at <= chain.now() or (r.grantor, r.delegate) == (grantor, c.user)}
    if sum(r.id not in gone for r in to_them) >= OPEN_REQUESTS_MAX:
        raise Conflict(f"{grantor} has too many open requests; try again later")
    if sum(r.id not in gone for r in from_me) >= OPEN_REQUESTS_MAX:
        raise Conflict("you have too many open requests; wait for answers first")
    for r in gone.values():
        r.delete()
    r = DelegationRequest(id=next_id(DelegationRequest, "DR"), grantor=grantor, delegate=c.user,
                          scope=scope, label=args.get("label", ""),
                          description=args.get("description", ""), starts_at=starts, expires_at=expires,
                          request_expires_at=chain.now() + REQUEST_TTL)
    return {"request": r.id}


@op("self.delegate", returns="delegation")
def delegation_request_decide(c, args):
    """Answer ``request``, made to you: ``approve`` true grants it, optionally for fewer ``groups`` (or
    ``operations``) or an earlier ``expires_at``, and replaces your open grant to that delegate; otherwise it is
    declined. Returns ``status``, and ``delegation`` when approved."""
    r = find(DelegationRequest, args["request"])
    if r.grantor != c.user or r.request_expires_at <= chain.now():
        raise Conflict(f"{r.id} is not an open request to you")
    if not args.get("approve"):
        r.delete()
        return {"status": "declined"}
    scope = scope_of(args) if args.get("groups") or args.get("operations") else r.scope
    narrower(scope, r.scope)
    until = int(args.get("expires_at") or r.expires_at)
    begin = max(r.starts_at, chain.now())
    if until > r.expires_at or until <= begin:
        raise Fail("the expiry can only be made earlier")
    check_names(scope, c.user)
    too_long(until - begin, longest(scope))
    old = open_grant(r.grantor, r.delegate)
    if old:
        close(old, "revoked", c.by)
    out = issue(r.grantor, r.delegate, scope, r.starts_at, until, c.by,
                {"label": r.label, "description": r.description}, False)
    r.delete()
    return out


def narrower(scope, asked):
    """Refuse a ``scope`` that names anything the request ``asked`` did not. Everything covers every group."""
    kind = "groups" if "groups" in asked else "operations"
    if kind not in scope:
        raise Fail(f"the request names {kind}")
    extra = set(scope[kind]) - set(asked[kind])
    if extra and poa.EVERYTHING_SCOPE not in asked[kind]:
        raise Fail(f"not asked for: {', '.join(sorted(extra))}")


@op("self.data_view", read=True)
def delegation_list(c, args):
    """Your grants given (``as_grantor``) and received (``as_delegate``), ``count`` of each at a time (default 100,
    at most 500) from rows ``from_grantor`` and ``from_delegate`` (default 1), and your open ``requests`` either way.
    ``next`` maps grantor and delegate to the ``from_grantor`` and ``from_delegate`` of the next page (null at the
    end)."""
    requests = open_requests("grantor", c.user) + open_requests("delegate", c.user)
    return {**mine(c.user, args), "requests": [view(r, "id", "grantor", "delegate", "scope", "label", "description",
                                                    "starts_at", "expires_at", "request_expires_at")
                                               for r in requests]}


@op("self.data_view", read=True, returns="operations")
def delegable_operations(c, args):
    """What a grant can carry. ``operations``: the operations a grant that names permissions covers, each with the
    permission to name. ``groups``: the groups a grant can name (``delegation_groups``)."""
    return {"operations": sorted(({"op": n, "permission": s["perm"]} for n, s in ops().items()
                                  if poa.is_legacy(n, s) and not s["signing"]), key=lambda o: o["op"]),
            "groups": poa.vocabulary(auth.permissions(c.user))}


@op("self.data_view", read=True, returns="groups")
def delegation_groups(c, args):
    """The groups you can grant, ``count`` at a time (default 100, at most 500) from row ``from`` (default 1): for each
    its ``id``, ``label``, ``summary``, ``risk``, ``longest`` (seconds), whether some of its operations ask you each time
    (``confirms``), and the ``operations`` in it you hold, each with its ``tier`` (direct or confirm). A grant may also
    name "*" for everything. ``next`` continues the list (null at the end)."""
    start, count = paging(args)
    groups = poa.vocabulary(auth.permissions(c.user))
    return {"groups": groups[start - 1:start - 1 + count], "next": start + count if len(groups) >= start + count else None}


@op("self.data_view", read=True, returns="events")
def delegation_activity(c, args):
    """The events recorded under ``delegation``, a grant of yours or to you, newest first: ``count`` (default 100, at
    most 500) with an id up to ``from`` (default the latest); ``next`` continues them (null at the end). The audit log
    keeps its last events only: ``since`` is the time of the oldest it still holds, and the ``poa`` notices are the
    lasting record."""
    d = find(Delegation, args["delegation"])
    if c.user not in (d.grantor, d.delegate):
        raise Forbidden(f"{d.id} is not yours")
    last, count = int(args.get("from") or Event.max_id()), min(int(args.get("count") or 100), 500)
    newest = [e for e in reversed(list(each(Event, "grant", d.id))) if int(e.id) <= last]
    oldest = oldest_event()
    return {"events": [view(e) for e in newest[:count]], "since": oldest.at if oldest else 0,
            "next": int(newest[count - 1].id) - 1 if len(newest) > count else None}


@op("self.delegate")
def delegation_revoke_all(c, args):
    """End every pending or active grant you gave, ``count`` of your grants at a time (default 100, at most 500) from
    row ``from`` (default 1); call again with ``next`` until it is null. Returns ``revoked``, how many it ended. It is
    your own act: never under a grant."""
    rows, nxt = page_by(Delegation, "grantor", c.user, args)
    open_grants = [d for d in rows if status(d) in OPEN]
    for d in open_grants:
        close(d, "revoked", c.by)
    return {"revoked": len(open_grants), "next": nxt}
