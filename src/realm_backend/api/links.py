"""Links between realms (GGG's model §10 and §11; docs/codices/layered-codex.md §8, §11): the realms this one takes
legislation from, its sync sources, and those that take it from this one, its followers, the releases it
publishes, and the records of its sync sources, which it reads.

A link is a row on each side, made by that side's vote (``sync_source_add``, ``follower_accept``): ``requested`` until
the other side's vote is in, then ``active`` (a stand-in record, which cannot vote, is active on the follower's vote
alone), and ``ended`` when either side ends it (I-NEST-1). The side whose vote changes a link tells the other side's
capital with ``link_notify`` and reads its answer; a notice nobody answered is sent again (I-NEST-3). A vote refuses a
cycle, at any remove (I-NEST-2). Calls between realms go between the capitals of a link and nowhere else, and a quarter
makes none (``only_capital``).
"""

import json

from api import proposals, quarters
from core import chain
from core import compliance
from core import layered_codex as store
from core.db import Conflict, Fail, Forbidden, Unavailable, each, find, page, view, window
from core.op import op
from core.records import MOST_CHAPTERS, NOTES_MAX, VERSION_MAX, is_realm_key, no_at, pinned
from ggg import ChapterSource, Follower, Release, SyncSource

SIDES = {SyncSource: "follower", Follower: "sync_source"}  # what a link's owner is to the realm it notifies
CLOCK_TOLERANCE = 5 * 60  # seconds a subnet's clock may differ from this one's
WALK_LIMIT = 32  # realms read by one check for a cycle
RETRY = 60  # seconds before a notice nobody answered is sent again
NAME_MAX, URL_MAX = 200, 500
MOST_LAYERS = 16  # a chain is at most 8 deep (§4.4)
STATUSES = ("requested", "active", "ended")


# ---- Links


def only_capital():
    if quarters.federated():
        raise Forbidden("only the capital of a realm links it to other realms: a quarter calls no other realm")


def is_realm(link):
    return is_realm_key(link.id)


def link_status(link):
    """GGG's ``LinkStatus`` of a link, as the realm's JSON has it: ``requested``, ``active`` or ``{"Ended": ...}``."""
    return {"Ended": {"by": link.ended_by, "at": link.ended_at}} if link.status == "ended" else link.status


def source_ref(source):
    """GGG's ``SyncSourceRef`` of a sync source, as the realm's JSON has it."""
    return {"Realm": source.id} if is_realm(source) else {"Record": {"url": source.id, "checksum": source.checksum}}


def source_view(source):
    """A sync source as GGG's ``Realm.sync_sources`` lists it, in the realm's JSON."""
    return {"source": source_ref(source), "name": source.name, "status": link_status(source)}


def capital_of(text):
    """A realm's capital as a principal's text. Never a url, which is how a stand-in record is told from a realm."""
    if not (isinstance(text, str) and text and all(ch.isalnum() or ch == "-" for ch in text)):
        raise Fail("a realm is named by its capital, a principal")
    return text


def source_key(ref):
    """The id a ``SyncSourceRef`` is kept under: the capital of a realm, the url of a stand-in record (which a second
    add pins to another checksum). Fail unless it is one of the two, well formed."""
    one = isinstance(ref, dict) and len(ref) == 1
    realm, stand_in = ref.get("Realm") if one else None, ref.get("Record") if one else None
    if realm is not None:
        return capital_of(realm)
    if isinstance(stand_in, dict) and set(stand_in) == {"url", "checksum"}:
        url = stand_in["url"]
        if not (isinstance(url, str) and url.startswith("https://") and len(url) <= URL_MAX):
            raise Fail("a stand-in record's url is https")
        if not pinned(stand_in["checksum"]):
            raise Fail("a stand-in record is pinned by its content's checksum, sha256:<64 hex digits>")
        return url
    raise Fail('source is {"Realm": <capital>} or {"Record": {"url": <https url>, "checksum": <sha256:...>}}')


