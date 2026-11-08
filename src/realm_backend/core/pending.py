"""Pending actions: a call that asks the member each time (core.poa) is prepared by the delegate, and runs only when the
member approves it.

``prepare`` files a delegate's call as a PendingAction instead of running it: the exact operation and arguments, sealed
with a hash, described in the realm's own words. The member reads that description and approves with the id and the hash
alone (``approve``), and the realm then runs the stored call as the member. Nothing else changes the call: the record is
never edited, it lasts a day, it works once, and it dies with its grant. Expiry and a lost grant are worked out when an
action is read, listed or approved (``settled``); nothing sweeps.
"""
import json

from core import auth, chain, poa, ranges
from core.db import Conflict, Fail, Forbidden, JSON_MAX, NotFound, Unavailable, each, find, next_id, view
from core.sandbox import sha256

TTL = 86400  # a pending action lasts a day
APPROVING_TTL = 600  # an approval still running after this long ended in a trap
OPEN_MAX = 10  # actions waiting between one member and one delegate
# The characters of a call's arguments as canonical JSON: a Json field holds 65,536, and the page shows the whole call.
ARGS_MAX = 32 * 1024
NOTE_MAX = 500
WAITING = ("pending", "approved")  # an approved read waits for its delegate to collect it
TOPIC = poa.TOPIC + ".confirm"  # of the notice that tells the member an action waits for them
LINK = "/delegations/confirm/"
CARD = ("id", "grantor", "delegate", "grant", "method", "args", "summary", "risk", "note", "status", "hash", "filed_at",
        "expires_at", "decided_at")
REFUSALS = {"not_found": NotFound, "forbidden": Forbidden, "conflict": Conflict, "unavailable": Unavailable}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def seal(aid, method, args, grantor, delegate, grant, expires_at):
    """The hash of a record: the realm's canister id (so no action works in another realm), the id, the call, who prepared
    it for whom under which grant, and when it lapses."""
    return sha256(canonical({"realm": chain.self_id(), "id": aid, "method": method, "args": args, "grantor": grantor,
                             "delegate": delegate, "grant": grant, "expires_at": expires_at}))


def refusal(out):
    """The exception that stands for a refused call's reply ``out`` (``ok`` false), so that it is refused again."""
    if out.get("denied"):
        return auth.Denied(out["error"], out.get("permission"))
    return REFUSALS.get(out.get("kind"), Fail)(out["error"])


# ---- Filing


def asked_under(user, by, spec, args):
    """The grant the delegate ``by`` acts for ``user`` under, when this call of ``spec`` asks the member each time: its
    group grant, else None. A call under no grant, or under an old one, is refused as any delegated call that is not
    direct is (core.auth.authorize)."""
    try:
        grant = auth.active_grant(user, by)
    except auth.Denied:
        return None
    return grant if poa.asks(grant, spec, args) else None


def prepare(grant, user, by, name, spec, args, note=""):
    """File the call of ``name`` with ``args`` that the delegate ``by`` makes for ``user`` under ``grant``, in place of
    running it. The grant must cover it and the member, acting for themselves, be allowed it now. Returns the action's id,
    the path of the page where the member decides, and when it lapses."""
    from core.op import audit
    from ggg import PendingAction

    needed = spec["perm_for"](args) if spec["perm_for"] else spec["perm"]
    if not poa.covered(grant, name, spec, needed):
        raise auth.Denied(f"{name} cannot be done under this power of attorney: this grant does not cover it")
    auth.authorize(user, user, name, spec, args)
    ranges.check(name, args)
    if spec["check"]:
        spec["check"](args)
    if len(canonical(args)) > ARGS_MAX:
        raise Fail(f"the arguments are over {ARGS_MAX // 1024} KB: the member could not read them")
    if len(note) > NOTE_MAX:
        raise Fail(f"a note is at most {NOTE_MAX} characters")
    pair = f"{user}|{by}"
    if len([a for a in list(each(PendingAction, "open_link", pair)) if settled(a).open_link]) >= OPEN_MAX:
        raise Conflict(f"{by} has {OPEN_MAX} actions waiting for {user}: wait for answers first")
    aid, now = next_id(PendingAction, "AC"), chain.now()
    lines = summary_of(user, name, spec, args)
    if len(json.dumps(lines)) > JSON_MAX - 1000:
        raise Fail("the call is too long to show the member")
    PendingAction(id=aid, grantor=user, delegate=by, grant=grant.id, method=name, args=args,
                  hash=seal(aid, name, args, user, by, grant.id, now + TTL), summary=lines, risk=risk_of(name),
                  note=note, filed_at=now, expires_at=now + TTL, open_link=pair)
    audit(name, user, by, "user", args, {"pending": aid}, grant.id)
    tell(grant, aid, name)
    return {"pending": aid, "approve_link": LINK + aid, "expires_at": now + TTL}


