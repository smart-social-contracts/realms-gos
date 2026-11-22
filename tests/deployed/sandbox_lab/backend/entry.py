"""The lab extension's backend. Edit it, then run ./run.sh again (README.md).

Any function here can be called: ./run.sh <function> <n> [<n> ...] calls it once for each n, as <function>(n=n). Each
call runs in a fresh sandbox, as the member Alice, and can reach the realm only through rpc(operation, **arguments),
for the operations manifest.json declares.
"""


def count(n):
    """Pure Python: n turns of a loop. What stops it is the sandbox's own meter, 10,000,000 bytecode instructions per
    call (core/sandbox.py BUDGET)."""
    total = 0
    for i in range(n):
        total += i
    return total


def rename(n):
    """n calls to a realm operation, profile_update. The realm's work for each runs outside the sandbox's meter, so what
    stops it is the Internet Computer's limit of 40,000,000,000 instructions per message."""
    for i in range(n):
        reply = rpc("profile_update", nickname="lab %d" % i)
        if not reply["ok"]:
            return reply
    return "renamed %d times" % n


def read(n):
    """n calls to a read, proposal_get of the proposal the realm starts with: cheaper than a write, so more fit."""
    for _ in range(n):
        reply = rpc("proposal_get", proposal="P-1", count=1)
        if not reply["ok"]:
            return reply
    return "read %d times" % n