def valid_add(args):
    source_key(args["source"])
    if not isinstance(args.get("name"), str) or not 0 < len(args["name"]) <= NAME_MAX:
        raise Fail(f"name is 1 to {NAME_MAX} characters")


def valid_remove(args):
    source_key(args["source"])


def valid_follower(args):
    capital_of(args.get("follower"))


def open_link(cls, key, **fields):
    """This side's vote: the link recorded as ``requested``, in place of an ended one under the same key. Returns the
    row and what it replaced, for ``undo``. It is written before the check for a cycle reads any other realm, so that a
    vote cast at the same time on the other side sees it (I-NEST-2), and it is ``checking`` until the check is over:
    the other side's notice is refused meanwhile, as if this side had not voted yet, for the check may still undo the
    vote (I-NEST-1)."""
    old = cls[key]
    before = view(old) if old is not None else None
    if old is not None:
        old.delete()
    return cls(id=key, checking=True, **fields), before


def undo(link, before):
    cls = type(link)
    link.delete()
    if before is not None:
        cls(**before)


def sources():
    """Every sync source of the realm, whatever the state of its link."""
    for status in STATUSES:
        yield from each(SyncSource, "status", status)


def open_followers():
    """The followers whose links have not ended."""
    return [f for status in STATUSES[:2] for f in each(Follower, "status", status)]


def open_links():
    """The links that have not ended, as ``sync source <id>`` and ``follower <id>``."""
    return [f"sync source {s.id}" for s in sources() if s.status != "ended"] + [f"follower {f.id}" for f in open_followers()]


def takes_legislation_from(start, target):
    """Whether realm ``start`` takes legislation from ``target``, directly or through other realms, over links that
    have not ended, as each realm says now. A realm that cannot be read, or a walk past WALK_LIMIT realms, fails the
    check with Unavailable: never an open link. The followers of this realm take legislation from it, whatever their
    own realm_info says yet: their vote may not have reached it. A generator."""
    todo, seen = [start], set()
    followers = {f.id for f in open_followers()} if target == chain.self_id() else set()
    while todo:
        realm = todo.pop()
        if realm == target or realm in followers:
            return True
        if realm in seen:
            continue
        seen.add(realm)
        if len(seen) > WALK_LIMIT:
            raise Unavailable("too many realms to check for a cycle")
        found = yield from sources_of(realm)
        todo += found
    return False


def sources_of(realm):
    """The capitals of the realms ``realm`` takes legislation from over links that have not ended. A generator."""
    if realm == chain.self_id():
        return [s.id for s in sources() if is_realm(s) and s.status != "ended"]
    try:
        info = yield from quarters.ask(realm, "realm_info", {})
        return [capital_of(s["source"]["Realm"]) for s in info["sync_sources"]
                if "Realm" in s["source"] and not isinstance(s["status"], dict)]
    except Exception:  # noqa: BLE001  rejected (stopped, gone, no realm), refused, or an answer that is no realm's
        raise Unavailable(f"{realm} could not be read to check for a cycle")


def vote_link(cls, key, cycle, **fields):
    """This side's vote on a link to ``key``: recorded, checked for a ``cycle`` ``(start, target)``, which undoes the
    record when it fails, and told to the other side. A generator."""
    link, before = open_link(cls, key, **fields)
    try:
        if (yield from takes_legislation_from(*cycle)):
            raise Conflict(f"{key} and this realm would take legislation from each other, directly or through other "
                           f"realms: a cycle")
    except Exception:  # noqa: BLE001  whatever stops the vote, the record written before the first call goes
        undo(link, before)
        raise
    cls[key].checking = False
    yield from tell(cls[key])  # their vote is in or it is not: this notice completes the link in either order
    return {}


