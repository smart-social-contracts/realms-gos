"""Pending actions (core.pending): what a member reads and decides, and what a delegate prepares and collects.

An action is shown as a card: ``id`` (AC-<n>), ``grantor`` (the member), ``delegate``, ``grant``, ``method`` and
``args`` (the exact call), ``title`` (the operation's plain name, written by the realm), ``delegate_is_member`` (the
delegate is a member of the realm, not an AI connection), ``summary`` (the realm's own words: ``[{label, value}]``),
``risk``, ``note`` (the delegate's own words, to be shown apart), ``status`` (pending, approved, approving, done,
failed, declined, withdrawn, expired or void), ``hash``, ``filed_at``, ``expires_at``, ``decided_at`` and ``result``
(the reply of the call once it ran, ``{ok, ...}``, without its secrets). The member decides on the page
``/delegations/confirm/<id>`` of the realm.

In ``summary``, a line whose label is a lower-case word is an argument of the call, with the value the delegate gave it:
a client shows these apart from the realm's own lines, whose labels have several words ("What changes", "What happens
next") and whose values quote what the delegate wrote.
"""
import json

from core import pending
from core.db import Fail, Forbidden, page_by
from core.op import dispatch, op, ops
from ggg import PendingAction

LISTED = ("grantor", "delegate")
FOR_TIERS = ("direct", "confirm")


@op("self.join", read=True, returns="action")
def action_get(c, args):
    """The pending action ``action``, to its grantor or to the delegate that prepared it: a delegate acting for the grantor
    reads only the actions it prepared. Returns ``action`` as a card, or null for anyone else and for an id that does not
    exist. A waiting action whose time or grant has run out reads expired or void."""
    a = PendingAction[str(args["action"])]
    return {"action": pending.card(c, pending.settled(a)) if a and pending.reads(c, a) else None}


@op("self.join", read=True, returns="actions")
def action_list(c, args):
    """Your pending actions as ``role`` grantor (the default: "Waiting for you") or delegate, optionally only those with
    ``status``, ``count`` at a time (default 100, at most 500) from row ``from`` (default 1), as cards. A delegate acting
    for the grantor lists only the actions it prepared. Returns ``actions`` and ``next`` (null at the end)."""
    role = args.get("role", "grantor")
    if role not in LISTED:
        raise Fail(f"role is one of {', '.join(LISTED)}")
    rows, nxt = page_by(PendingAction, role, c.user, args)
    settled = [pending.settled(a) for a in rows if pending.reads(c, a)]
    return {"actions": [pending.card(c, a) for a in settled if not args.get("status") or a.status == args["status"]],
            "next": nxt}


@op("self.confirm", returns="action")
def action_approve(c, args):
    """Approve the pending action ``action`` by its ``hash``, which you read on its card: nothing else is taken, so what runs
    is what you were shown. The realm runs the stored call as you, with every check your own call would meet now. Your own
    act: refused under a power of attorney and from any task, extension, codex, shell or replay. Returns ``action``: done
    with its ``result`` (secrets included, here alone), or failed if the call itself was refused; a read is approved
    and its delegate collects it. Refused, and nothing run, if the hash differs, the action is not pending, its day has
    passed (it reads expired) or its grant has ended or no longer covers it (it reads void)."""
    if set(args) != {"action", "hash"}:
        raise Fail("an approval takes the action and its hash, nothing else")
    a, out = yield from pending.approve(c, args["action"], args["hash"])
    return {"action": pending.card(c, a, out)}


@op("self.confirm", returns="action")
def action_decline(c, args):
    """Decline the pending action ``action``: nothing runs and it is closed. Your own act, as ``action_approve``. Returns
    ``action``."""
    return {"action": pending.card(c, pending.decline(c, args["action"]))}


@op("self.join", returns="action")
def action_withdraw(c, args):
    """Take back ``action``, a pending action you prepared, while it waits for the member: you do this as yourself. To change
    a call, withdraw it and file another. Returns ``action``."""
    return {"action": pending.card(c, pending.withdraw(c, args["action"]))}


@op("self.join")
def action_collect(c, args):
    """Run once the read of ``action``, a pending action you prepared that the member approved, as the member and now, while
    the grant still covers it and before the action lapses. You do this as yourself. Returns ``method``, ``args`` and
    ``result``, the read's reply. A second call, or one after the action lapsed or its grant ended, is refused; the audit
    log records the collection, never the reply."""
    a, out = yield from pending.collect(c, args["action"])
    return {"method": a.method, "args": a.args, "result": out}


def for_member(c, args):
    """The grantor, the operation, its spec and its arguments of a call a delegate makes for a member, which is refused unless
    it is the delegate's own, for another member, of an operation a delegate can reach."""
    if c.origin != "user":
        raise Forbidden("this is the delegate's own call: not from another operation")
    grantor, name = args["grantor"], args["method"]
    if grantor == c.user:
        raise Fail("act for yourself by calling the operation itself")
    spec = ops().get(name)
    if spec is None:
        raise Fail(f"unknown operation '{name}'")
    inner = dict(args.get("args") or {})
    if inner.get("on_behalf_of") not in (None, grantor):
        raise Fail("the arguments name another member to act for: there is no chaining through another grant")
    if spec["tier"] not in FOR_TIERS:
        raise Forbidden(f"{name} cannot be done for a member")
    return grantor, name, spec, {**inner, "on_behalf_of": grantor}


@op("self.join", private=("args",))
def call_for(c, args):
    """Call the operation ``method`` with ``args`` for ``grantor``, who has granted you power of attorney, with an optional
    ``note`` (at most 500 characters, shown to the member apart from the realm's own words). The call meets every check a
    delegated call meets. Returns the operation's reply when it ran or filed a proposal (``proposal``, ``requires_vote``),
    or, for a call that asks the member each time, ``pending`` (the action's id), ``approve_link`` (the path of the
    page where the member decides) and ``expires_at``. A read goes through ``read_for``, unless it asks the member each
    time. Refused for yourself, for an operation that is the member's own, the founder's or no one's, and with arguments
    that name another member."""
    grantor, name, spec, inner = for_member(c, args)
    if spec["read"] and not pending.asked_under(grantor, c.by, spec, inner):
        raise Fail(f"{name} is a read: use read_for")
    out = json.loads((yield from dispatch(name, json.dumps(inner), signer=c.by, note=str(args.get("note") or ""))))
    if not out.pop("ok"):
        raise pending.refusal(out)
    return out


@op("self.join", read=True, private=("args",))
def read_for(c, args):
    """Read through the operation ``method`` with ``args`` for ``grantor``, who has granted you power of attorney. Returns the
    operation's reply. Refused for an operation that changes the realm (use ``call_for``), for a read that asks the member
    each time (``call_for`` files it as a pending action), and as ``call_for`` is."""
    grantor, name, spec, inner = for_member(c, args)
    if not spec["read"]:
        raise Fail(f"{name} changes the realm: use call_for")
    if pending.asked_under(grantor, c.by, spec, inner):
        raise Forbidden(f"{name} needs the member's approval: file it with call_for")
    out = json.loads((yield from dispatch(name, json.dumps(inner), signer=c.by, query=True)))
    if not out.pop("ok"):
        raise pending.refusal(out)
    return out
