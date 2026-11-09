"""A change list (docs/codices/layered-codex.md §7) and the records that vouch for its chapters (§8): what the
install task checks before it fetches anything (``parse``, ``apply``) and once it has the records (``vouch``).

Plain data in, Fail out. Nothing here reads the database or calls a canister, so tests/ggg checks every rule
without a realm. ``layers_for`` turns the records the install task read (api.links) into what ``plan`` takes, and
``vouching`` says which record vouches for a release.
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
        data = json.loads(text, object_pairs_hook=no_repeats)
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
    both = sorted(set(names) & set(remove))
    if both:
        raise Fail(f"{both[0]} is both set and removed")
    summary = {} if data.get("summary") is None else data["summary"]
    if not isinstance(summary, dict) or not all(isinstance(v, str) for v in summary.values()):
        raise Fail('summary is {"<language>": "<text>"}')
    if not chapters and not remove and release is None:
        raise Fail("the change list changes nothing")
    return {"realm": realm, "replaces": replaces, "set": chapters, "remove": remove, "release": release,
            "summary": summary}


def no_repeats(pairs, what="the change list"):
    """A JSON object whose keys are all different: one that repeats a key reads as its last value here and as its
    first in a tool that stops at the first, so what voters were shown could differ from what is installed. ``what``
    names the document in the refusal."""
    found = {}
    for key, value in pairs:
        if key in found:
            raise Fail(f"{what} repeats the key {key!r}")
        found[key] = value
    return found


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


def source_name(source):
    """``source``, a source id such as ``eu/es/vc``; a Fail if it is not one."""
    errors = library().LegislationError
    from realms.legislation.codex import source_id

    try:
        return source_id(source)
    except errors as e:
        raise Fail(str(e))


def chapter_id(chapter):
    """``(source, domain)`` of the chapter id ``<source id>/<domain>``, the domain one of GGG's or ``calendar``."""
    source, _, domain = (chapter if isinstance(chapter, str) else "").rpartition("/")
    try:
        source_name(source)
    except Fail as e:
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


def vouch(change_list, realm, relays, own, vouchers):
    """Step 4 (§9.2): the change list against the layers the new codex will have (§8), ``relays`` in the shape ``plan``
    takes, with ``realm`` and ``own`` as ``apply`` returned them. ``vouchers`` says, for each layer from above, the
    source whose record vouches for it (``layers_for``).

    A release install must be whole: ``set`` holds exactly the release's chapters as the record lists it (a ``realm``
    chapter it lists is skipped, §3.2), with the record's checksums. Returns the ``Plan`` and, for every chapter and
    calendar of the new codex, the source that vouches for it; the realm's own are the realm's."""
    legislation = library()
    try:
        planned = legislation.plan(realm, relays, own)
    except legislation.LegislationError as e:
        raise Fail(str(e))
    if change_list["release"] is not None:
        _whole(change_list, realm, _record(relays, change_list["release"]))
    vouchers = {**vouchers, realm: realm}
    return planned, {chapter: vouchers[item["source"]] for chapter, item in named(planned)}


def sources_of(record):
    """The layers a record lists, top to bottom, each once: the layers it relays, then its own."""
    found = []
    for release in record["releases"]:
        if release["source"] not in found:
            found.append(release["source"])
    return found


def listed(record, source, version):
    """The record's release of layer ``source``: the one of ``version`` if it lists it, else the newest it lists, or
    None for a layer it does not list. A relayed layer has the one release the record relays."""
    found = [r for r in record["releases"] if r["source"] == source]
    return next((r for r in found if r["version"] == version), found[-1] if found else None)