def notify(link):
    """Tell the capital at the other side what this side's vote, or its ending, made of ``link``, and read the answer:
    ``active`` means their vote is in. An answer of any kind is an answer, a refusal among them; a call that fails is
    not, and the link stays ``unsent``. Returns whether it was answered. A generator."""
    cls, key = type(link), link.id
    try:
        reply = yield from quarters.ask(key, "link_notify", {"side": SIDES[cls], "status": link_status(link)})
    except Exception:  # noqa: BLE001  rejected: the realm is stopped, gone, or has no such method
        cls[key].unsent = True
        return False
    link = cls[key]  # the link may have changed while the call was out
    link.unsent = False
    if isinstance(reply, dict) and reply.get("ok") and reply.get("status") == "active" and link.status == "requested":
        link.status = "active"
    return True


def tell(link):
    """``notify``, and the timer that sends it again if nobody answered: one chain of timers per link, started when the
    link first becomes ``unsent``, as the one running sends the link's status as it is then. A generator."""
    waiting = link.unsent
    if not (yield from notify(link)) and not waiting:
        retry_later(type(link), link.id)


def retry_later(cls, key):
    chain.set_timer(RETRY, lambda: resend(cls, key))


def resend(cls, key):
    """A notice nobody answered, sent again with the link's status now, and the next timer set if nobody answers. The
    other side ending the link ends the chain: its notice (``received``) settles what this realm had to tell. A
    generator."""
    link = cls[key]
    if link is not None and link.unsent and not (yield from notify(link)):
        retry_later(cls, key)


def resume():
    """Timers do not survive an upgrade: send again the notices nobody answered."""
    for cls in SIDES:
        for link in each(cls, "unsent", True):
            retry_later(cls, link.id)


def end_link(cls, key):
    """This side ends the link under ``key``: ended by this realm, now, and told to the other side. A generator."""
    link = find(cls, key)
    if link.status == "ended":
        raise Conflict("this link has ended")
    link.status, link.ended_by, link.ended_at = "ended", chain.self_id(), chain.now()
    if is_realm(link):
        yield from tell(link)
    return {}


@op("sync_source.manage", governed=proposals.realm_wide, check=valid_add)
def sync_source_add(c, args):
    """Take legislation from ``source``, a realm (``{"Realm": <capital>}``) or a stand-in record of a body that runs no
    realm (``{"Record": {"url": <https url>, "checksum": <sha256:...>}}``), called ``name``. A realm's link is
    ``requested`` until it accepts this one, and ``active`` then, in either order; a stand-in record cannot vote, so its
    link is active on this vote, and a second add of the same url pins the new checksum. A source that takes legislation
    from this realm, directly or through others, is a cycle (Conflict); one that cannot be read is Unavailable. Only
    the capital of a federation links it to other realms."""
    only_capital()
    ref, key = args["source"], source_key(args["source"])
    old = SyncSource[key]
    if "Record" in ref:
        if old is None:
            SyncSource(id=key, checksum=ref["Record"]["checksum"], name=args["name"], status="active")
        else:
            old.checksum, old.name, old.status = ref["Record"]["checksum"], args["name"], "active"
            old.ended_by, old.ended_at = "", 0
        return {}
    if key == chain.self_id():
        raise Conflict("a realm does not take legislation from itself")
    if old is not None and old.status != "ended":
        raise Conflict(f"{key} is already a sync source")
    return (yield from vote_link(SyncSource, key, (key, chain.self_id()), name=args["name"]))


@op("sync_source.manage", governed=proposals.realm_wide, check=valid_remove)
def sync_source_remove(c, args):
    """Stop taking legislation from ``source`` (the url alone names a stand-in record): the link ends, by this realm and
    now, the source is told, and the layers this realm installed stay. Reserved (§10.4): no legislation can forbid,
    guard or hook it, so a realm can always leave."""
    only_capital()
    return (yield from end_link(SyncSource, source_key(args["source"])))


