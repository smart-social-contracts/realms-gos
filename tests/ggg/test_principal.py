"""The text form of a principal, checked in plain Python (core/principal.py): a canister's ``Principal.from_str`` gives back
whatever text it is handed, so the realm checks the checksum itself before it puts a delegate's text in its own lines."""
import hashlib

import pytest

from core import principal

ic_principal = pytest.importorskip("ic.principal")

REAL = ("aaaaa-aa", "2vxsx-fae", "ryjl3-tyaaa-aaaaa-aaaba-cai",
        "clj46-m343l-c4n6z-jwzek-3dtmg-dwwzm-gh62p-sjvbz-wjwej-7vh7i-rqe")


@pytest.mark.parametrize("text", REAL)
def test_a_real_principal_is_valid(text):
    assert principal.valid(text)


def test_every_length_of_principal_is_written_as_the_library_writes_it():
    for size in range(0, 30):
        raw = hashlib.sha256(bytes([size])).digest()[:size]
        text = ic_principal.Principal(bytes=raw).to_str()
        assert principal.text_of(raw) == text and principal.valid(text), size
    user = ic_principal.Principal.self_authenticating(hashlib.sha256(b"someone").digest()).to_str()
    assert principal.valid(user) and len(user) == 63


def forged(text):
    last = "a" if text[-1] != "a" else "b"
    return {"a phrase of dashed words": "no-one-else-will-read-it-you-keep-it", "a bad checksum": text[:-1] + last,
            "upper case": text.upper(), "no dashes": text.replace("-", ""), "an extra dash": text[:3] + "-" + text[3:],
            "a trailing dash": text + "-", "a leading dash": "-" + text, "a space": text.replace("-", " ", 1),
            "a colon": text.replace("-", ":", 1)}


@pytest.mark.parametrize("what", sorted(forged("aaaaa-aa")))
def test_text_that_only_looks_like_a_principal_is_not(what):
    for text in REAL:
        assert not principal.valid(forged(text)[what]), (what, text)


def test_what_is_not_text_of_the_right_size_is_not_a_principal():
    longest = ic_principal.Principal(bytes=bytes(range(29))).to_str()
    too_long = ic_principal.Principal(bytes=bytes(range(30))).to_str()
    assert principal.valid(longest) and not principal.valid(too_long)
    for text in ("", "a", "alice", "x", "aaaaa", "-", None, 7, b"aaaaa-aa", ["aaaaa-aa"]):
        assert not principal.valid(text), text
