"""The compliance flag of a follower or a quarter (docs/codices/layered-codex.md §11), and GGG's
``follower_compliance`` fact of the operations between realms.

A flag is ``"compliant"``, ``"unknown"`` or ``{"NonCompliant": {"since": <seconds>, "stale": [<source id>, ...]}}``,
the form GGG's variant takes in the realm's JSON (ggg_api.to_realms). A row keeps it as JSON text, so that a quarter
stored before the flag existed reads ``"unknown"`` through the field's default.
"""

import json

from core import chain
from core.db import Fail

MOST_STALE = 16  # layers a flag names: a chain is at most 8 deep (§4.4)
SOURCE_MAX = 200  # characters of a source id


def valid(value):
    """The flag ``value`` as the JSON text a row keeps, or Fail if it is none."""
    if value in ("compliant", "unknown"):
        return json.dumps(value)
    behind = value.get("NonCompliant") if isinstance(value, dict) and len(value) == 1 else None
    if isinstance(behind, dict) and set(behind) == {"since", "stale"} and type(behind["since"]) is int:
        stale = behind["stale"]
        if (isinstance(stale, list) and len(stale) <= MOST_STALE
                and all(isinstance(s, str) and 0 < len(s) <= SOURCE_MAX for s in stale)):
            return json.dumps(value, sort_keys=True)
    raise Fail('compliance is "compliant", "unknown" or {"NonCompliant": {"since": <seconds>, "stale": [<source id>]}}')


def of(text):
    """The flag a row keeps as ``text``."""
    return json.loads(text or '"unknown"')


def by_layer(record):
    """The releases a sync source's ``record`` lists, per layer: ``{source: [release, ...]}``, oldest first."""
    layers = {}
    for release in record["releases"]:
        layers.setdefault(release["source"], []).append(release)
    return layers


def missed(releases, running):
    """The releases of one layer, oldest first, that come after ``running``, the version the realm runs (None when it
    runs none): all of them when the record does not list it."""
    versions = [release["version"] for release in releases]
    return releases[versions.index(running) + 1:] if running in versions else releases


def judged(record, running, now):
    """The flag of a realm that runs ``running`` (``{source: version}``, its layers from above) against the ``record``
    of one of its sync sources, at ``now`` (§11.6): compliant while every layer the record lists is at least the latest
    release of it in effect; else non-compliant since the earliest release in effect it misses, naming the stale
    layers. It depends on the record and on what the realm runs, nothing else: a source's own releases are never
    removed, and a relayed layer is listed as the source runs it."""
    stale, since = [], []
    for source, releases in by_layer(record).items():
        in_effect = [r["effective_from"] for r in missed(releases, running.get(source)) if r["effective_from"] <= now]
        if in_effect:
            stale.append(source)
            since.append(min(in_effect))
    return {"NonCompliant": {"since": min(since), "stale": stale}} if stale else "compliant"


def installable(record, running):
    """The newest release of each layer the ``record`` lists that the realm, running ``running``, has not installed:
    what "Propose install" offers (§11.3), each as ``{source, version, effective_from}``."""
    newest = [missed(releases, running.get(source))[-1:] for source, releases in by_layer(record).items()]
    return [{key: r[key] for key in ("source", "version", "effective_from")} for found in newest for r in found]


def tag(value):
    """The case of GGG's ``Compliance`` a flag is, the value of the ``follower_compliance`` fact."""
    return "NonCompliant" if isinstance(value, dict) else value.capitalize()


def of_follower(to):
    """The ``follower_compliance`` fact of a transfer to ``to``: what the follower it names (``Follower:<capital>``)
    last reported over an active link; Unknown for any other recipient or a link that is not active."""
    from ggg import Follower

    kind, _, capital = (to or "").partition(":")
    follower = Follower[capital] if kind == "Follower" else None
    return tag(of(follower.compliance)) if follower is not None and follower.status == "active" else "Unknown"


def of_reporting_quarter():
    """The ``follower_compliance`` fact of a quarter's report: the compliance of the quarter reporting, Unknown for a
    caller that is not one."""
    from ggg import Quarter

    quarter = Quarter[chain.caller()]
    return tag(of(quarter.compliance)) if quarter is not None else "Unknown"
