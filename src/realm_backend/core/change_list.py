"""A change list (docs/codices/layered-codex.md §7) and the records that vouch for its chapters (§8): what the
install task checks before it fetches anything (``parse``, ``apply``) and once it has the records (``vouch``).

Plain data in, Fail out. Nothing here reads the database or calls a canister, so tests/ggg checks every rule
without a realm, and D6 plugs the sync sources' records in as the ``relays`` that ``plan`` takes.
"""

import json

from core.db import Fail
from core.layered_codex import MAX_CHAPTER

KIND, FORMAT = "realms-gos/legislation-change", 1
MAX_CHAPTERS = 50
MAX_MIRRORS = 4  # every URL tried is an outcall the realm pays for
CALENDAR = "calendar"  # a layer's calendar.json is set under <source>/calendar (§7)
REALM = "realm"  # the domain only the realm's own layer fills (§3)
# GGG's domain list, the ggg submodule's domains.json (tests/ggg keeps the two equal).
DOMAINS = ("realm", "tax", "procedure", "register", "local_regime", "water", "data_protection", "language",
           "treasury", "federation")
HEX = "0123456789abcdef"


def parse(text):
    """The change list in ``text``, checked for shape and limits: its ``realm``, ``replaces``, ``set`` (each entry
    with its ``chapter`` id, the ``source`` and ``domain`` in it, its ``urls``, the URL then its mirrors, its
    ``sha256`` as ``sha256:<hex>`` and its ``size``), ``remove``, ``release`` and ``summary``."""
    try:
        data = json.loads(text)
    except ValueError as e:
        raise Fail(f"the change list is not JSON: {e}")
    except RecursionError:
        raise Fail("the change list is nested too deep")
    if not isinstance(data, dict) or data.get("kind") != KIND or data.get("format") != FORMAT:
        raise Fail(f'the change list is not {{"kind": "{KIND}", "format": {FORMAT}}}')
    realm = data.get("realm")
    if not isinstance(realm, str) or not realm:
        raise Fail("realm names the realm's own source id")
    replaces = data.get("replaces")
    if replaces is not None and not isinstance(replaces, str):
        raise Fail("replaces is the active codex's checksum, or null")
    entries = data.get("set", [])
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise Fail("set is a list of chapters")
    if len(entries) > MAX_CHAPTERS:
        raise Fail(f"a change list sets at most {MAX_CHAPTERS} chapters, not {len(entries)}")
    chapters = [_entry(e) for e in entries]
    names = [c["chapter"] for c in chapters]
    if len(set(names)) != len(names):
        raise Fail("set names a chapter twice")
    remove = data.get("remove", [])
    if not isinstance(remove, list) or not all(isinstance(r, str) for r in remove):
        raise Fail("remove is a list of chapter ids")
    release = data.get("release")
    if release is not None and not (isinstance(release, dict) and isinstance(release.get("source"), str)
                                    and isinstance(release.get("version"), str)):
        raise Fail('release is {"source": <source id>, "version": <version>}, or null')
    if not chapters and not remove and release is None:
        raise Fail("the change list changes nothing")
    return {"realm": realm, "replaces": replaces, "set": chapters, "remove": remove, "release": release,
            "summary": data.get("summary") or {}}


def _entry(entry):
    chapter = entry.get("chapter")
    source, domain = chapter_id(chapter)
    mirrors = entry.get("mirrors") or []
    if not isinstance(mirrors, list):
        raise Fail(f"{chapter}: mirrors is a list of URLs")
    if len(mirrors) > MAX_MIRRORS:
        raise Fail(f"{chapter}: at most {MAX_MIRRORS} mirrors, not {len(mirrors)}")
    urls = [entry.get("url")] + mirrors
    for url in urls:
        if not isinstance(url, str) or not url.startswith("https://"):
            raise Fail(f"{chapter}: a chapter is fetched over https, not {url!r}")
    sha256 = entry.get("sha256")
    if not isinstance(sha256, str) or len(sha256) != 64 or any(ch not in HEX for ch in sha256):
        raise Fail(f"{chapter}: sha256 is 64 hex digits")
    size = entry.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_CHAPTER:
        raise Fail(f"{chapter}: size is between 1 and {MAX_CHAPTER} bytes ({MAX_CHAPTER // 1024} KB)")
    return {"chapter": chapter, "source": source, "domain": domain, "urls": urls, "sha256": "sha256:" + sha256,
            "size": size}


def library():
    """``realms.legislation``, loaded whole before any of its modules is used: in the realm, a bundled module runs
    when something first reads it, and the package's own import order is the one that resolves the imports between
    its modules (a submodule read first would run the package in the middle of its own load)."""
    import realms.legislation

    realms.legislation.plan  # noqa: B018  the read that runs the package
    return realms.legislation


def chapter_id(chapter):
    """``(source, domain)`` of the chapter id ``<source id>/<domain>``, the domain one of GGG's or ``calendar``."""
    errors = library().LegislationError
    from realms.legislation.codex import source_id

    source, _, domain = (chapter if isinstance(chapter, str) else "").rpartition("/")
    try:
        source_id(source)
    except errors as e:
        raise Fail(f"{chapter!r} is not a chapter id: {e}")
    if domain != CALENDAR and domain not in DOMAINS:
        raise Fail(f"{chapter!r} is not a chapter id: {domain!r} is not a GGG domain, nor calendar")
    return source, domain