@op("follower.manage", governed=proposals.realm_wide, check=valid_follower)
def follower_accept(c, args):
    """Accept realm ``follower`` (its capital) as a follower. Its link is ``requested`` until it has added this realm as
    a sync source, and ``active`` then, in either order. A follower that this realm takes legislation from, directly or
    through others, is a cycle (Conflict). Only the capital of a federation links it to other realms."""
    only_capital()
    key = args["follower"]
    if key == chain.self_id():
        raise Conflict("a realm is not its own follower")
    old = Follower[key]
    if old is not None and old.status != "ended":
        raise Conflict(f"{key} is already a follower")
    return (yield from vote_link(Follower, key, (chain.self_id(), key)))


@op("follower.manage", governed=proposals.realm_wide, check=valid_follower)
def follower_remove(c, args):
    """End the link to ``follower``: ended by this realm and now, and the follower is told. It keeps what it
    installed."""
    only_capital()
    return (yield from end_link(Follower, args["follower"]))


def received(link, caller, status):
    """The link after the other side's notice: their vote is in, or they ended it. A notice that a link ended names its
    sender as ``by``, at a time not ahead of this realm's clock by more than a few minutes (I-NEST-3). A notice about a
    link that has ended is a Conflict, unless it says it ended."""
    ended = isinstance(status, dict)
    if link.status == "ended":
        if not ended:
            raise Conflict("this link has ended")
        return
    if not ended:
        link.status = "active"
        return
    by, at = status["Ended"]["by"], status["Ended"]["at"]
    if by != caller:
        raise Fail("a notice that a link ended names its sender as by")
    if at > chain.now() + CLOCK_TOLERANCE:
        raise Fail("a notice that a link ended is not dated ahead of this realm's clock")
    link.status, link.ended_by, link.ended_at, link.unsent = "ended", by, at, False


def valid_status(status):
    if status in ("requested", "active"):
        return
    ended = status.get("Ended") if isinstance(status, dict) and len(status) == 1 else None
    if not (isinstance(ended, dict) and set(ended) == {"by", "at"} and isinstance(ended["by"], str)
            and type(ended["at"]) is int):
        raise Fail('status is "requested", "active" or {"Ended": {"by": <capital>, "at": <seconds>}}')


def valid_notice(args):
    if args.get("side") not in ("sync_source", "follower"):
        raise Fail('side is "sync_source" or "follower": what the caller is to this realm')
    valid_status(args.get("status"))


@op("link.sync", returns="status", check=valid_notice)
def link_notify(c, args):
    """From the capital at the other side of a link this realm has voted on (a vote still being checked for a cycle
    counts as not voted yet): its ``side`` to this realm (``sync_source``
    or ``follower``) and the link's new ``status`` after its vote (``requested`` or ``active``: its vote is in) or
    ``{"Ended": {"by": <its capital>, "at": <seconds>}}``. Replies this realm's status of the link: ``active`` tells the
    caller its vote completed the link. Legislation may hook it (``on_link_notify``) and never guard or forbid it."""
    cls = Follower if args["side"] == "follower" else SyncSource
    link = cls[c.by]
    if link is None or link.checking:
        raise Forbidden(f"{c.by} is not the capital at the other side of a link this realm has voted on")
    received(link, c.by, args["status"])
    return {"status": link_status(link)}


def valid_report(args):
    layers = args.get("layers")
    if not (isinstance(layers, list) and len(layers) <= MOST_LAYERS and all(
            isinstance(layer, dict) and set(layer) == {"version", "checksum"} and isinstance(layer["version"], str)
            and len(layer["version"]) <= VERSION_MAX and pinned(layer["checksum"]) for layer in layers)):
        raise Fail(f"layers are at most {MOST_LAYERS} of {{version, checksum: sha256:<64 hex digits>}}")
    compliance.valid(args.get("compliance"))


