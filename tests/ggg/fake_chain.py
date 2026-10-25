"""Stand-in for ``chain`` in unit tests: a clock, a caller, controllers and recorded calls."""

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
