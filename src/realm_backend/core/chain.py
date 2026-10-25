import json

from basilisk import Duration, Principal, ic

from core.candid import quote, unquote

ANONYMOUS = "2vxsx-fae"


class Trap(BaseException):
    """Undo the message's changes: no except clause sees it."""


def caller():
    return ic.caller().to_str()


def is_controller(principal):
    return principal != ANONYMOUS and ic.is_controller(Principal.from_str(principal))


def self_id():
    return ic.id().to_str()


def now():
    return ic.time() // 1_000_000_000


def trap(message):
    ic.trap(message)


def set_timer(secs, cb):
    return ic.set_timer(Duration(secs), cb)


def call(cid, method, candid="()", cycles=0):
    res = yield ic.call_raw(Principal.from_str(cid), method, ic.candid_encode(candid), cycles)
    if getattr(res, "Err", None) is not None:
        raise Exception(f"{method} on {cid} failed: {res.Err}")
    return ic.candid_decode(getattr(res, "Ok", res))


def call_text(cid, method, arg=None):
    reply = yield from call(cid, method, "()" if arg is None else f"({quote(arg)})")
    return unquote(reply)


def call_op(cid, name, args):
    """Run operation ``name`` on another realm canister; returns its JSON reply as text."""
    reply = yield from call(cid, "run", f"({quote(name)}, {quote(json.dumps(args))})")
    return unquote(reply)
