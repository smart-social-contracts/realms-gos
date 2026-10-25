"""Reading candid text replies without `re` (the canister's has no `compile`)."""

ESCAPES = {"n": b"\n", "r": b"\r", "t": b"\t", '"': b'"', "\\": b"\\", "'": b"'"}


def quote(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


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


def number(reply, after=""):
    """The first natural number in a candid reply, after ``after`` when given; None if there is none."""
    if after:
        if after not in reply:
            return None
        reply = reply.split(after, 1)[1]
    digits, started = "", False
    for ch in reply:
        if ch.isdigit():
            digits, started = digits + ch, True
        elif started and ch != "_":
            break
    return int(digits) if digits else None
