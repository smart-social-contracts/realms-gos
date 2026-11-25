"""What a realm tells the outside about itself: its counts, and its listing in the realm registries that list it.

``realm_numbers`` is the realm's own count of its members, proposals and votes. It is public, and the public dashboard
shows it.

A realm registry lets only the realm change the figures of its own listing (gos-as-a-service ``publish_listing``), so a realm
that never publishes leaves the portal showing it as it was made: no members and no logo (realms-gos#736). The realm
publishes, and what that costs is bounded:

- Never on a join. A realm publishes when it launches, when it is told a registry (``host_registry``, ``realm_register``),
  and from one timer that looks once a day (``DAY``) and calls a registry only if the figures it holds now differ from the
  ones that registry last took. A realm that has not changed makes no call, and a busy one makes one a day to each
  registry. The call costs the realm a few million cycles, and the registry one update.
- Only what the registry takes: the name, the members, the stage and an https logo (the registry refuses any other, and the
  realm keeps its images as data URLs). ``last_activity_at`` is the time of the last change in those figures, never the
  clock's, so it is not one of the figures that are compared.
- A quarter publishes nothing: its capital is the listing, with the members of every quarter.
- A registry that refuses, or does not answer, is told again at the next look, and what it said is kept in
  ``manifest_data["listing"]``.
"""

import json

from api import quarters
from core import chain
from core.db import Fail, page
from core.op import op
from ggg import Proposal, Realm, Registry, Vote

DAY = 86400
FIRST = 120  # seconds from the start of the canister to its first look; its id adds up to SPREAD, so that realms do not call together
SPREAD = 600
NAME_MAX, LOGO_MAX = 128, 512  # the longest a registry takes
MAX_REGISTRIES = 10
KEY = "listing"
# Whether this heap has armed the look. Timers do not survive an upgrade, which empties the heap too.
_timer = {"armed": False}


def members():
    """The active members of the realm and of its quarters, as ``quarter_directory`` shows them."""
    return sum(int(row["population"]) for row in quarters.directory())


@op(None, read=True, public=True)
def realm_numbers(c, args):
    """The realm's counts: its ``members`` (the active ones, in all its quarters), its ``proposals`` and its ``votes``."""
    return {"members": members(), "proposals": Proposal.count(), "votes": Vote.count()}


def figures():
    """What the realm shows a registry of itself; the registry keeps what is left out."""
    realm = Realm["1"]
    shown = {"name": (realm.name or "")[:NAME_MAX], "users_count": members(), "stage": realm.status}
    logo = realm.logo_url or ""
    if logo.startswith("https://") and len(logo) <= LOGO_MAX:
        shown["logo"] = logo
    return shown


def listed():
    """Whether the realm tells registries about itself: once it has launched (a realm in setup has no stage a registry
    knows), and never a quarter, which is listed through its capital."""
    return Realm["1"].status != "setup" and not quarters.federated()


def registries():
    return [row.id for row in page(Registry, {"count": MAX_REGISTRIES})[0]]


def kept():
    """What the realm last told each registry (``sent``), when (``at``) and what that registry answered (``error``)."""
    return (Realm["1"].manifest_data or {}).get(KEY, {})


def keep(registry, **fields):
    realm, told = Realm["1"], kept()
    realm.manifest_data = {**realm.manifest_data, KEY: {**told, registry: {**told.get(registry, {}), **fields}}}


def publish():
    """Tell each registry that lists the realm what it holds now, unless that is what it took last. A generator."""
    if not listed():
        return
    shown = figures()
    for registry in registries():
        if kept().get(registry, {}).get("sent") == shown:
            continue
        try:
            reply = yield from chain.call_text(registry, "listing_publish",
                                               json.dumps({**shown, "last_activity_at": chain.now()}))
            said = json.loads(reply or "{}")
            error = "" if said.get("success") else str(said.get("error"))[:300]
        except Exception as e:  # noqa: BLE001  a registry that is down, or has no such method: the next look tells it again
            error = str(e)[:300]
        keep(registry, at=chain.now(), error=error, **({} if error else {"sent": shown}))


def publish_soon():
    """Publish in a message of its own: whoever asked does not wait for a registry."""
    chain.set_timer(0, publish)


def join(registry):
    """A registry lists this realm: the realm keeps its listing there up to date from now on, and publishes it at once."""
    if not chain.is_principal(registry):
        raise Fail(f"registry is the canister id of a realm registry, not '{registry}'")
    if Registry[registry] is None:
        Registry(id=registry, principal_id=registry, description=Realm["1"].name)
    publish_soon()


def resume():
    """Arm the daily look, unless it is armed. Timers do not survive an upgrade, so this runs when the canister starts
    (setup.start), and a realm upgraded onto this release publishes within minutes, not a day later."""
    if not _timer["armed"]:
        arm(FIRST + sum(chain.self_id().encode()) % SPREAD)


def arm(delay):
    _timer["armed"] = True
    chain.set_timer(delay, look)


def look():
    """The timer: arm the next look first, so that one that fails is not the last, then publish."""
    _timer["armed"] = False
    arm(DAY)
    yield from publish()
