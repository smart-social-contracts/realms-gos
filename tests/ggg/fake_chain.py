"""Stand-in for ``chain`` in unit tests: a clock, a caller, controllers and recorded calls.

Calls to other canisters are answered by a scripted peer (``peer``), or, for a canister that is not one, by
``state["replies"]``, a fixed reply per ``(canister, method)``.
"""

import json
from collections import namedtuple

ANONYMOUS = "2vxsx-fae"
SELF = "realm"
state = {}


class Trap(BaseException):
    """A canister trap: no except clause sees it."""


def reset():
    state.clear()
    state.update(caller=ANONYMOUS, controllers={"ctl"}, now=1_790_000_000, timers=[], calls=[], replies={}, peers={})


def caller():
    return state["caller"]


def is_controller(principal):
    return principal in state["controllers"]


def self_id():
    return SELF


def now():
    return state["now"]


def instructions():
    """Stands in for the instruction counter: a million more every time it is read."""
    state["instructions"] = state.get("instructions", 0) + 1_000_000
    return state["instructions"]


def candid_encode(text):
    """Stands in for Candid: the text itself, after the magic bytes."""
    return b"DIDL" + text.encode()


def candid_decode(data):
    return data[4:].decode()


def cycles():
    return 5_000_000_000_000


def memory():
    return 64 * 65536


def trap(message):
    raise Trap(message)


def set_timer(secs, cb):
    state["timers"].append((secs, cb))


Failure = namedtuple("Failure", "kind message")


def rejects(message):
    """A peer's answer that fails the call: the canister rejects it with ``message``."""
    return Failure("reject", message)


def traps(message):
    """A peer's answer that fails the call: the canister traps while handling it, which its caller sees as a reject."""
    return Failure("trap", message)


class Peer:
    """A canister the realm calls, played by a test.

    ``replies`` maps a method name to what the peer answers: text or bytes, sent as they are; a dict or list, sent
    as JSON; a function of the argument returning any of these, to answer by what it was sent or to change over
    time; or ``rejects(...)`` and ``traps(...)``, which fail the call as the replica reports it to the realm. A method
    without a reply is rejected, as a canister that has no such method would. A wrong reply is just a reply the
    realm does not expect.

    ``calls`` holds what the peer was sent, ``(method, argument)`` in order. The argument is the Candid text for
    ``chain.call``, the text for ``chain.call_text`` and the args dict for ``chain.call_op``.
    """

    def __init__(self, cid, replies):
        self.cid, self.replies, self.calls = cid, dict(replies), []

    def answer(self, method, reply):
        self.replies[method] = reply
        return self

    def sent(self, method):
        """The arguments of the calls to ``method``, in order."""
        return [arg for m, arg in self.calls if m == method]

    def receive(self, method, arg):
        self.calls.append((method, arg))
        if method not in self.replies:
            self._fail(method, f"canister has no method '{method}'")
        reply = self.replies[method]
        if callable(reply):
            reply = reply(arg)
        if isinstance(reply, Failure):
            self._fail(method, f"Canister {self.cid} trapped explicitly: {reply.message}"
                       if reply.kind == "trap" else reply.message)
        return reply if isinstance(reply, (str, bytes)) else json.dumps(reply)

    def _fail(self, method, reason):
        raise Exception(f"{method} on {self.cid} failed: {reason}")  # the text chain.call raises on a rejected call


def peer(cid, **replies):
    """Script the canister ``cid``: the realm's calls to it go to the returned ``Peer`` from now on."""
    state["peers"][cid] = Peer(cid, replies)
    return state["peers"][cid]


def send(cid, method, recorded, given, default):
    """One call to another canister. ``recorded`` is what ``state["calls"]`` keeps and ``given`` what a peer is
    handed; a canister that is no peer answers with ``state["replies"]``, or ``default``."""
    state["calls"].append((cid, method, recorded))
    if cid in state["peers"]:
        return state["peers"][cid].receive(method, given)
    reply = state["replies"].get((cid, method), default)
    if isinstance(reply, Exception):
        raise reply
    return reply


def call(cid, method, candid="()", cycles=0):
    return send(cid, method, candid, candid, "()")
    yield


def call_text(cid, method, arg=None):
    return send(cid, method, arg, arg, "")
    yield


def call_op(cid, name, args):
    """Run operation ``name`` on another realm. ``state["calls"]`` records ``(cid, name, json)``, like call_text. A peer
    is handed ``args`` itself; a canister that is no peer answers with ``state["replies"][(cid, name)]``, or with the
    empty text if there is none."""
    return send(cid, name, json.dumps(args), args, "")
    yield
