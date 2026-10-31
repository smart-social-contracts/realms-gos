"""This realm's listing in the realm registry.

The registry's directory and globe read listings from the registry instead of
asking every realm. A capital (or a realm without quarters) publishes its own:
every ROUND_S a round walks the territory zones in batches of ZONE_BATCH (one
timer message each), then sends the summary if anything changed, and the
manifesto and zones only when those changed, since each change of either makes
every visitor fetch them again. Quarters have no listing.

What was last sent is kept on the heap, so the first round after an upgrade
sends everything once.
"""

import json

from _cdk import Principal, Service, ic, service_update, text
from ic_python_logging import get_logger

logger = get_logger("core.listing")

ROUND_S = 900
FIRST_ROUND_S = 60
ZONE_BATCH = 300
ZONES_MAX = 1000
STAGES = ("alpha", "beta", "production", "deprecation", "terminated")

_round = {"started": 0, "cursor": 1, "zones": {}}
_sent = {}


def _now_s() -> int:
    return int(ic.time() // 1_000_000_000)


class ListingRegistry(Service):
    @service_update
    def listing_publish(self, args: text) -> text: ...


def start() -> None:
    """Arm the round timer (init and post_upgrade)."""
    try:
        ic.set_timer(FIRST_ROUND_S, _begin_round)
        ic.set_timer_interval(ROUND_S, _begin_round)
    except Exception as e:
        logger.warning(f"listing timer not armed: {e}")


def _begin_round() -> None:
    # A round whose step timer was dropped never finishes; the next one replaces it.
    if _round["started"] and _now_s() - _round["started"] < ROUND_S:
        return
    _round.update(started=_now_s(), cursor=1, zones={})
    ic.set_timer(1, _step)


def _step():
    try:
        if not _scan_zones():
            ic.set_timer(1, _step)
            return
        yield from _publish()
    except Exception as e:
        logger.error(f"listing round failed: {e}")
    _round["started"] = 0


def _scan_zones() -> bool:
    """Collect one batch of territory zones; True when the scan is complete."""
    from ggg import Zone

    page = Zone.load_some(_round["cursor"], ZONE_BATCH)
    zones = _round["zones"]
    for zone in page:
        try:
            if zone.land is not None:
                continue
        except Exception:
            pass
        h3_index = (zone.h3_index or "").strip().lower()
        if not h3_index:
            continue
        if h3_index in zones:
            zones[h3_index]["user_count"] += 1
        elif len(zones) < ZONES_MAX:
            zones[h3_index] = {
                "h3_index": h3_index,
                "name": (zone.name or "")[:80],
                "zone_type": (getattr(zone, "zone_type", None) or "")[:32],
                "user_count": 1,
            }
    if len(page) < ZONE_BATCH:
        return True
    _round["cursor"] = int(page[-1]._id) + 1
    return False


def build_payload(status: dict, zones: list) -> dict:
    """The listing parts, each as compared against what was sent last."""
    summary = {
        "name": (status.get("realm_name") or "").strip()[:128],
        "users_count": int(status.get("users_count") or 0),
        "last_activity_at": int(status.get("last_activity_at") or 0),
    }
    stage = (status.get("realm_stage") or "").strip().lower()
    if stage in STAGES:
        summary["stage"] = stage
    logo = (status.get("logo_url") or "").strip()
    if logo.startswith("https://") and len(logo) <= 512:
        summary["logo"] = logo
    return {
        "summary": summary,
        "manifesto": (status.get("realm_manifesto") or "").strip()[:8000],
        "zones": sorted(zones, key=lambda z: z["h3_index"]),
    }


def changed_parts(parts: dict, sent: dict) -> dict:
    """The publish payload for what differs from *sent*; empty when nothing does."""
    payload = {}
    for key in ("manifesto", "zones"):
        if sent.get(key) != parts[key]:
            payload[key] = parts[key]
    if payload or sent.get("summary") != parts["summary"]:
        payload.update(parts["summary"])
    return payload


def _publish():
    from api.status import get_status
    from ggg import Realm

    from core.setup import get_realm_registry_canister_id

    realm = Realm.load("1")
    if realm is None or getattr(realm, "is_quarter", False):
        return
    registry_id = get_realm_registry_canister_id(realm)
    if not registry_id:
        return

    parts = build_payload(get_status(), list(_round["zones"].values()))
    payload = changed_parts(parts, _sent)
    if not payload:
        return

    result = yield ListingRegistry(Principal.from_str(registry_id)).listing_publish(
        json.dumps(payload, separators=(",", ":"))
    )
    reply = result.get("Ok") if isinstance(result, dict) else getattr(result, "Ok", None)
    if reply is None:
        logger.warning(f"listing_publish call failed: {result}")
        return
    answer = json.loads(reply)
    if not answer.get("success"):
        logger.warning(f"registry refused the listing: {answer.get('error')}")
        return
    _sent.update(parts)
    logger.info(f"listing published (revision {answer.get('revision')})")
