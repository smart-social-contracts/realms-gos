"""The published records of the sync sources, read (docs/codices/layered-codex.md §8, §11).

A realm's record is what its capital answers to ``legislation_releases``; a stand-in record, for a source that runs no
realm, is a JSON file at its url, ``{"source": <its source id>, "releases": [...]}`` with the same releases, pinned by
the checksum of its bytes. Both are checked for their shape and size and kept with the time they were read
(``SourceRecord``): the install task reads them to vouch for a release (§9.2), the compliance check on its schedule
(§11). The checks of what a release looks like (``pinned``, ``no_at`` and the limits) are shared with the operation
that publishes one (api.links).
"""

import json

from core import chain
from core import change_list
from core import layered_codex as store
from core.db import Conflict, Fail, each
from ggg import SourceRecord, SyncSource
from ggg.governance.links import RECORD_MAX

VERSION_MAX = 64
MOST_CHAPTERS = 64
MOST_LAYERS = 16  # in a record, and in a follower's report: a chain is at most 8 deep (§4.4)
NOTES_MAX = 8 * 1024  # characters of a release's notes, as JSON
# GGG's typed methods carry a release's times in nanoseconds, as a nat64: a record is refused for a time they cannot
# encode, since the typed ``ggg_legislation_releases`` would trap for every reader.
TIME_MAX = (2**64 - 1) // 1_000_000_000
READ_PAGE = 100  # releases asked of a capital at a time
MOST_RELEASES = 1000  # in a record read from a sync source
RELEASE_FIELDS = {"source", "version", "published", "effective_from", "commit", "chapters", "calendar", "notes"}
HEX = "0123456789abcdef"


def is_realm_key(key):
    """Whether ``key`` names a realm, by its capital, not a stand-in record, which is kept under its url."""
    return not key.startswith("https://")


def pinned(checksum):
    return isinstance(checksum, str) and checksum.startswith("sha256:") and len(checksum) == 71 \
        and all(ch in HEX for ch in checksum[7:])


def no_at(source, version):
    """A release is kept under ``<source>@<version>``: with an ``@`` in either part, two releases could share an id."""
    if "@" in source or "@" in version:
        raise Fail("a release's source and version hold no '@'")


def read_record(key):
    """The record of the active sync source ``key``, read now, checked and kept. Returns ``{key, source, releases,
    checked_at}``: ``source`` is the id of the record's own layer (None when it has none yet) and ``releases`` the
    layers it relays, top to bottom, then its own, oldest first. A source that cannot be read, or whose record is not
    well formed, is a Fail naming it. A generator."""
    link = SyncSource[key]
    if link is None or link.status != "active":
        raise Conflict(f"{key} is not an active sync source")
    if is_realm_key(link.id):
        source, releases = yield from releases_from_capital(key)
    else:
        source, releases = yield from releases_from_stand_in(key, link.checksum)
    record = checked_record(key, source, releases)
    keep_record(record)
    return record


def read_active_records():
    """The record of every active sync source, in the order of their links. A generator."""
    found = []
    for link in each(SyncSource, "status", "active"):
        found.append((yield from read_record(link.id)))
    return found


def ask_realm(key, method, args):
    """``method`` of the realm whose capital is ``key``, answered: a rejected call or a refusal is a Fail naming it. A
    generator."""
    try:
        reply = json.loads((yield from chain.call_op(key, method, args)) or '{"ok": false, "error": "no reply"}')
    except Exception as e:  # noqa: BLE001  rejected: the realm is stopped, gone, or has no such method
        raise Fail(f"{key}: {e}")
    if not isinstance(reply, dict) or not reply.get("ok"):
        error = reply.get("error") if isinstance(reply, dict) else None
        raise Fail(f"{key}: {error or 'no reply'}")
    return reply


