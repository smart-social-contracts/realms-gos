"""core.fastjson against CPython's json, which writes the same text as basilisk's stub when asked for compact
separators and raw non-ASCII characters. The canister itself is tested in tests/deployed/test_fastjson.py."""

import json

import pytest

from core import fastjson, layered_codex

COMPACT = {"separators": (",", ":"), "ensure_ascii": False}
CORPUS = [
    None, True, False, 0, -1, 10**30, -(10**30), 1.5, -0.0, 1e22, 1e-7, 123456789.123456789, "", "abc", "é", "日本語",
    "😀", "  ", 'q"uote', "back\\slash", "ctl\x00\x01\x1f\x7f", "tab\t\n\r\b\f", "/", [], {}, [1, [2, [3, []]]],
    {"a": {"b": {"c": []}}}, ["é", {"ü": "ß", "Z": 1, "a": 2, "10": 3, "2": 4}], (1, 2, 3), {"t": (1, "x")},
    {1: "a", 2.5: "b"}, [i / 7 for i in range(20)], {f"k{i}": i * 2**40 for i in range(50)}, "x" * 5000 + '"' + "é" * 100,
    {"layers": ["base/x", "quarter/y"], "layer_paths": {"tax": ["base/x"], "élection": []},
     "chapters": {"tax/rate": "sha256:" + "ab" * 32, "elección/ñ": "sha256:" + "0f" * 32}},
]


@pytest.mark.parametrize("value", CORPUS, ids=lambda v: repr(v)[:30])
@pytest.mark.parametrize("sort_keys", [False, True])
def test_dumps_writes_what_the_stub_wrote(value, sort_keys):
    assert fastjson.dumps(value, sort_keys=sort_keys) == json.dumps(value, sort_keys=sort_keys, **COMPACT)


def test_dumps_ignores_ensure_ascii_and_separators_like_the_stub():
    assert fastjson.dumps({"é": [1, 2]}, ensure_ascii=True, separators=(", ", ": ")) == '{"é":[1,2]}'


def test_dumps_with_indent_is_the_stubs(monkeypatch):
    monkeypatch.setattr(fastjson, "_STUB_DUMPS", lambda *args: "stub")
    assert fastjson.dumps({"a": 1}, indent=2) == "stub"


def test_dumps_calls_default_for_what_json_cannot_write():
    assert fastjson.dumps({"s": {2, 1}}, default=sorted) == '{"s":[1,2]}'
    with pytest.raises(TypeError, match="Object of type set is not JSON serializable"):
        fastjson.dumps({"s": {1}})


@pytest.mark.parametrize("value", [v for v in CORPUS if not isinstance(v, (tuple, dict)) or json.loads(json.dumps(v)) == v])
def test_loads_reads_what_dumps_wrote(value):
    assert fastjson.loads(fastjson.dumps(value)) == json.loads(json.dumps(value))


def test_loads_is_as_lenient_as_the_stub():
    assert fastjson.loads(' \n {"a": [1, 2]} tail') == {"a": [1, 2]}
    assert fastjson.loads(b'{"b": 1}') == {"b": 1}
    assert fastjson.loads('{"c": "line\nbreak"}') == {"c": "line\nbreak"}


@pytest.mark.parametrize("text", ["", "   ", "x", "[1,", "[\n  1,\n  "])
def test_loads_refuses_what_is_not_json_as_cpython_does(text):
    with pytest.raises(json.JSONDecodeError) as stub:
        json.loads(text)
    with pytest.raises(fastjson.JSONDecodeError) as fast:
        fastjson.loads(text)
    assert str(fast.value) == str(stub.value) and fast.value.pos == stub.value.pos
    assert (fast.value.lineno, fast.value.colno) == (stub.value.lineno, stub.value.colno)


def test_loads_wants_a_text():
    with pytest.raises(TypeError, match="must be str, bytes or bytearray, not NoneType"):
        fastjson.loads(None)


def test_install_leaves_cpythons_own_json_alone():
    loads, dumps = json.loads, json.dumps
    fastjson.install()
    assert (json.loads, json.dumps) == (loads, dumps)


def test_a_codex_id_is_the_checksum_of_the_stubs_text():
    """layered_codex.canonical is hashed into a codex id: its text must not change by a byte."""
    chapters = {"tax/rate": {"sha256": "ab" * 32}, "elección/ñ": {"sha256": "0f" * 32}}
    layers, paths = ["base/x", "quarter/ñ"], {"tax": ["base/x"], "élection": ["quarter/ñ", "base/x"]}
    text = '{"chapters":{"elección/ñ":"%s","tax/rate":"%s"},"layer_paths":{"tax":["base/x"],"élection":["quarter/ñ","base/x"]},"layers":["base/x","quarter/ñ"]}' % ("0f" * 32, "ab" * 32)
    assert layered_codex.canonical({"layers": layers, "layer_paths": paths,
                                    "chapters": {c: e["sha256"] for c, e in chapters.items()}}) == text
    assert fastjson.dumps({"layers": layers, "layer_paths": paths,
                           "chapters": {c: e["sha256"] for c, e in chapters.items()}}, sort_keys=True) == text
