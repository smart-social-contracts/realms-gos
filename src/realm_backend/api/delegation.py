from core import auth
from core import chain
from core.db import Fail, each, find, next_id, page_by, view
from ggg import Delegation, DelegationRequest, User
from core.op import op, ops

YEAR = 365 * 86400
REQUEST_TTL = 3600
OPEN_REQUESTS_MAX = 10
OPEN = ("pending", "active")


def delegable():
    return {s["perm"] for s in ops().values() if s["delegable"] and not s["signing"] and s["perm"]}


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
            raise Fail(f"{grantor} does not hold {p}")


def window(args):
    starts = int(args.get("starts_at") or 0)
    begin = max(starts, chain.now())
    expires = int(args.get("expires_at") or 0) or begin + int(args.get("expires_in") or 0)
    if expires <= begin:
        raise Fail("set expires_at (or expires_in) after the start")
    if expires - begin > YEAR:
        raise Fail("a delegation lasts at most one year")
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


def issue(grantor, delegate, perms, starts, expires, by, args, acceptance):
    for d in linked(grantor, delegate):
        close(d, status(d))
    d = Delegation(id=next_id(Delegation, "D"), grantor=grantor, delegate=delegate, scope={"operations": perms},
                   link=f"{grantor}|{delegate}",
                   status="pending" if acceptance else "active", label=args.get("label", ""),
                   description=args.get("description", ""), requires_acceptance=acceptance, granted_by=by,
                   accepted_at=0 if acceptance else chain.now(), starts_at=starts, expires_at=expires)
    return {"delegation": d.id, "status": d.status}


@op("self.delegate")
def delegation_grant(c, args):
    """Let ``delegate`` act for you with ``operations`` (permission names you hold) from ``starts_at`` (default now)
    until ``expires_at`` or for ``expires_in`` seconds, at most a year; optional ``label`` and ``description``.
    Returns ``delegation`` and ``status``: pending until a delegate who is a user accepts, otherwise active at once."""
    delegate, perms = args["delegate"], list(dict.fromkeys(args["operations"]))
    if delegate == c.user:
        raise Fail("you cannot delegate to yourself")
    check_scope(perms, c.user)
    starts, expires = window(args)
    if open_grant(c.user, delegate):
        raise Fail(f"{delegate} already holds a grant from you; revoke it first")
    # An AI connection has no User row, hence no inbox: its grant is active at once.
    return issue(c.user, delegate, perms, starts, expires, c.by, args, User[delegate] is not None)


@op("self.delegate")
def delegation_accept(c, args):
    """Accept ``delegation``, a pending grant to you that has not expired, so it becomes active."""
    d = find(Delegation, args["delegation"])
    if d.delegate != c.user or d.status != "pending":
        raise Fail(f"{d.id} is not a pending grant to you")
    if status(d) == "expired":
        raise Fail(f"{d.id} expired at {d.expires_at}")
    d.status, d.accepted_at = "active", chain.now()


@op("self.delegate")
def delegation_revoke(c, args):
    """End ``delegation`` as its grantor or delegate; a delegate turning down a pending grant declines it.
    Returns ``status``."""
    d = find(Delegation, args["delegation"])
    if c.user not in (d.grantor, d.delegate) and "all" not in auth.permissions(c.user):
        raise Fail(f"{d.id} is not yours")
    if status(d) not in OPEN:
        raise Fail(f"{d.id} is already {status(d)}")
    close(d, "declined" if d.status == "pending" and c.user == d.delegate else "revoked", c.by)
    return {"status": d.status}


@op("self.join")
def delegation_request(c, args):
    """Ask ``grantor`` to let you act for them with ``operations`` (permission names) from ``starts_at`` until
    ``expires_at`` or for ``expires_in`` seconds; optional ``label`` and ``description``. Returns ``request``.
    The request stays open for an hour; asking the same grantor again replaces it. A grantor can have at most 10
    open requests, and so can you."""
    grantor, perms = args["grantor"], list(dict.fromkeys(args["operations"]))
    find(User, grantor)
    if grantor == c.user:
        raise Fail("you cannot ask yourself")
    check_scope(perms)
    starts, expires = window(args)
    to_them = list(each(DelegationRequest, "grantor", grantor))
    from_me = list(each(DelegationRequest, "delegate", c.user))
    gone = {r.id: r for r in to_them + from_me
            if r.request_expires_at <= chain.now() or (r.grantor, r.delegate) == (grantor, c.user)}
    if sum(r.id not in gone for r in to_them) >= OPEN_REQUESTS_MAX:
        raise Fail(f"{grantor} has too many open requests; try again later")
    if sum(r.id not in gone for r in from_me) >= OPEN_REQUESTS_MAX:
        raise Fail("you have too many open requests; wait for answers first")
    for r in gone.values():
        r.delete()
    r = DelegationRequest(id=next_id(DelegationRequest, "DR"), grantor=grantor, delegate=c.user,
                          scope={"operations": perms}, label=args.get("label", ""),
                          description=args.get("description", ""), starts_at=starts, expires_at=expires,
                          request_expires_at=chain.now() + REQUEST_TTL)
    return {"request": r.id}


@op("self.delegate")
def delegation_request_decide(c, args):
    """Answer ``request``, made to you: ``approve`` true grants it, optionally for fewer ``operations`` or an earlier
    ``expires_at``, and replaces your open grant to that delegate; otherwise it is declined.
    Returns ``status``, and ``delegation`` when approved."""
    r = find(DelegationRequest, args["request"])
    if r.grantor != c.user or r.request_expires_at <= chain.now():
        raise Fail(f"{r.id} is not an open request to you")
    if not args.get("approve"):
        r.delete()
        return {"status": "declined"}
    asked = r.scope["operations"]
    perms = list(dict.fromkeys(args.get("operations") or asked))
    until = int(args.get("expires_at") or r.expires_at)
    if set(perms) - set(asked):
        raise Fail(f"not asked for: {', '.join(sorted(set(perms) - set(asked)))}")
    if until > r.expires_at or until <= max(r.starts_at, chain.now()):
        raise Fail("the expiry can only be made earlier")
    check_scope(perms, c.user)
    old = open_grant(r.grantor, r.delegate)
    if old:
        close(old, "revoked", c.by)
    out = issue(r.grantor, r.delegate, perms, r.starts_at, until, c.by,
                {"label": r.label, "description": r.description}, False)
    r.delete()
    return out


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


@op("self.data_view", read=True)
def delegable_operations(c, args):
    """The operations that can be delegated, each with the permission to name in a grant's ``operations``."""
    return {"operations": sorted(({"op": n, "permission": s["perm"]} for n, s in ops().items()
                                  if s["delegable"] and not s["signing"]), key=lambda o: o["op"])}
