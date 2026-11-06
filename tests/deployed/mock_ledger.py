"""A ledger for PocketIC that answers every call the same way: the balance it was given, and a transfer that succeeds.

It is a hand-assembled WebAssembly module of two methods, so the tests need no ledger release to download. The realm
only reads ``icrc1_balance_of`` (an invoice is paid once the balance covers it) and the ``Ok`` of ``icrc1_transfer``.
"""


def leb(n):
    """``n`` as an unsigned LEB128 number: WebAssembly's and Candid's integer."""
    out = bytearray()
    while True:
        low, n = n & 0x7F, n >> 7
        out.append(low | (0x80 if n else 0))
        if not n:
            return bytes(out)


def idl_hash(name):
    h = 0
    for byte in name.encode():
        h = (h * 223 + byte) % 2**32
    return h


def vec(items):
    return leb(len(items)) + b"".join(items)


def name(text):
    return leb(len(text)) + text.encode()


def section(section_id, payload):
    return bytes([section_id]) + leb(len(payload)) + payload


def candid_nat(n):
    return b"DIDL\x00\x01\x7d" + leb(n)


def candid_ok_nat(n):
    """``variant { Ok : nat }`` holding ``n``: what ``icrc1_transfer`` answers with the block index."""
    return b"DIDL\x01\x6b\x01" + leb(idl_hash("Ok")) + b"\x7d\x01\x00\x00" + leb(n)


def wasm(balance, block=1):
    balance_reply, transfer_reply = candid_nat(balance), candid_ok_nat(block)
    assert len(balance_reply) + len(transfer_reply) < 64  # offsets and lengths below are one-byte numbers

    def method(offset, length):
        # ic0.msg_reply_data_append(offset, length); ic0.msg_reply()
        code = b"\x00\x41" + bytes([offset]) + b"\x41" + bytes([length]) + b"\x10\x00\x10\x01\x0b"
        return leb(len(code)) + code

    return b"\x00asm\x01\x00\x00\x00" + b"".join([
        section(1, vec([b"\x60" + vec([b"\x7f", b"\x7f"]) + vec([]), b"\x60" + vec([]) + vec([])])),
        section(2, vec([name("ic0") + name("msg_reply_data_append") + b"\x00\x00",
                        name("ic0") + name("msg_reply") + b"\x00\x01"])),
        section(3, vec([leb(1), leb(1)])),
        section(5, vec([b"\x00\x01"])),
        section(7, vec([name("canister_query icrc1_balance_of") + b"\x00\x02",
                        name("canister_update icrc1_transfer") + b"\x00\x03"])),
        section(10, vec([method(0, len(balance_reply)), method(len(balance_reply), len(transfer_reply))])),
        section(11, vec([b"\x00\x41\x00\x0b" + leb(len(balance_reply + transfer_reply)) + balance_reply
                         + transfer_reply])),
    ])


def install(pic, balance):
    """A ledger canister on ``pic``; returns its id as text."""
    canister = pic.create_canister()
    pic.add_cycles(canister, 10**12)
    pic.install_code(canister, wasm(balance), [])
    return canister.to_str()