@op("link.sync", returns="follower", check=valid_report)
def follower_report(c, args):
    """From a follower's capital, over an active link: the ``layers`` it runs (each ``{version, checksum}``), its
    ``compliance`` against this realm and its ``population``, its own claims, kept as its last report with the time.
    This realm matches a layer by its checksum in its record; one the record does not list matches nothing."""
    follower = Follower[c.by]
    if follower is None:
        raise Forbidden(f"{c.by} is not a follower this realm has accepted")
    if follower.status != "active":
        raise Conflict("the link is not active")
    follower.layers, follower.compliance = args["layers"], compliance.valid(args["compliance"])
    follower.population, follower.reported_at = int(args["population"]), chain.now()
    return {"follower": follower.id}


def follower_view(follower):
    """A follower as GGG's ``Follower`` has it, in the realm's JSON."""
    return {"id": follower.id, "status": link_status(follower), "layers": follower.layers,
            "compliance": compliance.of(follower.compliance), "population": follower.population,
            "reported_at": follower.reported_at}


@op("realm.data_view", read=True, returns="followers")
def follower_list(c, args):
    """The realms that take legislation from this one, ``count`` at a time (default 100, at most 500) from row ``from``
    (default 1), each with the status of its link and what it last reported. Returns ``followers`` and ``next``."""
    rows, nxt = page(Follower, args)
    return {"followers": [follower_view(f) for f in rows], "next": nxt}


# ---- The published record


def release_of(row):
    """A release as the record lists it (§3.2): the entries of ``releases.json`` with their ``source``."""
    return view(row, "source", "version", "published", "effective_from", "commit", "chapters", "calendar", "notes")


def record():
    """The releases of the record in order: those of the layers the realm relays, top layer first, then its own, oldest
    first."""
    return sorted(each(Release, "relayed", True), key=lambda r: r.rank) + list(each(Release, "relayed", False))


def published_as():
    """The source id this realm publishes under, the one of its first release; None before it has published."""
    first = next(iter(each(Release, "relayed", False)), None)
    return first.source if first is not None else None


def brought_by_install():
    """The layer from above that the install in progress brings, if there is one and it has read its records."""
    job = store.live_job()
    vouching = ((job.staged.get("layers") or {}).get("vouching")) if job is not None else None
    return vouching["release"]["source"] if vouching else None


def check_from_above(source, version):
    """Refuse a release that this realm cannot keep as a layer from above: one with an ``@`` in its id, and one of the
    layer the realm publishes. The install task asks before it fetches anything, so that the switch cannot fail on it."""
    no_at(source, version)
    if source == published_as():
        raise Fail(f"{source} is the source id this realm publishes under: it is not a layer from above")


def valid_release(args):
    for key, most in (("source", NAME_MAX), ("version", VERSION_MAX), ("commit", VERSION_MAX)):
        if not isinstance(args.get(key), str) or not 0 < len(args[key]) <= most:
            raise Fail(f"{key} is 1 to {most} characters")
    no_at(args["source"], args["version"])
    chapters = args.get("chapters")
    if not (isinstance(chapters, list) and len(chapters) <= MOST_CHAPTERS and all(
            isinstance(c, dict) and set(c) == {"domain", "checksum"} and isinstance(c["domain"], str)
            and 0 < len(c["domain"]) <= NAME_MAX and pinned(c["checksum"]) for c in chapters)):
        raise Fail(f"chapters are at most {MOST_CHAPTERS} of {{domain, checksum: sha256:<64 hex digits>}}")
    if len({c["domain"] for c in chapters}) != len(chapters):
        raise Fail("a release names a domain once")
    if args.get("calendar") is not None and not pinned(args["calendar"]):
        raise Fail("calendar is sha256:<64 hex digits>")
    if len(json.dumps(args.get("notes") or {})) > NOTES_MAX:
        raise Fail(f"notes are at most {NOTES_MAX} characters")