def releases_from_capital(key):
    """``(source, releases)`` as the capital ``key`` lists them, a page at a time. A generator."""
    source, releases, start, size = None, [], 1, 0
    for page_number in range(MOST_RELEASES // READ_PAGE + 1):
        reply = yield from ask_realm(key, "legislation_releases", {"from": start, "count": READ_PAGE})
        page, start = reply.get("releases"), reply.get("next")
        if not isinstance(page, list) or not (start is None or type(start) is int):
            raise Fail(f"{key}: legislation_releases answers its releases and where the next page starts")
        if page_number == 0:
            source = reply.get("source")
        releases += page
        size += len(json.dumps(page))
        if len(releases) > MOST_RELEASES:
            break
        if size > RECORD_MAX:
            raise Fail(f"{key}: a record is at most {RECORD_MAX // 1024} KB")
        if start is None:
            return source, releases
    raise Fail(f"{key}: a record lists at most {MOST_RELEASES} releases")


def releases_from_stand_in(url, pin):
    """``(source, releases)`` of the stand-in record at ``url``, whose bytes must be the ``pin``. A generator."""
    status, body = yield from store.fetched(url, RECORD_MAX)
    text = store.decoded(url, status, body, RECORD_MAX, is_json=True)
    found = store.checksum(text)
    if found != pin:
        raise Fail(f"{url}: the record is {found}, the pin is {pin}")
    try:
        data = json.loads(text, object_pairs_hook=lambda pairs: change_list.no_repeats(pairs, f"the record at {url}"))
    except (ValueError, RecursionError):
        raise Fail(f"{url}: the record is not JSON")
    if not (isinstance(data, dict) and set(data) == {"source", "releases"}):
        raise Fail(f'{url}: a stand-in record is {{"source": <source id>, "releases": [...]}}')
    return data["source"], data["releases"]


def checked_record(key, source, releases):
    """The record of ``key`` whose own layer is ``source`` (None when it has none), with every release checked: its
    shape, and the layout §8 gives a record: the layers it relays once each, top to bottom, then its own layer,
    each of its versions once."""
    if source is not None:
        change_list.source_name(source)
    if not isinstance(releases, list) or len(releases) > MOST_RELEASES:
        raise Fail(f"{key}: a record lists at most {MOST_RELEASES} releases")
    entries = [checked_release(entry, key) for entry in releases]
    if entries and source is None:
        raise Fail(f"{key}: a record that lists releases names the source id of its own layer")
    relayed = [entry["source"] for entry in entries if entry["source"] != source]
    own = [entry["version"] for entry in entries if entry["source"] == source]
    if [entry["source"] for entry in entries] != relayed + [source] * len(own):
        raise Fail(f"{key}: a record lists its own layer last, below the layers it relays")
    if len(set(relayed)) != len(relayed) or len(set(own)) != len(own):
        raise Fail(f"{key}: a record lists a layer relayed twice, or a version of its own layer twice")
    if len(relayed) + bool(own) > MOST_LAYERS:
        raise Fail(f"{key}: a record lists at most {MOST_LAYERS} layers")
    if len(json.dumps(entries)) > RECORD_MAX:
        raise Fail(f"{key}: a record is at most {RECORD_MAX // 1024} KB")
    return {"key": key, "source": source, "releases": entries, "checked_at": chain.now()}


def checked_release(entry, key):
    """One release of a record, checked and copied: the fields ``legislation_releases`` gives (§3.2), with no calendar
    as None."""
    if not (isinstance(entry, dict) and set(entry) == RELEASE_FIELDS):
        raise Fail(f"{key}: a release has {', '.join(sorted(RELEASE_FIELDS))}")
    source = change_list.source_name(entry["source"])
    for field in ("version", "commit"):
        if not isinstance(entry[field], str) or not 0 < len(entry[field]) <= VERSION_MAX:
            raise Fail(f"{key}: {source} {field} is 1 to {VERSION_MAX} characters")
    no_at(source, entry["version"])
    for field in ("published", "effective_from"):
        if type(entry[field]) is not int or not 0 <= entry[field] <= TIME_MAX:
            raise Fail(f"{key}: {source} {field} is a time in seconds, from 0 to {TIME_MAX}")
    chapters = entry["chapters"]
    if not isinstance(chapters, dict) or len(chapters) > MOST_CHAPTERS:
        raise Fail(f"{key}: {source} lists at most {MOST_CHAPTERS} chapters, as {{domain: sha256:<hex>}}")
    for domain, checksum in chapters.items():
        if domain == change_list.CALENDAR:
            raise Fail(f"{key}: {source} lists its calendar as calendar, not among its chapters")
        if domain not in change_list.DOMAINS:
            raise Fail(f"{key}: {source}/{domain}: {domain!r} is not a GGG domain")
        if not pinned(checksum):
            raise Fail(f"{key}: {source}/{domain} is sha256:<64 hex digits>")
    calendar = entry["calendar"] or None
    if calendar is not None and not pinned(calendar):
        raise Fail(f"{key}: {source} calendar is sha256:<64 hex digits>")
    notes = entry["notes"]
    if not isinstance(notes, dict) or len(json.dumps(notes)) > NOTES_MAX:
        raise Fail(f"{key}: {source} notes are at most {NOTES_MAX} characters")
    return {**entry, "source": source, "chapters": dict(chapters), "calendar": calendar, "notes": dict(notes)}


def keep_record(record):
    """Store ``record`` as the last one read from its sync source."""
    row = SourceRecord[record["key"]] or SourceRecord(id=record["key"])
    row.source, row.releases, row.checked_at = record["source"] or "", record["releases"], record["checked_at"]
