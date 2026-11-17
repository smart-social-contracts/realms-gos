"""Layer paths from the links (layered-codex.md §3.3, §8, §9.2 step 5).

A realm's layer path in a domain is the path its sync sources relay for that domain, followed by its own
layer. ``plan`` works that out from plain data and applies the one-chain rule. It calls no canister and
reads no file, so the install task, ``realms legislation resolve`` and the tests run the same code.

What goes in, for each chosen sync source (a link both sides have voted), is its published record (§8)
as the releases it runs, top to bottom: the layers it relays and its own layer last::

    {"source": "eu/es/vc", "layers": [
        {"source": "eu/es", "version": "2.4.0", "chapters": {"tax": "sha256:..."}, "calendar": "sha256:..."},
        {"source": "eu/es/vc", "version": "1.1.0", "chapters": {"procedure": "sha256:..."}, "calendar": None}]}

and the realm's own layer, which needs no release (§3.2): ``{"chapters": {domain: sha256}, "calendar": sha256}``.
A release never lists the ``realm`` domain, which only the realm's own layer fills (§3); a relayed one is
skipped and reported in ``Plan.skipped``.

A layer is on a domain's path only if its release has a chapter in that domain: empty chapters are
skipped, so València's tax path is Spain → València (§3.3). The paths the sync sources relay in one
domain must form one chain, each the start of the longest, which the realm uses. Paths that differ are
refused unless one extends the other, and the refusal is of the whole install (§2, rule 4). The
layers the sync sources relay must also form one chain across domains: taken together, their records
must put every layer with a chapter or a calendar above or below every other, with no ties.
"""

from realms.legislation.codex import domain_id, source_id
from realms.legislation.errors import LegislationError

ARROW = " → "
REALM = "realm"  # the domain only the realm's own layer fills (§3)


class Plan:
    """What the install needs before it downloads or loads anything.

    ``layers``: every layer the realm runs, top to bottom, its own last. ``paths``: for each domain, the
    sources that have a chapter in it, top to bottom. ``chapters``: the chapters on those paths to
    fetch, ``{source, domain, version, sha256}`` for each, a domain at a time, top to bottom (the order
    ``Codex.load`` needs). ``calendars``: ``{source, version, sha256}`` for each layer that has one. The
    realm's own layer has the version ``None``. ``skipped``: the ``realm`` chapters that relayed releases
    listed and ``plan`` left out, ``{source, domain, sha256}`` each.
    """

    def __init__(self, realm, layers, paths, chapters, calendars, skipped):
        self.realm, self.layers, self.paths = realm, layers, paths
        self.chapters, self.calendars, self.skipped = chapters, calendars, skipped


def plan(realm, relays, own=None):
    """The ``Plan`` of the realm ``realm`` (its source id) that syncs from ``relays`` and has ``own``."""
    realm = source_id(realm)
    relays = [_relay(relay, realm) for relay in relays]
    skipped = []
    for relay in relays:
        skipped += [s for s in relay["skipped"] if s not in skipped]
    releases = _agreed(relays)
    releases[realm] = _own(realm, own)
    paths = {}
    for domain in sorted({d for r in releases.values() for d in r["chapters"]}):
        relayed = [
            (
                relay["source"],
                [r["source"] for r in relay["layers"] if domain in r["chapters"]],
            )
            for relay in relays
        ]
        paths[domain] = _chain(domain, relayed)
        if domain in releases[realm]["chapters"]:
            paths[domain].append(realm)
    layers = _order(relays) + [realm]
    chapters = [
        {
            "source": source,
            "domain": domain,
            "version": releases[source]["version"],
            "sha256": releases[source]["chapters"][domain],
        }
        for domain in paths
        for source in paths[domain]
    ]
    calendars = [
        {
            "source": source,
            "version": releases[source]["version"],
            "sha256": releases[source]["calendar"],
        }
        for source in layers
        if releases[source]["calendar"]
    ]
    return Plan(realm, layers, paths, chapters, calendars, skipped)


def _relay(relay, realm):
    """A sync source's record, checked: its releases, each layer once, never this realm's own layer, and
    the source's own layer, if it lists one, last. The ``realm`` chapters it lists are set aside.
    """
    if not isinstance(relay, dict) or not isinstance(relay.get("layers"), list):
        raise LegislationError(
            'a sync source is {"source": <source id>, "layers": [<release>, ...]}'
        )
    name = source_id(relay.get("source"))
    if name == realm:
        raise LegislationError(
            f"sync source {name} is this realm itself: a realm cannot sync from itself, directly or "
            "through other realms"
        )
    layers, skipped = [], []
    for entry in relay["layers"]:
        release = _release(entry, f"sync source {name}", versioned=True)
        if release["source"] == realm:
            raise LegislationError(
                f"sync source {name} relays {realm}, this realm's own layer: a realm cannot sync from "
                "itself, directly or through other realms"
            )
        if release["source"] in [r["source"] for r in layers]:
            raise LegislationError(
                f"sync source {name} relays {release['source']} twice"
            )
        if REALM in release["chapters"]:
            sha256 = release["chapters"].pop(REALM)
            skipped.append(
                {"source": release["source"], "domain": REALM, "sha256": sha256}
            )
        layers.append(release)
    sources = [r["source"] for r in layers]
    if name in sources and sources[-1] != name:
        raise LegislationError(
            f"sync source {name} lists its own layer before the end: it comes last, below the "
            "layers it relays"
        )
    return {"source": name, "layers": layers, "skipped": skipped}


