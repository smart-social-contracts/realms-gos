from core import chain, language, poa
from core.db import Fail, Forbidden, deref, find, next_id, page_by, ref, view
from ggg import Event, External, Notification, User
from core.op import op, ops
from api.external import queue
from api.proposals import appointed

SHOWN = ("id", "topic", "title", "body", "href", "sender", "at")


def notify(recipient, title, body="", href="", topic="", sender="", translations=()):
    """Leave a notification for ``recipient`` ("User:alice", "Department:Health", "Organization:clinic-b")."""
    return Notification(id=next_id(Notification, "N"), recipient=recipient, title=title, body=body, href=href,
                        topic=topic, sender=sender, at=chain.now(), translations=list(translations)).id


def in_language(n, tag):
    """``n`` as a reader whose language is ``tag`` reads it: the title and body in their language, when it has one."""
    found = language.pick(n.translations, tag)
    return {"title": found["title"], "body": found.get("body") or n.body} if found else {}


def addressed_to(c):
    """The recipients whose notifications the caller reads: themselves and where they hold a seat. A delegate reads
    only the member's own: the inboxes of an office belong to the people who hold its seats."""
    if c.delegated:
        return {f"User:{c.user}"}
    return {f"User:{c.user}"} | {ref(a.position.department or a.position.organization) for a in appointed(c.user)}


def inbox(c, args):
    """``recipient`` (default "User:<you>"), which must be you or a department or organization where you hold a seat."""
    recipient = args.get("recipient") or f"User:{c.user}"
    if recipient not in addressed_to(c):
        raise Forbidden(f"you do not read {recipient}'s notifications")
    return recipient


@op("self.data_view", read=True, returns="notifications")
def notification_list(c, args):
    """The notifications of ``recipient`` (default "User:<you>"; or "Department:<id>", "Organization:<id>" where
    you hold a seat), oldest first, ``count`` at a time (default 100, at most 500) from row ``from`` (default 1);
    ``unread`` keeps only those you have not read. Returns ``notifications`` and ``next`` (null at the end)."""
    rows, nxt = page_by(Notification, "recipient", inbox(c, args), args)
    rows = [n for n in rows if not (args.get("unread") and c.user in n.read_by)]
    reader = User[c.user]
    tag = reader.language if reader else ""
    return {"notifications": [{**view(n, *SHOWN), **in_language(n, tag), "read": c.user in n.read_by} for n in rows],
            "next": nxt}


def left_unread(c, n):
    """A delegate leaves the member's power-of-attorney notices unread: they are how the member learns what it did."""
    return c.delegated and n.topic == poa.TOPIC


@op("self.data_manage")
def notification_read(c, args):
    """Mark notifications as read: ``notification`` (one id), or with ``all`` a page of ``recipient``'s (default
    "User:<you>"), ``count`` (default 100, at most 500) from row ``from`` (default 1); call again with ``next`` until
    it is null. Returns ``read``, how many were newly marked. A delegate marks none of the member's power-of-attorney
    notices (topic ``poa``): by id it is refused, and with ``all`` they stay unread."""
    if args.get("all"):
        rows, nxt = page_by(Notification, "recipient", inbox(c, args), args)
    else:
        rows, nxt = [find(Notification, args["notification"])], None
        if rows[0].recipient not in addressed_to(c):
            raise Forbidden(f"notification {rows[0].id} is not addressed to you")
        if left_unread(c, rows[0]):
            raise Forbidden(f"notification {rows[0].id} tells you what your delegates did: only you mark it read")
    fresh = [n for n in rows if c.user not in n.read_by and not left_unread(c, n)]
    for n in fresh:
        n.read_by = n.read_by + [c.user]
    return {"read": len(fresh), "next": nxt}


def valid_translations(args):
    translations = args.get("translations") or []
    if not (isinstance(translations, list) and all(isinstance(t, dict) for t in translations)):
        raise Fail("translations are a list of {language, title, body}")
    for t in translations:
        language.require(t.get("language"))
        if not (isinstance(t.get("title"), str) and t["title"]) or not isinstance(t.get("body", ""), str):
            raise Fail("a translation has a title, and a body in text")


@op("notification.send", check=valid_translations)
def notification_send(c, args):
    """Notify ``recipient`` ("User:<id>", "Department:<id>", "Organization:<id>" or "External:<id>") with ``title`` and
    optional ``body``, ``href`` and ``topic``; you are the sender. ``translations`` (``[{language, title, body}]``) give
    each reader the notice in their language, else the title and body above. A notice to an outside body is queued as a
    dispatch for an operator to deliver, and a ``body`` that holds personal data must be the id of an encrypted
    Document, never the data. Returns ``notification``, or ``dispatch`` for an outside body."""
    kind, _, key = args["recipient"].partition(":")
    if kind == "External":
        body = {"text": args.get("body", "")}
        return {"dispatch": queue(find(External, key), args.get("topic", ""), args["title"], body).id}
    deref(args["recipient"])
    return {"notification": notify(args["recipient"], args["title"], args.get("body", ""), args.get("href", ""),
                                   args.get("topic", ""), c.user, args.get("translations") or [])}


@op("realm.monitor", read=True, returns="events")
def events(c, args):
    """The audit log, newest first: ``count`` events (default 100, at most 500) from id ``from`` (default the latest)
    down; ``next`` continues it."""
    start = int(args.get("from") or Event.max_id())
    count = min(int(args.get("count") or 100), 500)
    rows = [Event[str(i)] for i in range(start, max(start - count, 0), -1)]
    return {"events": [view(e) for e in rows if e], "next": start - count if start > count else None}


@op(None, read=True, public=True)
def ops_list(c, args):
    """Every operation with its permission, flags and description. ``delegable`` says a power of attorney can reach it in
    this release (the groups it can be granted in are in ``delegable_operations``)."""
    return {"operations": [{"name": name, "perm": s["perm"], "read": s["read"], "delegable": poa.reachable(name, s),
                            "governed": s["governed"] is not None, "signing": s["signing"], "public": s["public"],
                            "doc": (s["fn"].__doc__ or "").strip()} for name, s in sorted(ops().items())]}