def apply(change_list, realm, active_id, own):
    """Step 2 (§9.2): the change list against this realm. ``realm`` is the realm's own source id, or None before its
    first layered install, which sets it (the vote approved it); ``active_id`` the active codex's id, or None;
    ``own`` the realm's own layer in the active codex, ``{"chapters": {domain: sha256}, "calendar": sha256 or None}``.
    Returns the realm's source id and its own layer after the change, in the same shape."""
    if realm is None:
        realm = change_list["realm"]
    elif change_list["realm"] != realm:
        raise Fail(f"the change list is for {change_list['realm']}; this realm is {realm}")
    if change_list["replaces"] != active_id:
        raise Fail(f"stale change list: it replaces {change_list['replaces'] or 'no codex'}, "
                   f"the active codex is {active_id or 'none'}")
    release = change_list["release"]
    if release is not None and release["source"] == realm:
        raise Fail("the realm's own layer has no releases: its proposals are its record")
    if release is not None and change_list["remove"]:
        raise Fail("a release install changes nothing else: remove is empty")
    for entry in change_list["set"]:
        if entry["source"] == realm:
            continue
        if release is None:
            raise Fail(f"{entry['chapter']} is a layer from above: it is installed whole, as a release")
        if entry["source"] != release["source"]:
            raise Fail(f"{entry['chapter']} is not part of the release of {release['source']}")
    chapters, calendar = dict(own.get("chapters") or {}), own.get("calendar")
    for chapter in change_list["remove"]:
        source, domain = chapter_id(chapter)
        if source != realm:
            raise Fail(f"remove {chapter}: only the realm's own layer, {realm}, may be emptied")
        if domain == CALENDAR and calendar:
            calendar = None
        elif domain in chapters:
            del chapters[domain]
        else:
            raise Fail(f"remove {chapter}: the codex has no such chapter")
    for entry in change_list["set"]:
        if entry["source"] == realm:
            if entry["domain"] == CALENDAR:
                calendar = entry["sha256"]
            else:
                chapters[entry["domain"]] = entry["sha256"]
    return realm, {"chapters": chapters, "calendar": calendar}


def vouch(change_list, realm, relays, own):
    """Step 4 (§9.2): the change list against the records of the sync sources (§8), ``relays`` in the shape
    ``plan`` takes, with ``realm`` and ``own`` as ``apply`` returned them.

    ``plan`` refuses paths that are not one chain and two sync sources that relay different releases of one layer.
    A release install must be whole: ``set`` holds exactly the release's chapters as the record lists them (a
    ``realm`` chapter it lists is skipped, §3.2), with the record's checksums. Returns the ``Plan`` and, for every
    chapter and calendar of the new codex, the source whose record vouches for it: its publisher's, when the realm
    syncs from the publisher directly, else the sync source that relays it; the realm's own are the realm's."""
    legislation = library()
    try:
        planned = legislation.plan(realm, relays, own)
    except legislation.LegislationError as e:
        raise Fail(str(e))
    vouchers = {realm: realm}
    for relay in relays:
        for release in relay["layers"]:
            if release["source"] == relay["source"] or release["source"] not in vouchers:
                vouchers[release["source"]] = relay["source"]
    if change_list["release"] is not None:
        _whole(change_list, realm, _record(relays, change_list["release"]))
    vouched_by = {chapter: vouchers[item["source"]] for chapter, item in named(planned)}
    return planned, vouched_by


def named(planned):
    """``(chapter id, plan entry)`` of every chapter and calendar of ``planned``, chapters first."""
    return [(f"{c['source']}/{c['domain']}", c) for c in planned.chapters] + [
        (f"{c['source']}/{CALENDAR}", c) for c in planned.calendars]


def _record(relays, release):
    """The release as the record that relays its source lists it (every record agrees, or ``plan`` refused)."""
    for relay in relays:
        for listed in relay["layers"]:
            if listed["source"] == release["source"]:
                if listed["version"] != release["version"]:
                    raise Fail(f"the record of {relay['source']} relays {release['source']} {listed['version']}, "
                               f"not {release['version']}")
                return listed
    raise Fail(f"no sync source relays {release['source']}: a layer from above comes through a link")


def _whole(change_list, realm, listed):
    source = listed["source"]
    expected = {f"{source}/{d}": sha for d, sha in listed["chapters"].items() if d != REALM}
    if listed.get("calendar"):
        expected[f"{source}/{CALENDAR}"] = listed["calendar"]
    given = {e["chapter"]: e["sha256"] for e in change_list["set"] if e["source"] != realm and e["domain"] != REALM}
    if set(given) != set(expected):
        raise Fail(f"a release is installed whole: {source} {listed['version']} is "
                   f"{', '.join(sorted(expected)) or 'no chapters'}; the change list sets "
                   f"{', '.join(sorted(given)) or 'none of them'}")
    for chapter in sorted(expected):
        if given[chapter] != expected[chapter]:
            raise Fail(f"{chapter}: the record says {expected[chapter]}, the change list {given[chapter]}")
