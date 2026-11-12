"""The scheduled check of the sync sources (docs/codices/layered-codex.md §11.2, §11.6, §11.8).

From its first sync source on, the realm reads the record of each active one (core.records), one query per source,
every ``interval`` hours: 24 until the admins set another with ``codex_check_config``, and ``codex_check_now`` runs the
check at once. For each source it keeps on the link what it found: the realm's compliance against that record (core.
compliance), when, and the releases it could install from it. It logs the releases no check had found before
(``FoundRelease``), for the official journal, and reports to the capital of each realm it syncs from what it runs, a
``ReportedLayer`` per layer from above, its compliance against that source and its population. Each source is checked
on its own: one whose check fails, a record that cannot be read or is refused among the reasons, is ``unknown`` with
the reason, and the others are checked all the same. After every install and revert the flags are judged again from
the records kept.

Only the capital checks, as a quarter calls no other realm. Both operations are the admins' and reserved (§10.4): no
chapter hooks, guards or forbids them.
"""

from api import codex, installs, links, quarters
from core import chain, change_list, compliance, journal, records
from core import layered_codex as store
from core.db import Fail, each, first, page, view
from core.op import op
from ggg import FoundRelease, Realm, SourceRecord, SyncSource

HOUR = 3600
DEFAULT_INTERVAL, LEAST, MOST = 24, 1, 7 * 24  # hours between two checks
FOUND = ("source", "version", "published", "effective_from", "notes", "found_in", "found_at")


# ---- When the realm checks


def settings():
    """``{interval, due}``, as the admins set them and the last check scheduled them, kept with the legislation."""
    return ((Realm["1"].manifest_data or {}).get("legislation") or {}).get("check") or {}


def remember(**values):
    row = Realm["1"]
    data = row.manifest_data or {}
    kept = data.get("legislation") or {}
    data["legislation"] = {**kept, "check": {**(kept.get("check") or {}), **values}}
    row.manifest_data = data


def interval():
    return settings().get("interval") or DEFAULT_INTERVAL


def schedule_view():
    return {"interval": interval(), "due": settings().get("due") or 0}


def schedule():
    """The next check, ``interval`` hours from now. One set before finds itself replaced when its time comes and does
    nothing, so the realm has one chain of checks."""
    seconds = interval() * HOUR
    due = chain.now() + seconds
    remember(due=due)
    chain.set_timer(seconds, lambda: scheduled(due))


def scheduled(due):
    """The check set for ``due``, unless another has replaced it. A generator."""
    if settings().get("due") == due:
        yield from check_and_schedule()


def check_and_schedule():
    """The next check scheduled first, so that one that fails leaves it set, then this one. A generator."""
    schedule()
    yield from check()


def arm():
    """The realm checks its sync sources from its first one on (api.links.sync_source_add)."""
    if not settings().get("due"):
        schedule()


def resume():
    """Timers do not survive an upgrade: the next check is set again for its time, at once if that has passed. A realm
    that had sync sources before it had checks starts them now."""
    due = settings().get("due")
    if due:
        chain.set_timer(max(due - chain.now(), 0), lambda: scheduled(due))
    elif first(SyncSource, "status", "active") is not None:
        schedule()


def valid_interval(args):
    hours = args.get("interval")
    if type(hours) is not int or not LEAST <= hours <= MOST:
        raise Fail(f"interval is a whole number of hours, from {LEAST} to {MOST}")


@op("realm.configure", check=valid_interval)
def codex_check_config(c, args):
    """Check the sync sources every ``interval`` hours (1 to 168; 24 until set), the next check that many hours from
    now; before the realm's first sync source the interval is only kept. The admins' setting, outside legislation: no
    chapter hooks, guards or forbids it. Returns ``interval`` and ``due``."""
    links.only_capital()
    remember(interval=args["interval"])
    if settings().get("due"):
        schedule()
    return schedule_view()


@op("realm.configure")
def codex_check_now(c, args):
    """Check the sync sources now, in the next message, and every ``interval`` hours from then. The admins' operation,
    outside legislation: no chapter hooks, guards or forbids it. ``codex_status`` shows what the check found."""
    links.only_capital()
    chain.set_timer(0, check_and_schedule)
    return {}


# ---- The check


