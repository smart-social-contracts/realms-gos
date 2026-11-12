"""What changed on a capital since its quarters last synced ("What a sync carries" in
docs/architecture/quarters-and-federation.md).

Every sync used to carry the whole directory, every zone and every synced setting, so an hourly sync of a federation that
had not changed cost what a changed one did, on both sides, and grew with the number of quarters and zones. The capital
now notes each change a quarter takes from it, in order, in a log of ``SyncChange`` rows: a quarter's row of the
directory, a zone the realm defines, its synced settings, its sweep rules. A quarter that syncs presents its cursor, the
number of the last change it took and when that one was made, and gets only what came after it.

It gets everything when it has no cursor (the first sync after a start, a quarter on the previous release), when its
cursor is ahead of this log or names a change that is not this log's (the capital was restored from a snapshot), or when
it is further behind than the log reaches.
"""

from core import chain
from core.db import put
from ggg import Realm, SyncChange

# How many changes the log keeps. A quarter this far behind or further takes everything: a directory of the size a
# federation has costs about what that many changes do.
KEPT = 256
HEAD = "head"


def slot_id(n):
    """Not a number: the ids of rows made without one are numbers, and a row is found by either."""
    return f"slot-{n % KEPT}"


def slot(n):
    """The row that holds the change numbered ``n``, while the log keeps it."""
    return SyncChange[slot_id(n)]


def note(part, key=""):
    """On a capital, record that ``part`` (quarter, zone, settings or sweep) changed, ``key`` being the quarter or zone it
    is about. Anywhere else nothing reads it: a quarter has no quarters, and a realm with none has no one to tell."""
    r = Realm["1"]
    if not (r.is_quarter and r.is_capital):
        return
    head, now = SyncChange[HEAD] or SyncChange(id=HEAD), chain.now()
    n = head.n + 1
    put(slot(n) or SyncChange(id=slot_id(n)), n=n, part=part, key=key, at=now)
    put(head, n=n, at=now)


def cursor():
    """The position of the last change: its number and when it was made, 0 and 0 for a log with none."""
    head = SyncChange[HEAD]
    return {"n": head.n, "at": head.at} if head else {"n": 0, "at": 0}


def since(have, head):
    """The changes after the cursor ``have``, as ``{part: [key, ...]}`` with each key once, or None when its holder takes
    everything (see the top of the module). ``head`` is the log's own cursor."""
    if not isinstance(have, dict):
        return None
    n = have.get("n")
    if type(n) is not int or n < 0 or n > head["n"] or head["n"] - n >= KEPT:
        return None
    if n == head["n"]:
        if have.get("at") != head["at"]:
            return None
    elif n:  # the change the cursor names must be the one this log numbered so
        held = slot(n)
        if held is None or (held.n, held.at) != (n, have.get("at")):
            return None
    changes = {}
    for m in range(n + 1, head["n"] + 1):
        change = slot(m)
        if change is None or change.n != m:
            return None
        keys = changes.setdefault(change.part, [])
        if change.key not in keys:
            keys.append(change.key)
    return changes