def _own(realm, own):
    """The realm's own layer, checked, in the shape of a release that has no version."""
    own = {} if own is None else own
    if not isinstance(own, dict) or set(own) - {"chapters", "calendar"}:
        raise LegislationError(
            'the realm\'s own layer is {"chapters": {domain: sha256}, "calendar": sha256}, with no '
            "version (layered-codex.md §3.2)"
        )
    return _release(dict(own, source=realm), "the realm's own layer")


def _release(entry, where, versioned=False):
    """A release (or the realm's own layer) checked: source id, version, chapters by domain, calendar."""
    if not isinstance(entry, dict):
        raise LegislationError(f"{where}: a release is an object")
    source = source_id(entry.get("source"))
    version = entry.get("version")
    if versioned and (not isinstance(version, str) or not version):
        raise LegislationError(f"{where}: {source} has no version")
    chapters = entry.get("chapters", {})
    if not isinstance(chapters, dict):
        raise LegislationError(f"{where}: {source}'s chapters are {{domain: sha256}}")
    for domain, checksum in chapters.items():
        domain_id(domain)
        _checksum(checksum, f"{where}: {source}/{domain}")
    calendar = entry.get("calendar")
    if calendar is not None:
        _checksum(calendar, f"{where}: {source}/calendar")
    return {
        "source": source,
        "version": version,
        "chapters": dict(chapters),
        "calendar": calendar,
    }


def _checksum(value, where):
    if not isinstance(value, str) or not value:
        raise LegislationError(f"{where}: a checksum is text such as sha256:<hex>")


def _agreed(relays):
    """One release per source. A layer that two sync sources both relay must be the same release."""
    agreed = {}
    for relay in relays:
        for release in relay["layers"]:
            seen, seen_by = agreed.setdefault(
                release["source"], (release, relay["source"])
            )
            if seen != release:
                differ = (
                    f"{seen['version']} and {release['version']}"
                    if seen["version"] != release["version"]
                    else f"{release['version']} with different checksums"
                )
                raise LegislationError(
                    f"{release['source']}: sync sources {seen_by} and {relay['source']} relay "
                    f"different releases ({differ}). The realm's own legislation chooses which one it "
                    "installs; until it does, the install is refused (layered-codex.md §9.2 step 4)"
                )
    return {source: release for source, (release, _) in agreed.items()}


def _chain(domain, relayed):
    """The longest of the paths the sync sources relay in ``domain``, if each other one is the start of
    it; otherwise a refusal naming the two sync sources."""
    longest_by, longest = max(
        relayed, key=lambda named: len(named[1]), default=(None, [])
    )
    for name, path in relayed:
        if path != longest[: len(path)]:
            raise LegislationError(
                f"{domain}: sync source {longest_by} relays {_arrow(longest)} and sync source {name} "
                f"relays {_arrow(path)}. Paths that differ are refused unless one extends the other "
                "(layered-codex.md §3.3)"
            )
    return list(longest)


def _arrow(path):
    return ARROW.join(path) or "no layers"


def _order(relays):
    """Every layer the sync sources relay, top to bottom, in the one order their records put them in:
    taken together, the records must put every layer above or below every other, with no ties (§3.3), so
    ``self.layer`` can say which layer is above which across domains. Refused: records that contradict
    each other, and layers they leave with neither above the other."""
    runs = [
        [r["source"] for r in relay["layers"] if r["chapters"] or r["calendar"]]
        for relay in relays
    ]
    before = {}
    for run in runs:
        for position, source in enumerate(run):
            before.setdefault(source, set())
            if position:
                before[source].add(run[position - 1])
    order = []
    while len(order) < len(before):
        ready = [s for s in before if s not in order and before[s] <= set(order)]
        if not ready:
            raise LegislationError(
                "the sync sources put these layers in contradicting orders: "
                f"{ARROW.join(_circle(before, order))}"
            )
        if len(ready) > 1:
            raise LegislationError(
                f"the sync sources' records leave {' and '.join(ready)} with neither above the other: "
                "the layers a realm runs form one chain (layered-codex.md §3.3)"
            )
        order.append(ready[0])
    return order


def _circle(before, placed):
    """Layers that follow each other round in a circle, ``a → b → a``: walk back from a layer that
    cannot be placed, through a layer that must come before it, until one repeats."""
    path = [next(s for s in before if s not in placed)]
    while True:
        previous = sorted(s for s in before[path[-1]] if s not in placed)[0]
        if previous in path:
            circle = path[path.index(previous) :][::-1]
            return circle + circle[:1]
        path.append(previous)
