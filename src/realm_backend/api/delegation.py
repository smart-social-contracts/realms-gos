from core import auth
from core import chain
from core.db import Fail, each, find, next_id, view
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
    lapsed = d.status == "active" and d.expires_at and chain.now() >= d.expires_at
    return "expired" if lapsed else d.status


def open_grant(grantor, delegate):
    for d in each(Delegation, "delegate", delegate):
        if d.grantor == grantor and status(d) in OPEN:
            return d
    return None


def open_requests(field, principal):
    return [r for r in each(DelegationRequest, field, principal) if r.request_expires_at > chain.now()]


def card(d):
    return {**view(d, "id", "grantor", "delegate", "scope", "label", "description", "starts_at", "expires_at",
                   "accepted_at", "revoked_at"), "status": status(d)}


def mine(principal):
    return {"as_grantor": [card(d) for d in each(Delegation, "grantor", principal)],
            "as_delegate": [card(d) for d in each(Delegation, "delegate", principal)]}


def issue(grantor, delegate, perms, starts, expires, by, args, acceptance):
    d = Delegation(id=next_id(Delegation, "D"), grantor=grantor, delegate=delegate, scope={"operations": perms},
                   status="pending" if acceptance else "active", label=args.get("label", ""),
                   description=args.get("description", ""), requires_acceptance=acceptance, granted_by=by,
                   accepted_at=0 if acceptance else chain.now(), starts_at=starts, expires_at=expires)
    return {"delegation": d.id, "status": d.status}


@op("self.delegate")
def delegation_grant(c, args):
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
    d = find(Delegation, args["delegation"])
    if d.delegate != c.user or d.status != "pending":
        raise Fail(f"{d.id} is not a pending grant to you")
    d.status, d.accepted_at = "active", chain.now()


@op("self.delegate")
def delegation_revoke(c, args):
    d = find(Delegation, args["delegation"])
    if c.user not in (d.grantor, d.delegate) and "all" not in auth.permissions(c.user):
        raise Fail(f"{d.id} is not yours")
    if status(d) not in OPEN:
        raise Fail(f"{d.id} is already {status(d)}")
    declined = d.status == "pending" and c.user == d.delegate
    d.status, d.revoked_at, d.revoked_by = "declined" if declined else "revoked", chain.now(), c.by
    return {"status": d.status}


@op("self.join")
def delegation_request(c, args):
    grantor, perms = args["grantor"], list(dict.fromkeys(args["operations"]))
    find(User, grantor)
    if grantor == c.user:
        raise Fail("you cannot ask yourself")
    check_scope(perms)
    starts, expires = window(args)
    older = [r for r in open_requests("delegate", c.user) if r.grantor == grantor]
    if len(open_requests("grantor", grantor)) - len(older) >= OPEN_REQUESTS_MAX:
        raise Fail(f"{grantor} has too many open requests; try again later")
    for r in older:
        r.delete()
    r = DelegationRequest(id=next_id(DelegationRequest, "DR"), grantor=grantor, delegate=c.user,
                          scope={"operations": perms}, label=args.get("label", ""),
                          description=args.get("description", ""), starts_at=starts, expires_at=expires,
                          request_expires_at=chain.now() + REQUEST_TTL)
    return {"request": r.id}


@op("self.delegate")
def delegation_request_decide(c, args):
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
        old.status, old.revoked_at, old.revoked_by = "revoked", chain.now(), c.by
    out = issue(r.grantor, r.delegate, perms, r.starts_at, until, c.by,
                {"label": r.label, "description": r.description}, False)
    r.delete()
    return out


@op("self.data_view", read=True)
def delegation_list(c, args):
    requests = open_requests("grantor", c.user) + open_requests("delegate", c.user)
    return {**mine(c.user), "requests": [view(r, "id", "grantor", "delegate", "scope", "label", "description",
                                              "starts_at", "expires_at", "request_expires_at") for r in requests]}


@op("self.data_view", read=True)
def delegable_operations(c, args):
    return {"operations": sorted(({"op": n, "permission": s["perm"]} for n, s in ops().items()
                                  if s["delegable"] and not s["signing"]), key=lambda o: o["op"])}