def tell(grant, aid, name):
    """Leave the member a notice that the action waits for them. A delegate cannot mark it read (api.notifications)."""
    from ggg import Notification

    who = grant.label or grant.delegate
    Notification(id=next_id(Notification, "N"), recipient=f"User:{grant.grantor}", topic=TOPIC,
                 title=f"{who} asks you to approve {name}", body="It waits for you for a day.", href=LINK + aid,
                 sender=grant.delegate, at=chain.now())


# ---- What the member reads


def risk_of(name):
    """The sentence of the group the operation belongs to: what it risks."""
    group = poa.group_of(name)
    return poa.GROUPS[group][2] if group else ""


def summary_of(user, name, spec, args):
    """The realm's words about the call, a label and a value per line: each argument under its own name, in lower case,
    then what the operation says of its effect (its ``summary`` hook) under a label that starts with a capital, then
    where a governed call goes. An argument cannot take a label of the realm's: its name is a lower-case word."""
    for key in args:
        if not (key.isascii() and key.isidentifier() and key == key.lower()):
            raise Fail(f"'{key}' is not an argument name: they are lower-case words")
    lines = [{"label": key, "value": shown(key, value, args)} for key, value in sorted(args.items())]
    if spec["summary"]:
        lines += spec["summary"](user, args)
    if spec["governed"]:
        lines.append({"label": "Then", "value": consequence(spec["governed"](args))})
    return lines


def quoted(text):
    """Text the delegate supplied, inside a sentence of the realm's: quoted, so that it cannot pass for the realm's words."""
    return json.dumps(str(text))


def shown(key, value, args):
    """An argument as the member reads it: a principal with its nickname, an amount in the token's decimals when the
    call names the token, any structure as JSON."""
    from ggg import Token, User

    token = Token[str(args["symbol"])] if args.get("symbol") else None
    if key == "amount" and isinstance(value, int) and not isinstance(value, bool) and token:
        return f"{amount(value, token.decimals)} {token.id}"
    if isinstance(value, str):
        member = User[value] if len(value) <= 64 else None
        return f"{value} ({member.nickname})" if member and member.nickname else value
    return canonical(value)


def amount(units, decimals):
    """``units`` of a token with ``decimals`` places as a decimal number."""
    whole, part = divmod(units, 10 ** decimals)
    return f"{whole}.{part:0{decimals}d}".rstrip("0").rstrip(".") if decimals else str(whole)


def consequence(department):
    from core.op import direct

    if department is None or direct(department):
        return "Runs at once"
    return f"Files a proposal under {department.id}; the realm votes"


def card(a, result=None):
    """An action as a client reads it. ``result`` replaces the stored one: the approval's own reply carries what is not
    kept (``blank``)."""
    return {**view(a, *CARD), "result": a.result if result is None else result}


# ---- States


def holds(a):
    """Whether the grant ``a`` was prepared under still lets its delegate prepare it: active, in force, and covering the
    operation (its groups, its limits)."""
    from core.op import ops
    from ggg import Delegation

    grant, spec = Delegation[a.grant], ops().get(a.method)
    return bool(grant and spec and grant.status == "active" and auth.in_force(grant, chain.now())
                and poa.covered(grant, a.method, spec, spec["perm"]))


def close(a, state):
    a.status, a.open_link = state, None


