import json

from basilisk import Duration, Principal, ic

from core.candid import quote, unquote
from core import principal

ANONYMOUS = "2vxsx-fae"
# An HTTPS outcall is paid for by every node of the subnet; this is the largest subnet, and what a smaller one does
# not spend is given back. The price list: https://internetcomputer.org/docs/building-apps/essentials/gas-cost
HTTP_NODES = 34


class Trap(BaseException):
    """Undo the message's changes: no except clause sees it."""


def caller():
    return ic.caller().to_str()


def is_controller(principal):
    return principal != ANONYMOUS and ic.is_controller(Principal.from_str(principal))


def is_principal(text):
    """Whether ``text`` is the text form of a principal, checksum included (core.principal). The in-process tests' chain
    accepts the names of their fixtures too."""
    return principal.valid(text)


def self_id():
    return ic.id().to_str()


def now():
    return ic.time() // 1_000_000_000


def instructions():
    """Instructions this message has run so far: a deterministic clock for what work costs."""
    return ic.performance_counter(0)


def candid_encode(text):
    return ic.candid_encode(text)


def candid_decode(data):
    """Candid text for the encoded ``data``; it must be Candid ("DIDL…"): anything else traps."""
    return ic.candid_decode(data)


def cycles():
    return ic.canister_balance128()


def memory():
    """Bytes of stable memory in use."""
    return ic.stable64_size() * 65536


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


def http_cycles(request_bytes, max_response_bytes):
    """The most an HTTPS outcall of this size costs, on a subnet of HTTP_NODES nodes."""
    nodes = HTTP_NODES
    return (3_000_000 + 60_000 * nodes) * nodes + 400 * nodes * request_bytes + 800 * nodes * max_response_bytes


def http_get(url, max_bytes):
    """GET ``url`` by HTTPS outcall, for a reply of at most ``max_bytes``: ``(status, body)``. Every node fetches the
    URL and the replica compares what they got through the realm's ``http_transform`` (main.py), which drops the
    headers, so the replies agree. A failed call raises, as ``call`` does. A generator."""
    from basilisk.canisters.management import management_canister

    request = {"url": url, "max_response_bytes": max_bytes, "method": {"get": None},
               "headers": [{"name": "Accept-Encoding", "value": "identity"}], "body": None,
               "transform": {"function": (ic.id(), "http_transform"), "context": b""}}
    reply = yield management_canister.http_request(request).with_cycles(http_cycles(len(url.encode()) + 100, max_bytes))
    if "Ok" not in reply:
        raise Exception(f"GET {url} failed: {reply}")
    return int(reply["Ok"]["status"]), bytes(reply["Ok"]["body"])