def layers_for(installed, records, release):
    """What ``plan`` takes (§8) for an install that leaves the layers from above as they are and, for a ``release``
    ``{source, version}``, brings one: ``{relays, vouchers, vouching}``. ``installed`` are the layers from above the
    active codex runs, top to bottom, as it runs them (``{source, version, chapters, calendar, vouched_by}``);
    ``records`` the sync sources' records that were read, ``{key, source, releases}`` each.

    The layers keep the order the active codex runs them in, whatever a record says: a layer the realm runs takes the
    place of its earlier release, a new one goes right after the last layer the vouching record lists above it (or
    before the first it lists below it), and a record that puts two of the layers the other way round than the realm
    runs them is refused. They go to ``plan`` as one record, named by its last layer. Without a release nothing from
    outside is needed, and no record is read. A layer that no record lists (its link has ended) stays like the others."""
    layers = {layer["source"]: layer for layer in installed}
    vouchers = {layer["source"]: layer["vouched_by"] for layer in installed}
    order, found = list(layers), None
    if release is not None:
        found = vouching(records, release)
        order = _placed(order, release["source"], found)
        for record in records:
            _agrees(record, order)
        layers[release["source"]], vouchers[release["source"]] = found["release"], found["by"]
    relay = {"source": order[-1], "layers": [_plain(layers[source]) for source in order]} if order else None
    return {"relays": [relay] if relay else [], "vouchers": vouchers, "vouching": found}


def _plain(release):
    """A release as ``plan`` takes it (§8): its source, version, chapters and calendar."""
    return {key: release.get(key) for key in ("source", "version", "chapters", "calendar")}


def _placed(order, source, found):
    """``order``, the layers the realm runs, with the layer ``source`` the vouching record ``found`` lists: where it is
    already, or in the place that record gives it among the layers it lists."""
    if source in order:
        return order
    at = found["order"].index(source)
    above = [order.index(s) for s in found["order"][:at] if s in order]
    below = [order.index(s) for s in found["order"][at + 1:] if s in order]
    if order and not (above or below):
        raise Fail(f"the record of {found['by']} lists {source}, and none of the layers this realm runs: "
                   f"nothing places it among them")
    place = max(above) + 1 if above else min(below, default=0)
    return order[:place] + [source] + order[place:]


def _agrees(record, order):
    """Refuse a record that puts two of the layers in ``order`` the other way round than the realm runs them."""
    listed_here = [source for source in sources_of(record) if source in order]
    for at, above in enumerate(listed_here):
        for below in listed_here[at + 1:]:
            if order.index(above) > order.index(below):
                raise Fail(f"the record of {record['source']} puts {above} above {below}, and this realm runs "
                           f"{below} above {above}")


def vouching(records, release):
    """The record that vouches for ``release`` ``{source, version}``: its publisher's, when the realm syncs from the
    publisher directly, else the first record that lists it (the order the links come in). Returns ``{key, by,
    release, order}``: the sync source's key, its layer, the release as it lists it, and the layers it lists, top to
    bottom. Refused: a release no record lists, and two records that list different releases of its layer, which only
    the realm's own legislation may choose between (§9.2 step 4)."""
    source, version = release["source"], release["version"]
    holders = [(r, listed(r, source, version)) for r in records]
    holders = [(record, entry) for record, entry in holders if entry is not None]
    if not holders:
        raise Fail(f"no sync source relays {source}: a layer from above comes through a link")
    exact = [(record, entry) for record, entry in holders if entry["version"] == version]
    if not exact:
        record, entry = holders[0]
        raise Fail(f"the record of {record['source']} relays {source} {entry['version']}, not {version}")
    record, entry = next((held for held in exact if held[0]["source"] == source), exact[0])
    for other, theirs in holders:
        if _compared(theirs) != _compared(entry):
            differ = (f"{entry['version']} and {theirs['version']}" if entry["version"] != theirs["version"]
                      else f"{version} with different checksums")
            raise Fail(f"{source}: sync sources {record['source']} and {other['source']} relay different releases "
                       f"({differ}). The realm's own legislation chooses which one it installs; until it does, the "
                       f"install is refused (layered-codex.md §9.2 step 4)")
    return {"key": record["key"], "by": record["source"], "release": entry, "order": sources_of(record)}


def _compared(release):
    """What two records must agree on for a layer: ``_plain`` without the ``realm`` chapter, which no follower takes."""
    plain = _plain(release)
    return {**plain, "chapters": {d: sha for d, sha in plain["chapters"].items() if d != REALM}}


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