def check():
    """Every active sync source checked, one after the other. A generator."""
    if quarters.federated():
        return
    for link in list(each(SyncSource, "status", "active")):
        try:
            yield from check_source(link.id)
        except Exception as e:  # noqa: BLE001  whatever fails for one source, the others are still checked
            failed(link.id, e)


def check_source(key):
    """Read the record of sync source ``key`` and keep what it says; report to its capital if it is a realm. A record
    that cannot be read, or is refused, raises Fail. A generator."""
    record = yield from records.read_record(key)
    link = SyncSource[key]
    link.checked_at, link.check_error = chain.now(), ""
    keep_found(record)
    judge(link, record)
    if links.is_realm(link):
        yield from report(key, compliance.of(link.compliance))


def failed(key, error):
    """The check of sync source ``key`` failed with ``error``: the realm's flag against it is ``unknown``, and why."""
    link = SyncSource[key]
    link.checked_at, link.compliance, link.check_error = chain.now(), compliance.valid("unknown"), str(error)[:300]


def layers_run():
    """The layers from above that the active codex runs, top to bottom (api.installs.installed_layers)."""
    return installs.installed_layers(codex.active(), store.own_source())


def judge(link, record):
    """The realm's flag against ``record``, the record of ``link``'s sync source, and what it could install from it."""
    running = {layer["source"]: layer for layer in layers_run()}
    link.compliance = compliance.valid(compliance.judged(record, running, chain.now()))
    link.installable = compliance.installable(record, running)
    link.notes = compliance.notes(record, running)
    link.layer = record.get("source") or ""


def keep_found(record):
    """Log the releases of ``record`` that no check has found before, with the time it was read, and report each in the
    official journal (§9.6)."""
    for release in record["releases"]:
        key = f"{release['source']}@{release['version']}"
        if FoundRelease[key] is None:
            found = FoundRelease(id=key, source=release["source"], version=release["version"],
                                 published=release["published"], effective_from=release["effective_from"],
                                 notes=release["notes"], found_in=record["key"], found_at=record["checked_at"])
            journal.release_found(found)


def population():
    """The realm's members: the capital's own, and those each of its quarters last reported."""
    return quarters.population() + sum(q.population for q in quarters.known())


def report(key, flag):
    """Tell the capital ``key`` what this realm runs, a ``ReportedLayer`` per layer from above, its ``flag`` against
    that source and its population (§11.8). A source that refuses it, such as one that has not accepted this realm,
    or does not answer, is told at the next check. A generator."""
    layers = [{"version": layer["version"], "checksum": change_list.layer_checksum(layer)} for layer in layers_run()]
    try:
        yield from records.ask_realm(key, "follower_report",
                                     {"layers": layers, "compliance": flag, "population": population()})
    except Fail:
        pass


def rejudge():
    """What the realm runs has changed, by an install or a revert (api.codex): its flag against each sync source is
    judged again from the record the last check kept, one source per message, as a record can be hundreds of
    kilobytes. A source whose record could not be read then stays ``unknown`` until a check reads it."""
    for link in each(SyncSource, "status", "active"):
        if compliance.of(link.compliance) != "unknown":
            chain.set_timer(0, lambda key=link.id: rejudge_source(key))


def rejudge_source(key):
    link, kept = SyncSource[key], SourceRecord[key]
    if link is not None and kept is not None and compliance.of(link.compliance) != "unknown":
        judge(link, {"source": kept.source, "releases": kept.releases})


# ---- What the checks found


def found():
    """What the last check found for each active sync source, as ``codex_status`` shows it."""
    return [{"source": link.id, "name": link.name, "compliance": compliance.of(link.compliance),
             "layer": link.layer, "checked_at": link.checked_at, "error": link.check_error,
             "installable": link.installable, "notes": link.notes}
            for link in each(SyncSource, "status", "active")]


@op(None, read=True, public=True, returns="releases")
def codex_releases_found(c, args):
    """The releases the checks of the sync sources have found, in the order found, ``count`` at a time (default 100, at
    most 500) from row ``from`` (default 1): each with its ``source``, ``version``, ``published`` and ``effective_from``
    times and ``notes``, as the record listed it, the sync source whose record listed it first (``found_in``) and when
    (``found_at``). What the official journal reports (§9.6). Returns ``releases`` and ``next``."""
    rows, nxt = page(FoundRelease, args)
    return {"releases": [view(row, *FOUND) for row in rows], "next": nxt}
