"""Lone surrogates: a code point from U+D800 to U+DFFF that is not one half of a pair. Python's ``str`` can hold one, but
no UTF-8 text can. The stable map refuses a key or a value that holds one (UnicodeEncodeError at the insert), and so does
Candid, which the replies are in.

They come in through JSON: ``json.loads`` makes one of the escape ``"\\ud800"`` (a pair, ``"\\ud83d\\ude00"``, is one
character and no concern), and the canister's ``json.dumps`` ignores ``ensure_ascii`` (core.fastjson), so it writes the
surrogate as it is. Left alone, a call with one traps wherever it first meets a write or a reply, with a Python error for
its message that names no argument, and after an await a trap leaves what the message did before it. So
core.op.dispatch refuses such a call once, for every operation, and says which argument holds it.

The stable map's refusal is what keeps a row from holding one. tests/deployed/test_lone_surrogates.py pins it: were it
ever lifted, a row that held one would make every reply that held the row trap, and ``json.dumps`` would have to write
the escape.
"""

import json


def encodable(text):
    """Whether ``text`` holds no lone surrogate. Text that is plain ASCII has none, and is known so without a pass."""
    if text.isascii():
        return True
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def holds(value):
    """Whether a text in ``value`` (JSON-like data: texts, lists, dicts and their keys) holds a lone surrogate. It does
    not recurse, so that data nested as deeply as JSON allows cannot exhaust the stack."""
    todo = [value]
    while todo:
        item = todo.pop()
        if isinstance(item, str):
            if not encodable(item):
                return True
        elif isinstance(item, dict):
            todo.extend(item)
            todo.extend(item.values())
        elif isinstance(item, (list, tuple)):
            todo.extend(item)
    return False


def culprit(record):
    """The first key of the dict ``record`` whose name or value holds a lone surrogate, or None."""
    return next((key for key, value in record.items() if holds(key) or holds(value)), None)


def json_holds(text):
    """Whether the JSON ``text``, parsed, holds a lone surrogate. Only a text with the start of such an escape (``\\ud``
    and a hex digit, from ``\\ud800`` to ``\\udfff``) can, so that is looked for first, in one pass in C, and only a text
    that has one is parsed. A text that is not JSON holds none."""
    if "\\ud" not in text and "\\uD" not in text:
        return False
    try:
        return holds(json.loads(text))
    except (ValueError, RecursionError):
        return False


def refuse_reply(text, cid, method):
    """``text``, the reply of ``method`` of canister ``cid``, unless its JSON holds a lone surrogate: the call then fails as
    a rejected one does (core.chain.call), with a reason the realm records where it records why a call failed. Whatever
    the realm stored of such a reply would trap at the stable map, in the timer that made the call, at every try."""
    if json_holds(text):
        raise Exception(f"{method} on {cid} failed: its reply holds a lone surrogate (U+D800 to U+DFFF), which is not text")
    return text
