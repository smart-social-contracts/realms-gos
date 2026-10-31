"""When a person last did something in this realm.

The realm listing shows it as "active 2 h ago". Only changes made by people
count — joining, and the writes extensions make on a caller's behalf (votes,
proposals, cases, bids, parcels). Timers, sweeps and refreshes write all the
time; counting them would show every realm as permanently active.

A quarter reports its own time to the capital in the ``get_quarter_directory``
``self`` block, and the capital's ``status`` shows the latest of all of them.
"""

ANONYMOUS = "2vxsx-fae"
NOT_PEOPLE = {"", "system", ANONYMOUS}

# One write per minute at most: "active 2 h ago" does not need more, and every
# write is a stable-memory write on the caller's message.
MIN_INTERVAL_S = 60


def _now_s() -> int:
    from _cdk import ic

    return int(ic.time() // 1_000_000_000)


def _self_id() -> str:
    try:
        from _cdk import ic

        return ic.id().to_str()
    except Exception:
        return ""


def is_person(caller: str) -> bool:
    caller = (caller or "").strip()
    return caller not in NOT_PEOPLE and caller != _self_id()


def record(caller: str, now_s: int = None) -> bool:
    """Note that *caller* just changed something. True when stored."""
    if not is_person(caller):
        return False
    from ggg import Realm

    realm = Realm.load("1")
    if realm is None:
        return False
    now_s = _now_s() if now_s is None else int(now_s)
    if now_s - int(getattr(realm, "last_activity_at", 0) or 0) < MIN_INTERVAL_S:
        return False
    realm.last_activity_at = now_s
    return True


def own(realm) -> int:
    return int(getattr(realm, "last_activity_at", 0) or 0) if realm is not None else 0


def federation_latest(realm, quarters) -> int:
    """Latest activity across this realm and the quarters it knows of (seconds, 0 = none)."""
    latest = own(realm)
    for q in quarters or ():
        latest = max(latest, int(getattr(q, "last_activity_at", 0) or 0))
    return latest