@op("legislation.publish", governed=proposals.realm_wide, check=valid_release)
def legislation_release_publish(c, args):
    """Publish a release of the realm's own layer: its ``source`` id, ``version``, its ``chapters`` (each a ``domain``
    and the ``checksum`` of its text), the ``calendar``'s checksum, the ``commit`` it was built from, the time
    ``effective_from`` it is in force and ``notes`` by language. Every checksum is already stored by the realm,
    installed or pending (InvalidArgument), so a release can always be served. The realm publishes under one source id,
    the one of its first release, which is not a layer it relays (InvalidArgument), and a version once (Conflict): a
    release is never edited or removed, and a correction is a new version. A layer that an install in progress brings
    from above is refused too, so that the switch cannot fail on it."""
    source, version = args["source"], args["version"]
    if any(r.source == source for r in each(Release, "relayed", True)):
        raise Fail(f"{source} is a layer this realm relays: it publishes under its own source id")
    if source == brought_by_install():
        raise Fail(f"{source} is a layer an install in progress brings from above: it publishes under its own "
                   f"source id")
    own = published_as()
    if own is not None and own != source:
        raise Fail(f"this realm publishes as {own}")
    if Release[f"{source}@{version}"] is not None:
        raise Conflict(f"{source} {version} is published already")
    stored = [c["checksum"] for c in args["chapters"]] + ([args["calendar"]] if args.get("calendar") else [])
    held = next((s for s in stored if ChapterSource[s] is None), None)
    if held is not None:
        raise Fail(f"{held} is not stored by this realm: it cannot serve what it publishes")
    release = Release(id=f"{source}@{version}", source=source, version=version, published=chain.now(),
                      effective_from=args["effective_from"], commit=args["commit"],
                      chapters={c["domain"]: c["checksum"] for c in args["chapters"]},
                      calendar=args.get("calendar") or "", notes=args.get("notes") or {})
    return {"release": release.id}


def record_source():
    """The source id of the realm's own layer: the one its releases are published under, else the one its first layered
    codex set; None before either."""
    return published_as() or store.own_source()


@op(None, read=True, public=True, returns="releases")
def legislation_releases(c, args):
    """The realm's published record, ``count`` releases at a time (default 100, at most 500) from position ``from``
    (default 1): the releases of the layers it relays, top layer first, each as it installed it, then its own, oldest
    first. Each has its ``source``, ``version``, ``published`` and ``effective_from`` times, ``commit``, ``chapters`` (a
    domain to its checksum), ``calendar`` and ``notes``. Returns ``releases``, ``next`` and ``source``: the id of the
    realm's own layer, which tells a follower whether the record is its publisher's (None while the realm has none)."""
    start, count = window(args)
    rows = record()
    end = start - 1 + count
    return {"releases": [release_of(r) for r in rows[start - 1:end]], "next": end + 1 if end < len(rows) else None,
            "source": record_source()}


@op(None, read=True, public=True)
def legislation_chapter(c, args):
    """The text of the chapter, or calendar, stored under ``chapter`` (``sha256:<hex>``): NotFound when the realm holds
    none. The checksum pins the text, so it is as public as the legislation. Returns ``text``."""
    return {"text": find(ChapterSource, args["chapter"]).source}


def relay_release(release, order):
    """Keep ``release``, a release of a layer from above, as the record that vouches for it lists it (the install task
    calls this for each layer it installs). ``order`` is that record's sources, top layer first: the release takes the
    place of an earlier release of its layer, or joins after every relayed layer the record lists above it."""
    source = release["source"]
    if source not in order:
        raise Fail(f"the record that vouches for {source} does not list it")
    check_from_above(source, release["version"])
    relayed = [r for r in record() if r.relayed]
    for old in [r for r in relayed if r.source == source]:
        old.delete()
    kept = [r for r in relayed if r.source != source]
    new = Release(id=f"{source}@{release['version']}", relayed=True, source=source, version=release["version"],
                  published=release["published"], effective_from=release["effective_from"],
                  commit=release["commit"], chapters=release["chapters"], calendar=release.get("calendar") or "",
                  notes=release.get("notes") or {})
    above = [i for i, r in enumerate(kept, 1) if r.source in order and order.index(r.source) < order.index(source)]
    kept.insert(max(above, default=0), new)
    for rank, row in enumerate(kept, 1):
        row.rank = rank
