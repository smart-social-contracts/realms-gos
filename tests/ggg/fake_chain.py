"""Stand-in for ``chain`` in unit tests: a clock, a caller, controllers and recorded calls."""

import json

ANONYMOUS = "2vxsx-fae"
SELF = "realm"
state = {}


class Trap(BaseException):
    """A canister trap: no except clause sees it."""


def reset():
    state.clear()
    state.update(caller=ANONYMOUS, controllers={"ctl"}, now=1_790_000_000, timers=[], calls=[], replies={})


def caller():
    return state["caller"]


def is_controller(principal):
    return principal in state["controllers"]


def self_id():
    return SELF


def now():
    return state["now"]


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


def call(cid, method, candid="()", cycles=0):
    state["calls"].append((cid, method, candid))
    reply = state["replies"].get((cid, method), "()")
    if isinstance(reply, Exception):
        raise reply
    return reply
    yield


def call_text(cid, method, arg=None):
    state["calls"].append((cid, method, arg))
    reply = state["replies"].get((cid, method), "")
    if isinstance(reply, Exception):
        raise reply
    return reply
    yield


def call_op(cid, name, args):
    """Recorded as ``(cid, name, json)``, like call_text, with replies keyed by ``(cid, name)``."""
    return (yield from call_text(cid, name, json.dumps(args)))
