from core import chain
from core.db import Fail, deref, each, find, next_id, ref, view
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


@op("self.data_view", read=True, delegable=True)
def notification_list(c, args):
    """Your notifications, newest first; ``unread`` keeps only those you have not read."""
    rows = [n for r in addressed_to(c.user) for n in each(Notification, "recipient", r)]
    rows = [n for n in rows if not (args.get("unread") and c.user in n.read_by)]
    rows.sort(key=lambda n: (n.at, int(n.id.split("-")[1])), reverse=True)
    shown = rows[:int(args.get("count") or 50)]
    return {"notifications": [{**view(n, *SHOWN), "read": c.user in n.read_by} for n in shown],
            "unread": sum(c.user not in n.read_by for n in rows)}


@op("self.data_manage", delegable=True)
def notification_read(c, args):
    """Mark notifications as read: ``notification`` (one id) or ``all``."""
    if args.get("all"):
        rows = [n for r in addressed_to(c.user) for n in each(Notification, "recipient", r)]
    else:
        rows = [find(Notification, args["notification"])]
        if rows[0].recipient not in addressed_to(c.user):
            raise Fail(f"notification {rows[0].id} is not addressed to you")
    for n in rows:
        if c.user not in n.read_by:
            n.read_by = n.read_by + [c.user]
    return {"read": len(rows)}


@op("notification.send")
def notification_send(c, args):
    deref(args["recipient"])
    return {"notification": notify(args["recipient"], args["title"], args.get("body", ""), args.get("href", ""),
                                   args.get("topic", ""), c.user)}


@op("realm.monitor", read=True)
def events(c, args):
    """The audit log, newest first: ``count`` events before id ``from``; ``next`` continues it."""
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