def settled(a):
    """``a`` with what the clock and its grant have brought about worked out: a waiting action past its time is expired, one
    whose grant ended or no longer covers it is void, and an approval that has run for longer than a call can ended
    in a trap: it failed, and is never retried."""
    now = chain.now()
    if a.status == "approving":
        if now - a.decided_at >= APPROVING_TTL:
            a.status = "failed"
    elif a.status in WAITING:
        if now >= a.expires_at:
            close(a, "expired")
        elif not holds(a):
            close(a, "void")
    return a


def blank(name, value):
    """``value``, the reply of operation ``name``, with its secrets (core.op.redacted's) blanked at any depth: the action
    keeps the reply for whoever reads it later."""
    from core.op import SECRET, ops

    spec = ops().get(name)
    hidden = set(SECRET) | set(spec["private"] if spec else ()) | set(spec["secret"] if spec else ())

    def clean(x):
        if isinstance(x, dict):
            return {k: "" if k in hidden else clean(v) for k, v in x.items()}
        return [clean(i) for i in x] if isinstance(x, list) else x

    return clean(value)


# ---- Deciding


def decision(c, aid):
    """The action ``aid`` for its grantor to decide, as it stands: the member's own act, so never under a grant, and
    not from a task, an extension, a codex, a shell or a replay."""
    from ggg import PendingAction

    if c.delegated or c.origin != "user":
        raise Forbidden("approving or declining is the member's own act: never under a grant or from another operation")
    a = settled(find(PendingAction, aid))
    if a.grantor != c.user:
        raise Forbidden(f"{a.id} is not yours to decide")
    if a.status not in WAITING:
        raise Conflict(f"{a.id} is {a.status}")
    return a


def approve(c, aid, sent):
    """Run the call of the action ``aid`` as its grantor, on the grantor's own approval that sends its hash. A generator.
    Returns the action and the reply of the call; the call's own refusal fails the action, it does not undo the approval."""
    from core.op import dispatch, ops

    a = decision(c, aid)
    if a.status != "pending":
        raise Conflict(f"{a.id} is {a.status}")
    if sent != a.hash:
        raise Fail(f"the hash does not match {a.id}: read it again")
    a.decided_at = chain.now()
    if ops()[a.method]["read"]:  # a read is approved once, and its delegate collects it
        a.status = "approved"
        return a, None
    # Before anything awaits: a second approval that arrives while the call waits on another canister meets this.
    a.status, a.open_link = "approving", None
    out = json.loads((yield from dispatch(a.method, json.dumps(a.args), origin="confirmed", signer=a.grantor, prepared=a)))
    a.result, a.status = blank(a.method, out), "done" if out["ok"] else "failed"
    return a, out


def decline(c, aid):
    a = decision(c, aid)
    a.decided_at = chain.now()
    close(a, "declined")
    return a


def withdraw(c, aid):
    """The delegate takes back its own action while it waits."""
    from ggg import PendingAction

    a = settled(find(PendingAction, aid))
    if a.delegate != c.user or c.delegated:
        raise Forbidden(f"{a.id} was not prepared by you")
    if a.status not in WAITING:
        raise Conflict(f"{a.id} is {a.status}")
    close(a, "withdrawn")
    return a


def collect(c, aid):
    """Run the read of the approved action ``aid`` once, as its grantor, for the delegate that prepared it. A generator.
    The audit log records that it was collected, never what it returned. Returns the action and the reply."""
    from core.op import audit, dispatch
    from ggg import PendingAction

    a = settled(find(PendingAction, aid))
    if a.delegate != c.user or c.delegated:
        raise Forbidden(f"{a.id} was not prepared by you")
    if a.status != "approved":
        raise Conflict(f"{a.id} is {a.status}")
    close(a, "done")  # once: nothing awaits before this
    out = json.loads((yield from dispatch(a.method, json.dumps(a.args), origin="confirmed", signer=a.grantor)))
    if not out["ok"]:
        a.status = "failed"
        raise refusal(out)
    audit(a.method, a.grantor, a.delegate, "confirmed", a.args, {"collected": a.id}, a.grant, a.delegate)
    return a, out
