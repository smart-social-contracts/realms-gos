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
