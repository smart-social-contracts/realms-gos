"""The canister's JSON on the real wasm (realms-gos#582).

basilisk's ``json`` is pure Python: parsing a row cost 2,700 instructions a character, and ``realm_info`` of a realm
whose branding images fill 1 MB passed a query's 5 billion. ``core.fastjson`` puts the C scanner and encoder behind it.
These tests run it where it matters: the text it writes must not differ by a byte from the stub's (stored rows and the
id of a layered codex are made of it), and ``realm_info`` must cost little however large the images are.
"""

import hashlib
import json
import os

import pytest
from pic import Canister

# The realm wasm before this change, packed from feat/layered-codex: the test of the upgrade is skipped without it.
PREVIOUS_WASM = os.environ.get("REALM_PREVIOUS_WASM")
RATE_LIMIT = 600  # seconds the replica wants between install_code messages as dear as installing this wasm
IMAGE_MAX = 960_000  # api.setup.IMAGE_MAX, written out: a test that follows its constant would follow a wrong one
# realm_info with both images at the most allowed costs 0.31 billion instructions (measured on PocketIC); a query may
# use 5 billion. At the stub's price of a character, 4,500 instructions, the same call costs 9 billion.
REALM_INFO_BUDGET = 500_000_000
# A query reply passes 3 MiB at the most: the images and the rest must fit.
REPLY_MAX = 3 * 1024 * 1024

LAYERS = ["eu/es", "eu/es/vc"]
PATHS = {"tax": ["eu/es", "eu/es/vc"], "élection": ["eu/es/vc"]}
SUMS = {"eu/es/tax": "sha256:" + "ab" * 32, "eu/es/vc/elección/ñ": "sha256:" + "0f" * 32}


def ok(realm, op, who, query=False, **args):
    out = realm.call(op, args, sender=who, query=query)
    assert out["ok"], (op, out)
    return out


def run(realm, code):
    out = realm.shell(code)
    assert "Traceback" not in out, out
    return out


def launched(wasm, people, image=""):
    """A launched realm whose logo and background are ``image``."""
    realm = Canister(wasm, installer=people["installer"])
    founder = people["founder"]
    ok(realm, "setup_step", people["installer"], step="install", network="local", founder=founder)
    ok(realm, "setup_step", founder, step="realm", name="Agora", manifesto="Every step is tested.",
       welcome_message="Welcome", open_registration=True)
    ok(realm, "setup_step", founder, step="branding", logo_url=image, background_image_url=image,
       primary_color="#1E6B52")
    ok(realm, "setup_launch", founder)
    return realm


IMAGE = "data:image/png;base64," + "Ab+/" * 239_994 + "=="  # IMAGE_MAX characters
assert len(IMAGE) == IMAGE_MAX


@pytest.fixture(scope="module")
def full(wasm_of, people):
    """A launched realm with the largest images the realm accepts. Its setup_step answers with both of them, in the 2 MiB
    an update's reply may have."""
    return launched(wasm_of("production"), people, IMAGE)


def test_the_canister_runs_the_fast_json(full):
    out = run(full, "import json\nfrom core import fastjson\nprint(json.loads is fastjson.loads, "
                    "json.dumps is fastjson.dumps)")
    assert out.strip() == "True True"


def test_realm_info_with_the_largest_images_costs_a_fraction_of_a_query(full, costs):
    spent = costs.measure(full, "realm_info, two images of 960,000 characters", (
        "from core.op import dispatch, run as drive\n"
        "reply = drive(dispatch('realm_info', '{}', query=True))\n"
        "assert len(reply) > 1_900_000, len(reply)"))
    assert spent < REALM_INFO_BUDGET


def test_realm_info_is_answered_as_a_query_within_the_reply_limit(full):
    reply = full.call("realm_info", {}, query=True)
    assert reply["ok"] and reply["realm"]["name"] == "Agora" and reply["realm"]["welcome_message"] == "Welcome"
    assert reply["realm"]["logo_url"] == IMAGE and len(json.dumps(reply)) < REPLY_MAX


def test_an_image_above_the_limit_is_refused_and_the_limit_is_accepted(wasm_of, people):
    realm = Canister(wasm_of("production"), installer=people["installer"])
    founder = people["founder"]
    ok(realm, "setup_step", people["installer"], step="install", network="local", founder=founder)
    refused = realm.call("setup_step", {"step": "branding", "logo_url": "x" * (IMAGE_MAX + 1)}, sender=founder)
    assert not refused["ok"] and "logo_url is above 960,000 characters" in refused["error"]
    ok(realm, "setup_step", founder, step="branding", logo_url="x" * IMAGE_MAX)


DECODE = '''
import json
texts = ["[1,", "", "x", '{"a":}', '"abc', '{\\n  "a": 1,\\n  "b": }', "[1] tail"]
out = {}
for text in texts:
    try:
        json.loads(text)
        out[text] = "read"
    except json.JSONDecodeError as e:
        out[text] = [isinstance(e, ValueError), e.pos, e.lineno, e.colno, str(e)]
try:
    json.loads(None)
except TypeError as e:
    out["None"] = str(e)
print(json.dumps(out))
'''


