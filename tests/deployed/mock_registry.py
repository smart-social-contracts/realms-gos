"""A realm registry for PocketIC that takes every listing and keeps the last one it was sent, so that a test sees what a
realm published and how many times.

Like ``mock_ledger`` it is a hand-assembled WebAssembly module, so the tests need no registry release to download. Its
three methods:

- ``listing_publish`` (update): keeps its argument and counts the call, and answers ``{"success": true}``;
- ``last_arg`` (query): the argument of the last call, as the realm sent it (Candid, a text that holds JSON);
- ``calls`` (query): how many calls it has had, a Candid ``nat`` (at most 127).

The reply and the argument are Candid ``text`` (``DIDL`` ``00 01 71``), the type of the real ``listing_publish``.
"""
from mock_ledger import leb, name, section, vec

REPLY = b'{"success":true,"id":"mock","revision":0}'
HEADER = b"DIDL\x00\x01\x7d"  # a nat follows: the call count, at memory 15
REPLY_AT, ARG_AT = 16, 256  # where the fixed reply, and the last argument, sit in memory
TEXT_REPLY = b"DIDL\x00\x01\x71" + leb(len(REPLY)) + REPLY

# Function types: 0 () -> i32, 1 (i32, i32, i32) -> (), 2 (i32, i32) -> (), 3 () -> ().
TYPES = [b"\x60\x00\x01\x7f", b"\x60\x03\x7f\x7f\x7f\x00", b"\x60\x02\x7f\x7f\x00", b"\x60\x00\x00"]
IMPORTS = ["msg_arg_data_size", "msg_arg_data_copy", "msg_reply_data_append", "msg_reply"]  # functions 0 to 3
SIZE, COPY, APPEND, REPLY_ = 0, 1, 2, 3


def i32(n):
    """``i32.const n`` (n below 8192)."""
    low, high = n & 0x7F, n >> 7
    return b"\x41" + (bytes([low | 0x80, high]) if high else bytes([low]))


def call(function):
    return b"\x10" + bytes([function])


def body(code):
    """A function body: its size, no locals, the code, ``end``."""
    code = b"\x00" + code + b"\x0b"
    return leb(len(code)) + code


def wasm():
    assert len(TEXT_REPLY) < 64  # its length goes in as a one-byte i32
    publish = body(
        i32(0) + call(SIZE) + b"\x36\x02\x00"  # mem[0] = the size of the argument
        + i32(ARG_AT) + i32(0) + i32(0) + b"\x28\x02\x00" + call(COPY)  # copy it to ARG_AT
        + i32(15) + i32(15) + b"\x2d\x00\x00" + i32(1) + b"\x6a" + b"\x3a\x00\x00"  # mem[15] += 1
        + i32(REPLY_AT) + i32(len(TEXT_REPLY)) + call(APPEND) + call(REPLY_))
    last_arg = body(i32(ARG_AT) + i32(0) + b"\x28\x02\x00" + call(APPEND) + call(REPLY_))
    calls = body(i32(8) + i32(8) + call(APPEND) + call(REPLY_))
    data = HEADER + b"\x00" + TEXT_REPLY  # mem[8..15) the nat's header, mem[15] the count, then the fixed reply at 16
    return b"\x00asm\x01\x00\x00\x00" + b"".join([
        section(1, vec(TYPES)),
        section(2, vec([name("ic0") + name(n) + b"\x00" + bytes([t])
                        for n, t in zip(IMPORTS, (0, 1, 2, 3))])),
        section(3, vec([leb(3), leb(3), leb(3)])),
        section(5, vec([b"\x00\x01"])),
        section(7, vec([name("canister_update listing_publish") + b"\x00" + leb(4),
                        name("canister_query last_arg") + b"\x00" + leb(5),
                        name("canister_query calls") + b"\x00" + leb(6),
                        name("memory") + b"\x02\x00"])),
        section(10, vec([publish, last_arg, calls])),
        section(11, vec([b"\x00" + i32(8) + b"\x0b" + leb(len(data)) + data])),
    ])


def install(pic):
    """A registry canister on ``pic``; returns its id as text."""
    canister = pic.create_canister()
    pic.add_cycles(canister, 10**12)
    pic.install_code(canister, wasm(), [])
    return canister.to_str()
