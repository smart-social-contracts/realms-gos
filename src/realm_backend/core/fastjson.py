"""JSON on the C scanner and encoder, for the canister.

basilisk gives a canister a small pure-Python ``json`` (basilisk/frozen_stdlib_preamble.py): it costs about 2,700
instructions to parse and 1,800 to write one character of a string, measured on PocketIC. ic-python-db reads a row
with ``json.loads``, so a row holding a 1 MB branding image cost 2.7 billion instructions to load and a ``realm_info``
query that returned it passed the 5 billion limit (realms-gos#582). The C module ``_json`` is built into the canister
too, and ``install`` puts its scanner and encoder behind ``json.loads`` and ``json.dumps``, for every caller: 50 times
and 16 times cheaper.

What the stub did stays as it was, because stored rows and ``layered_codex.canonical`` (whose checksum is a codex id)
depend on it: ``dumps`` writes compact separators and non-ASCII characters as they are, whatever ``ensure_ascii`` and
``separators`` say; ``loads`` ignores whatever follows the first value and accepts control characters in strings.
``loads`` reports a malformed text as CPython does, with the line and column (``JSONDecodeError``, below).
This belongs in basilisk's preamble; it is here until that release.
"""

import json
import sys

import _json

STUB = "<frozen json>"  # the ``__file__`` basilisk gives its json
_STUB_DUMPS = json.dumps


class JSONDecodeError(json.JSONDecodeError):
    """CPython's ``json.decoder.JSONDecodeError``: the message with its line and column, and ``msg``, ``doc``,
    ``pos``, ``lineno`` and ``colno``. It is a subclass of the stub's, so what catches that catches this."""

    def __init__(self, msg, doc, pos):
        lineno, colno = doc.count("\n", 0, pos) + 1, pos - doc.rfind("\n", 0, pos)
        ValueError.__init__(self, f"{msg}: line {lineno} column {colno} (char {pos})")
        self.msg, self.doc, self.pos, self.lineno, self.colno = msg, doc, pos, lineno, colno


class _Scanner:
    """What ``_json.make_scanner`` reads from the decoder it serves, as json.decoder.JSONDecoder sets it."""

    strict = False
    object_hook = None
    object_pairs_hook = None
    parse_float = float
    parse_int = int
    parse_constant = {"-Infinity": float("-inf"), "Infinity": float("inf"), "NaN": float("nan")}.__getitem__


def _refuse(obj):
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def install():
    """Replace the stub's ``loads`` and ``dumps``, if ``json`` is the stub: under CPython's own json (tests, tools)
    there is nothing to do. The C scanner reports a malformed text with ``json.decoder.JSONDecodeError``, a module the
    stub has not: this one is registered in its place."""
    if getattr(json, "__file__", "") != STUB or json.loads is loads:
        return
    decoder = type(json)("json.decoder")
    decoder.JSONDecodeError = JSONDecodeError
    sys.modules["json.decoder"] = json.decoder = decoder
    json.loads, json.dumps = loads, dumps


def loads(text, **_):
    """The first JSON value in ``text``. Raises JSONDecodeError where there is none."""
    if isinstance(text, (bytes, bytearray)):
        text = text.decode("utf-8")
    elif not isinstance(text, str):
        raise TypeError(f"the JSON object must be str, bytes or bytearray, not {type(text).__name__}")
    start = len(text) - len(text.lstrip(" \t\n\r"))
    try:
        return _json.make_scanner(_Scanner)(text, start)[0]
    except StopIteration as no_value:
        raise JSONDecodeError("Expecting value", text, no_value.value) from None


def dumps(obj, ensure_ascii=True, sort_keys=False, indent=None, separators=None, default=None, **_):
    """``obj`` as JSON text: compact, with non-ASCII characters as they are; ``indent`` is the stub's."""
    if indent is not None:
        return _STUB_DUMPS(obj, ensure_ascii, sort_keys, indent, separators, default)
    encode = _json.make_encoder({}, default or _refuse, _json.encode_basestring, None, ":", ",", sort_keys, False, True)
    return "".join(encode(obj, 0))