def test_a_malformed_text_is_reported_in_the_canister_as_cpython_reports_it(full):
    """The same exceptions, positions, lines and columns, and messages that CPython's own json gives, which D2 shows
    to the authors of a codex."""
    expected = {}
    for text in ["[1,", "", "x", '{"a":}', '"abc', '{\n  "a": 1,\n  "b": }']:
        with pytest.raises(json.JSONDecodeError) as raised:
            json.loads(text)
        e = raised.value
        expected[text] = [True, e.pos, e.lineno, e.colno, str(e)]
    expected["[1] tail"] = "read"
    expected["None"] = "the JSON object must be str, bytes or bytearray, not NoneType"
    assert json.loads(run(full, DECODE)) == expected


def test_a_change_list_that_repeats_a_key_is_refused_in_the_canister(full):
    """core.change_list reads the pairs of each object; the stub's loads ignored the hook, the fast one gives it."""
    out = run(full, "from core import change_list\nfrom core.db import Fail\ntry:\n"
                    "    change_list.parse('{\"kind\": 1, \"kind\": 2}')\nexcept Fail as e:\n    print(e)")
    assert out.strip() == "the change list repeats the key 'kind'"


# What the stub writes, which the canister's dumps must write byte for byte. The text is made on the host, with
# CPython's json asked for compact separators and raw non-ASCII characters, which is how the stub writes.
CORPUS = [
    None, True, False, 0, -1, 10**30, -(10**30), 1.5, -0.0, 1e22, 1e-7, 123456789.123456789, "", "abc", "é", "日本語",
    "😀", "  ", 'q"uote', "back\\slash", "ctl\x00\x01\x1f\x7f", "tab\t\n\r\b\f", "/", [], {}, [1, [2, [3, []]]],
    {"a": {"b": {"c": []}}}, ["é", {"ü": "ß", "Z": 1, "a": 2, "10": 3, "2": 4}], {"t": [1, "x"]}, {"1": "a", "2.5": "b"},
    [i / 7 for i in range(20)], {f"k{i}": i * 2**40 for i in range(50)}, "x" * 5000 + '"' + "é" * 100,
    {"layers": LAYERS, "layer_paths": PATHS, "chapters": SUMS},
]

COMPARE = f'''
import json
from core import fastjson

corpus = {CORPUS!r}
differ = []
for value in corpus:
    for kw in ({{}}, {{"sort_keys": True}}, {{"sort_keys": True, "separators": (",", ":"), "ensure_ascii": False}}):
        stub, fast = fastjson._STUB_DUMPS(value, **kw), fastjson.dumps(value, **kw)
        if stub != fast:
            differ.append(["dumps", repr(value)[:60], stub[:80], fast[:80]])
    text = fastjson._STUB_DUMPS(value)
    if repr(json.loads(text)) != repr(value):
        differ.append(["loads", repr(value)[:60]])
print(json.dumps(differ))
'''


def test_dumps_and_loads_write_and_read_what_the_stub_did(full):
    assert json.loads(run(full, COMPARE)) == []


@pytest.mark.parametrize("sort_keys", [False, True])
def test_dumps_in_the_canister_is_the_text_cpython_writes_compact(full, sort_keys):
    code = f"import json\nprint(json.dumps({CORPUS!r}, sort_keys={sort_keys}))"
    expected = json.dumps(CORPUS, sort_keys=sort_keys, separators=(",", ":"), ensure_ascii=False)
    assert run(full, code).strip() == expected


CODEX_ID = f'''
import json
from core import fastjson, layered_codex as store
value = {{"layers": {LAYERS!r}, "layer_paths": {PATHS!r}, "chapters": {SUMS!r}}}
stub = fastjson._STUB_DUMPS(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
chapters = {{chapter: {{"sha256": sum_}} for chapter, sum_ in {SUMS!r}.items()}}
print(json.dumps({{"same_text": store.canonical(value) == stub, "id": store.codex_id({LAYERS!r}, {PATHS!r}, chapters)}}))
'''


# What the previous release's canister has: its stub json, and no fastjson to compare with.
CODEX_ID_BEFORE = f'''
import json
from core import layered_codex as store
chapters = {{chapter: {{"sha256": sum_}} for chapter, sum_ in {SUMS!r}.items()}}
print(json.dumps({{"id": store.codex_id({LAYERS!r}, {PATHS!r}, chapters)}}))
'''


def codex_id_on_the_host():
    text = json.dumps({"layers": LAYERS, "layer_paths": PATHS, "chapters": SUMS}, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def test_a_layered_codex_id_is_the_same_with_the_stub_and_with_the_fast_json(full):
    """The id is the checksum of canonical(), which json.dumps writes: a byte of difference is another codex."""
    out = json.loads(run(full, CODEX_ID))
    assert out == {"same_text": True, "id": codex_id_on_the_host()}


@pytest.mark.skipif(not PREVIOUS_WASM, reason="REALM_PREVIOUS_WASM names no wasm")
def test_a_layered_codex_id_does_not_change_across_the_upgrade_to_the_fast_json(wasm_of, people):
    realm = launched(PREVIOUS_WASM, people)
    before = json.loads(run(realm, CODEX_ID_BEFORE))
    realm.wait(RATE_LIMIT)
    realm.upgrade(wasm_of("production"))
    assert json.loads(run(realm, CODEX_ID)) == {"same_text": True, "id": before["id"]}
    assert before["id"] == codex_id_on_the_host()
    assert ok(realm, "realm_info", people["founder"], query=True)["realm"]["name"] == "Agora"
