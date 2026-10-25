from core import chain
from core.db import Fail, deref, find, next_id, page_by, ref, view
from ggg import Event, Notification
from core.op import op, ops
from api.proposals import appointed

SHOWN = ("id", "topic", "title", "body", "href", "sender", "at")


def notify(recipient, title, body="", href="", topic="", sender=""):
    """Leave a notification for ``recipient`` ("User:alice", "Department:Health", "Organization:clinic-b")."""
    return Notification(id=next_id(Notification, "N"), recipient=recipient, title=title, body=body, href=href,
                        topic=topic, sender=sender, at=chain.now()).id


def addressed_to(principal):
    """The recipients whose notifications ``principal`` reads: themselves and where they hold a seat."""
    return {f"User:{principal}"} | {ref(a.position.department or a.position.organization) for a in appointed(principal)}


def inbox(c, args):
    """``recipient`` (default "User:<you>"), which must be you or a department or organization where you hold a seat."""
    recipient = args.get("recipient") or f"User:{c.user}"
    if recipient not in addressed_to(c.user):
        raise Fail(f"you do not read {recipient}'s notifications")
    return recipient


@op("self.data_view", read=True, delegable=True)
def notification_list(c, args):
    """The notifications of ``recipient`` (default "User:<you>"; or "Department:<id>", "Organization:<id>" where
    you hold a seat), oldest first, ``count`` at a time (default 100, at most 500) from row ``from`` (default 1);
    ``unread`` keeps only those you have not read. Returns ``notifications`` and ``next`` (null at the end)."""
    rows, nxt = page_by(Notification, "recipient", inbox(c, args), args)
    rows = [n for n in rows if not (args.get("unread") and c.user in n.read_by)]
    return {"notifications": [{**view(n, *SHOWN), "read": c.user in n.read_by} for n in rows], "next": nxt}


@op("self.data_manage", delegable=True)
def notification_read(c, args):
    """Mark notifications as read: ``notification`` (one id), or with ``all`` a page of ``recipient``'s (default
    "User:<you>"), ``count`` (default 100, at most 500) from row ``from`` (default 1); call again with ``next`` until
    it is null. Returns ``read``, how many were newly marked."""
    if args.get("all"):
        rows, nxt = page_by(Notification, "recipient", inbox(c, args), args)
    else:
        rows, nxt = [find(Notification, args["notification"])], None
        if rows[0].recipient not in addressed_to(c.user):
            raise Fail(f"notification {rows[0].id} is not addressed to you")
    fresh = [n for n in rows if c.user not in n.read_by]
    for n in fresh:
        n.read_by = n.read_by + [c.user]
    return {"read": len(fresh), "next": nxt}


@op("notification.send")
def notification_send(c, args):
    """Notify ``recipient`` ("User:<id>", "Department:<id>" or "Organization:<id>") with ``title`` and optional
    ``body``, ``href`` and ``topic``; you are the sender. Returns ``notification``."""
    deref(args["recipient"])
    return {"notification": notify(args["recipient"], args["title"], args.get("body", ""), args.get("href", ""),
                                   args.get("topic", ""), c.user)}


@op("realm.monitor", read=True)
def events(c, args):
    """The audit log, newest first: ``count`` events (default 100, at most 500) from id ``from`` (default the latest)
    down; ``next`` continues it."""
    start = int(args.get("from") or Event.max_id())
    count = min(int(args.get("count") or 100), 500)
    rows = [Event[str(i)] for i in range(start, max(start - count, 0), -1)]
    return {"events": [view(e) for e in rows if e], "next": start - count if start > count else None}


@op(None, read=True, public=True)
def ops_list(c, args):
    """Every operation with its permission, flags and description."""
    return {"operations": [{"name": name, "perm": s["perm"], "read": s["read"], "delegable": s["delegable"],
                            "governed": s["governed"] is not None, "signing": s["signing"], "public": s["public"],
                            "doc": (s["fn"].__doc__ or "").strip()} for name, s in sorted(ops().items())]}
