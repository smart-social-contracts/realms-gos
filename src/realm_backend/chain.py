from basilisk import Duration, Principal, ic

ANONYMOUS = "2vxsx-fae"
ESCAPES = {"n": b"\n", "r": b"\r", "t": b"\t", '"': b'"', "\\": b"\\", "'": b"'"}


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
    candid = "()" if arg is None else '("' + arg.replace("\\", "\\\\").replace('"', '\\"') + '")'
    reply = yield from call(cid, method, candid)
    return unquote(reply)


def unquote(decoded):
    body, out, i = decoded[decoded.index('"') + 1:decoded.rindex('"')], bytearray(), 0
    while i < len(body):
        if body[i] != "\\":
            out += body[i].encode()
            i += 1
        elif body[i + 1] in ESCAPES:
            out += ESCAPES[body[i + 1]]
            i += 2
        elif body[i + 1] == "u":
            end = body.index("}", i)
            out += chr(int(body[i + 3:end], 16)).encode()
            i = end + 1
        else:
            out.append(int(body[i + 1:i + 3], 16))
            i += 3
    return out.decode()
