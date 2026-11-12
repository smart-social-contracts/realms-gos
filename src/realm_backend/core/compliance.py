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


def after(releases, layer):
    """The releases of one layer, oldest first, that come after what the realm runs of it, ``layer`` (``{version,
    published, vouched_by}``, None when it runs none: all of them). A version the record lists has the releases the
    record lists after it. One the record does not list is placed by ``published``, the publisher's chain time on every
    entry of a record, relays copy it: the releases published strictly later, and none when the realm did not keep the
    time (a layer installed before it was kept, §11.6). An effective date does not order releases (the versions of one
    publication can take effect together, and an urgent one earlier than the release before it), and a tie or a time
    not known leaves a version the record cannot place: it is never behind it."""
    if layer is None:
        return releases
    versions = [release["version"] for release in releases]
    if layer["version"] in versions:
        return releases[versions.index(layer["version"]) + 1:]
    published = layer.get("published")
    return [release for release in releases if published is not None and release["published"] > published]


def went_back(releases, layer, by):
    """Whether the source ``by`` (the layer a record is the publisher's own record of), whose record lists ``releases``
    of the layer, vouched for what the realm runs of it and now lists only older ones: the source went back (it
    reverted a layer it relays, §11.6). The record does not list what the realm runs, and everything it lists was
    published strictly earlier than that, which the realm kept the time of to say so: a tie, or a time not kept, is no
    step back."""
    published = None if layer is None else layer.get("published")
    return (bool(by) and published is not None and layer.get("vouched_by") == by and bool(releases)
            and layer["version"] not in [release["version"] for release in releases]
            and all(release["published"] < published for release in releases))


def judged(record, running, now):
    """The flag of a realm that runs ``running`` (``{source: {version, published, vouched_by}}``, its layers from
    above) against the ``record`` of one of its sync sources, at ``now`` (§11.6): compliant while no layer the record
    lists has a release in effect that comes after what the realm runs of it (``after``); else non-compliant since the
    earliest such release, naming the stale layers. A layer the realm runs and the record does not list is no reason to
    be behind (``notes`` says so). It depends on the record and on what the realm runs, nothing else: a source's own
    releases are never removed, and a relayed layer is listed as the source runs it."""
    stale, since = [], []
    for source, releases in by_layer(record).items():
        in_effect = [r["effective_from"] for r in after(releases, running.get(source)) if r["effective_from"] <= now]
        if in_effect:
            stale.append(source)
            since.append(min(in_effect))
    return {"NonCompliant": {"since": min(since), "stale": stale}} if stale else "compliant"


def installable(record, running):
    """The newest release of each layer the ``record`` lists that comes after what the realm runs of it: what "Propose
    install" offers (§11.3), each as ``{source, version, effective_from}``. Where the source went back (``went_back``)
    it offers the release the source now lists, marked ``step_back``: an older release than the one the realm runs."""
    offered = []
    for source, releases in by_layer(record).items():
        layer = running.get(source)
        newer = after(releases, layer)
        if newer:
            offered.append({k: newer[-1][k] for k in ("source", "version", "effective_from")})
        elif went_back(releases, layer, record.get("source")):
            offered.append({**{k: releases[-1][k] for k in ("source", "version", "effective_from")}, "step_back": True})
    return offered


def order_of(published, running):
    """How a release published at ``published`` stands against the one the realm runs, published at ``running``
    (§11.6): ``older`` or ``newer`` when the times differ, ``tied`` when they are the same, ``unknown`` when either is
    not known. A tie, or a time not known, places nothing: a note, never a step back."""
    if published is None or running is None:
        return "unknown"
    return "older" if published < running else "newer" if published > running else "tied"


def notes(record, running):
    """What a flag does not say about the layers the ``record`` lists and the realm runs under a version the record
    does not list (§11.6): ``{source, kind, runs, lists}`` each, ``lists`` the newest release the record has of the
    layer. ``kind`` is ``went_back`` when the source vouched for what the realm runs and has since gone back to an
    older release (``went_back``), else ``unlisted``: a version this source never listed, or no longer lists, and no
    listed release was published after it. A realm that is behind a layer has no note for it."""
    found = []
    for source, releases in by_layer(record).items():
        layer = running.get(source)
        if layer is None or layer["version"] in [release["version"] for release in releases] or after(releases, layer):
            continue
        back = went_back(releases, layer, record.get("source"))
        found.append({"source": source, "kind": "went_back" if back else "unlisted", "runs": layer["version"],
                      "lists": releases[-1]["version"]})
    return found


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
