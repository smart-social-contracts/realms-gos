"""A codex of the file registry that is not what the install reads is a refusal on the real wasm, never a trap (review
of #711; tests/ggg/test_registry_path.py runs the same shapes in process).

No deployed test has a file-registry canister, so the calls to it are stubbed from the controller's shell: the stub
replaces ``chain.call`` in the canister's interpreter, which keeps it across messages, and answers the registry's
methods from ``chain._stub``. The canister's JSON is ``core.fastjson`` and its ``base64`` a pure-Python stub, with
errors and limits of their own (the parse stops near 5,000 levels, not 1,000): the shapes are the worst of each
kind, and the install must answer each with a refusal, by a call, by the vote that closes a proposal and by the
timer of one that waits for its time. A trap in either of the last two rolls the message back, and the proposal
ends ``executed`` or stays ``voting`` with no reason.
"""

import base64
import json

import pytest
from network import Network

HOUR = 3600
LEX = {"id": "lex", "version": "1.0.0", "codex_api_version": 2, "hooks": ["init"], "capabilities": []}
HOOKS = "from ggg_sdk import hook\n\n@hook\ndef init(args):\n    return {}\n"
APPROVED = {"namespace": "ext/lex/1.0.0", "approved": True, "approver": "mkt"}


def b64(content):
    return base64.b64encode(content if isinstance(content, bytes) else content.encode()).decode()


def files(manifest=None, hooks=HOOKS):
    """The registry's answers to ``get_file`` by path."""
    text = json.dumps(LEX) if manifest is None else manifest
    return {"manifest.json": json.dumps({"content_b64": b64(text)}), "backend/hooks.py":
            json.dumps({"content_b64": b64(hooks)})}


STUB = r'''
import base64
import json
from core import chain
from core.candid import quote, unquote
from ggg import Realm

if not hasattr(chain, "_real_call"):
    chain._real_call = chain.call
chain._stub = {}
chain._asked, chain._stored = 0, []  # the chunks asked for, and what the frontend was sent to store
CHUNK = 128 * 1024  # what the registry hands out per call
chain._chunk_b64 = base64.b64encode(b"x" * CHUNK).decode()  # encoded once: the registry encodes on its side


def sized_chunk(what, offset):
    """The chunk at ``offset`` of "FILE <bytes>" (a file) or "NEVER" (one with no end), as the registry serves it."""
    total = None if what == "NEVER" else int(what.split()[1])
    length = CHUNK if total is None else min(CHUNK, total - offset)
    content = chain._chunk_b64 if length == CHUNK else base64.b64encode(b"x" * length).decode()
    return json.dumps({"content_b64": content, "length": length, "eof": total is not None and offset + length >= total})


def stubbed(cid, method, candid="()", cycles=0):
    if cid not in ("registry-stub", "frontend-stub"):
        return (yield from chain._real_call(cid, method, candid, cycles))
    what = chain._stub[method]
    if isinstance(what, dict):  # by the path asked for
        what = what[json.loads(unquote(candid))["path"]]
    if method == "get_file_chunk":
        yield from chain._real_call("aaaaa-aa", "raw_rand", "()")  # a registry's reply is an await: a message per chunk
        chain._asked += 1
        if what == "NEVER" or what.startswith("FILE "):
            what = sized_chunk(what, json.loads(unquote(candid))["offset"])
    if method == "store":
        chain.candid_encode(candid)  # what the call does with its argument, which costs the most
        chain._stored.append({"key": candid.split('"')[1], "bytes": candid.count("\\78"),  # "x" is \78 in Candid text
                              "instructions": chain.instructions()})  # all of the message's, up to the call
    if what == "FAIL":
        raise Exception("IC0522: the canister exceeded the instruction limit for a single call")
    return "(" + quote(what) + ")"


chain.call = stubbed
r = Realm["1"]
r.file_registry_canister_id, r.marketplace_canister_id, r.frontend_canister_id = "registry-stub", "mkt", "frontend-stub"
r.require_marketplace_approval, r.trusted_approvers = True, []
print("stubbed")
'''
UNSTUB = r'''
from core import chain
chain.call = chain._real_call
print("restored")
'''


@pytest.fixture(scope="module")
def net(wasm_of, people):
    return Network(wasm_of("production"), people)


@pytest.fixture(scope="module")
def realm(net):
    realm = net.realm("registry")
    assert realm.shell(STUB).strip() == "stubbed"
    yield realm
    assert realm.shell(UNSTUB).strip() == "restored"


def serve(realm, get_file, approval=APPROVED):
    """What the stub answers ``get_file`` (text, or by path) and the approval with; "FAIL" fails the call."""
    approval_text = json.dumps(approval) if isinstance(approval, dict) else approval
    stub = {"get_file": get_file, "get_namespace_approval_icc": approval_text}
    out = realm.shell(f"from core import chain\nchain._stub = {stub!r}\nprint('served')")
    assert out.strip() == "served", out


def installing(realm, **more):
    action = {"op": "codex_install", "args": {"codex": "lex", "version": "1.0.0"}}
    return realm.ok("proposal_create", "alice", title="Install lex", action=action, **more)["proposal"]


def vote(realm, proposal):
    for member in realm.members:
        if realm.proposal(proposal)["status"] != "voting":
            break
        realm.ok("proposal_vote", member, proposal=proposal, choice="yes")
    return realm.proposal(proposal)["status"]


def status(realm):
    return realm.ok("codex_status", "alice", query=True)


DEEP = "[" * 6000  # past where the canister's parse stops
SHAPES = {
    "a manifest that is not JSON": (files("{not json"), APPROVED, "manifest.json is not JSON"),
    "a manifest that is null": (files("null"), APPROVED, "manifest.json: manifest is the codex's manifest.json"),
    "a manifest that is a list": (files("[1]"), APPROVED, "manifest.json: manifest is the codex's manifest.json"),
    "a manifest nested too deep": (files(DEEP), APPROVED, "manifest.json is not JSON"),
    "a manifest nested past a codex's need": (files(json.dumps(LEX)[:-1] + ', "x": ' + "[" * 120 + "]" * 120 + "}"),
                                              APPROVED, "nests lists or records more than 100 levels deep"),
    "an id that is a number": (files(json.dumps({**LEX, "id": 5})), APPROVED, "the manifest's id is text"),
    "code that is not UTF-8": ({"manifest.json": files()["manifest.json"], "backend/hooks.py": json.dumps(
        {"content_b64": b64(b"def init(args):\n    return '\xff\xfe'\n")})}, APPROVED, "is not UTF-8 text"),
    # The canister's base64 is a stub that is lenient where CPython's raises: "abc" decodes to bytes that are no text
    "content that is not base64": ({"manifest.json": json.dumps({"content_b64": "abc"}),
                                    "backend/hooks.py": files()["backend/hooks.py"]}, APPROVED,
                                   ("is not base64", "is not UTF-8 text")),
    "a file reply that is a list": ({"manifest.json": "[1]", "backend/hooks.py": "[1]"}, APPROVED, "is not a record"),
    "a file reply nested too deep": ({"manifest.json": DEEP, "backend/hooks.py": DEEP}, APPROVED, "is not JSON"),
    "a file reply that holds a lone surrogate": ({"manifest.json": r'{"error": "\ud800"}',
                                                  "backend/hooks.py": r'{"error": "\ud800"}'}, APPROVED,
                                                 "its reply holds a lone surrogate"),
    "a call for the manifest that fails": ({"manifest.json": "FAIL", "backend/hooks.py": "FAIL"}, APPROVED,
                                           "get_file for ext/lex/1.0.0/manifest.json failed"),
    "a call for the code that fails": ({"manifest.json": files()["manifest.json"], "backend/hooks.py": "FAIL"},
                                       APPROVED, "get_file for ext/lex/1.0.0/backend/hooks.py failed"),
    "a call for the approval that fails": (files(), "FAIL", "get_namespace_approval_icc for ext/lex/1.0.0 failed"),
    "an approval that is a list": (files(), "[]", "get_namespace_approval_icc ext/lex/1.0.0 is not a record"),
    "an approval nested too deep": (files(), DEEP, "get_namespace_approval_icc ext/lex/1.0.0 is not JSON"),
}


@pytest.fixture(autouse=True)
def no_codex(net, realm):
    """What one case installs does not stay for the next: a case that fails would otherwise fail the rest."""
    yield
    realm.call("codex_uninstall", {}, sender=net.people["ctl"])


def test_a_codex_of_the_registry_that_is_as_the_install_reads_it_installs(net, realm):
    """The control: the stub answers as a registry does, and the install goes through it."""
    serve(realm, files())
    out = realm.call("codex_install", {"codex": "lex", "version": "1.0.0"}, sender=net.people["ctl"])
    assert out["ok"] and out["codex"] == "lex@1.0.0", out
    realm.ok("codex_uninstall", "ctl")
    assert status(realm)["codex"] is None


@pytest.mark.parametrize("name", list(SHAPES))
def test_what_the_registry_sends_or_fails_to_send_is_a_refusal_by_a_call_a_vote_and_a_timer(net, realm, name):
    get_file, approval, reasons = SHAPES[name]
    reasons = (reasons,) if isinstance(reasons, str) else reasons

    def named(error):
        return any(reason in error for reason in reasons)

    serve(realm, get_file, approval)
    direct = realm.call("codex_install", {"codex": "lex", "version": "1.0.0"}, sender=net.people["ctl"])
    assert direct["ok"] is False and named(direct["error"]), direct
    voted = installing(realm)
    assert vote(realm, voted) == "failed"  # the vote's message completes, with the reason
    row = realm.ok("proposal_get", "alice", query=True, proposal=voted)["proposal"]
    assert row["status"] == "failed" and named(row["result"]["error"]), row
    timed = installing(realm, effective_at=realm.now() + HOUR)
    assert vote(realm, timed) == "execution_scheduled"
    net.wait(HOUR + 5)
    row = realm.ok("proposal_get", "alice", query=True, proposal=timed)["proposal"]
    assert row["status"] == "failed" and named(row["result"]["error"]), row  # the timer completed
    assert status(realm)["codex"] is None  # nothing installed


ECHO = "this realm runs codex API 2, not ['x', 'x'"


def test_a_registry_manifest_whose_codex_api_version_echoes_past_the_record_concludes_with_the_marker(net, realm):
    """The refusal names the codex API the manifest asks for, which here is a list of 30,000 texts: larger than a
    proposal's result holds. The vote and the timer conclude, with the reason cut (core.db.bounded, #716)."""
    big = json.dumps({**LEX, "codex_api_version": ["x"] * 30000})
    serve(realm, files(big))
    direct = realm.call("codex_install", {"codex": "lex", "version": "1.0.0"}, sender=net.people["ctl"])
    assert direct["ok"] is False and direct["error"].startswith(ECHO), direct
    for timed in (False, True):
        pid = installing(realm, **({"effective_at": realm.now() + HOUR} if timed else {}))
        assert vote(realm, pid) == ("execution_scheduled" if timed else "failed")
        if timed:
            net.wait(HOUR + 5)
        row = realm.ok("proposal_get", "alice", query=True, proposal=pid)["proposal"]
        error = row["result"]["error"]
        assert row["status"] == "failed" and error.startswith(ECHO), row["status"]
        assert "[cut: the reply was" in error and len(error) < 65_536
    assert status(realm)["codex"] is None


# ---- extension_install: the same registry, and the frontend it copies

BOARD = {"id": "board", "version": "1.0.0", "operations": ["proposal_list"], "profiles": ["member"]}
BOARD_APPROVED = {"namespace": "ext/board/1.0.0", "approved": True, "approver": "mkt"}
LISTING = [{"path": "frontend/a.js", "content_type": "application/javascript"}]
CHUNK = {"content_b64": b64("x = 1"), "length": 5, "eof": True}


def serve_extension(realm, manifest=None, listing=None, chunk=None, store="()", approval=BOARD_APPROVED):
    """The registry holds ``board`` with a frontend of one file, which the realm's frontend canister (a stub) takes."""
    text = json.dumps(BOARD) if manifest is None else manifest
    stub = {"get_file": {"manifest.json": json.dumps({"content_b64": b64(text)})},
            "get_namespace_approval_icc": json.dumps(approval),
            "list_files": json.dumps(LISTING) if listing is None else listing,
            "get_file_chunk": json.dumps(CHUNK) if chunk is None else chunk, "store": store}
    out = realm.shell(f"from core import chain\nchain._stub = {stub!r}\n"
                      "chain._asked, chain._stored = 0, []\nprint('served')")
    assert out.strip() == "served", out


EXTENSION_SHAPES = {
    "a manifest nested past the limit": ({"manifest": json.dumps(BOARD)[:-1] + ', "x": ' + "[" * 120 + "]" * 120 + "}"},
                                         "manifest.json nests lists or records more than 100 levels deep"),
    "a manifest that is a list": ({"manifest": "[1]"}, "manifest.json is a record that is not empty"),
    "operations that are a number": ({"manifest": json.dumps({**BOARD, "operations": 5})},
                                     "manifest.json: operations is a list of text"),
    "a listing that is a record": ({"listing": "{}"}, "reply to list_files ext/board/1.0.0 is not a list"),
    "a listing nested too deep": ({"listing": DEEP}, "reply to list_files ext/board/1.0.0 is not JSON"),
    "an entry without a path": ({"listing": json.dumps([{"content_type": "text/css"}])},
                                "lists an entry without a path"),
    "a listing call that fails": ({"listing": "FAIL"}, "the file registry's list_files for ext/board/1.0.0 failed"),
    "a chunk call that fails": ({"chunk": "FAIL"},
                                "get_file_chunk for ext/board/1.0.0/frontend/a.js failed"),
    "a chunk whose length is text": ({"chunk": json.dumps({**CHUNK, "length": "5"})},
                                     "has no length (a count) or no eof"),
    "a chunk nested too deep": ({"chunk": DEEP}, "reply to get_file_chunk ext/board/1.0.0/frontend/a.js is not JSON"),
    "a frontend that rejects the file": ({"store": "FAIL"}, "the realm's frontend did not store"),
}


def test_an_extension_with_a_frontend_installs_through_the_stubs(net, realm):
    serve_extension(realm)
    out = realm.call("extension_install", {"extension": "board", "version": "1.0.0"}, sender=net.people["ctl"])
    assert out["ok"] and out["extension"] == "board", out
    realm.ok("extension_uninstall", "ctl", extension="board")


@pytest.mark.parametrize("name", list(EXTENSION_SHAPES))
def test_what_the_registry_sends_or_fails_to_send_for_an_extension_is_a_refusal_by_a_call_a_vote_and_a_timer(
        net, realm, name):
    served, reason = EXTENSION_SHAPES[name]
    serve_extension(realm, **served)
    args = {"extension": "board", "version": "1.0.0"}
    direct = realm.call("extension_install", args, sender=net.people["ctl"])
    assert direct["ok"] is False and reason in direct["error"], direct
    for timed in (False, True):
        extra = {"effective_at": realm.now() + HOUR} if timed else {}
        action = {"op": "extension_install", "args": args}
        pid = realm.ok("proposal_create", "alice", title="Install board", action=action, **extra)["proposal"]
        assert vote(realm, pid) == ("execution_scheduled" if timed else "failed")
        if timed:
            net.wait(HOUR + 5)
        row = realm.ok("proposal_get", "alice", query=True, proposal=pid)["proposal"]
        assert row["status"] == "failed" and reason in row["result"]["error"], (timed, row)


# ---- what the install can keep and copy: a manifest its row holds, a file the frontend takes in one call

JSON_MAX = 65_536  # characters of a Json field (core.db)
ROUTES = ["call", "vote", "timer"]
CHUNK_BYTES = 128 * 1024  # what the registry hands out per call


def install_board(net, realm, route):
    """``extension_install`` of board 1.0.0 by a call, by the vote that closes a proposal, or by the timer of one that
    waits: the reply, or what the proposal concluded with (a trap would leave it with no result)."""
    args = {"extension": "board", "version": "1.0.0"}
    if route == "call":
        return realm.call("extension_install", args, sender=net.people["ctl"])
    extra = {"effective_at": realm.now() + HOUR} if route == "timer" else {}
    action = {"op": "extension_install", "args": args}
    pid = realm.ok("proposal_create", "alice", title="Install board", action=action, **extra)["proposal"]
    vote(realm, pid)
    if route == "timer":
        net.wait(HOUR + 5)
        net.wait_until(lambda: realm.proposal(pid).get("result") is not None)  # in flight, it shows no result yet
    row = realm.proposal(pid)
    assert row["status"] in ("executed", "failed"), row
    return row["result"]


def refused(out, reason):
    assert out["ok"] is False and reason in out["error"], out


def left(realm):
    """The chunks the registry was asked for, what the frontend was sent to store, and the extensions installed."""
    seen = json.loads(realm.shell("import json\nfrom core import chain\n"
                                  "print(json.dumps([chain._asked, chain._stored]))"))
    return seen[0], seen[1], [e["id"] for e in realm.ok("extension_list", "alice", query=True)["extensions"]]


def compact(value):
    """A dump as the canister writes it, and the Json fields store it: compact, and non-ASCII text as it is."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def manifest_of_size(chars, unit="x"):
    """The board's manifest, padded with ``unit`` until the canister's dump of it is ``chars`` characters."""
    plain = {**BOARD, "pad": ""}
    room = chars - len(compact(plain))
    return {**plain, "pad": unit * room}


def row_of(realm):
    """The board's access and manifest, as its row keeps them."""
    return json.loads(realm.shell("import json\nfrom ggg import Extension\ne = Extension['board']\n"
                                  "print(json.dumps({'access': e.access, 'manifest': e.manifest}))"))


def with_access_of(chars):
    """A manifest of profiles alone whose access, as a first install keeps it, is ``chars`` characters."""
    return {"profiles": ["x" * (chars - len(compact({"profiles": [""], "departments": []})))]}


@pytest.fixture
def no_board(net, realm):
    yield
    realm.call("extension_uninstall", {"extension": "board"}, sender=net.people["ctl"])


def test_a_manifest_up_to_the_size_of_a_json_field_installs(net, realm, no_board):
    for chars in (60_000, JSON_MAX):
        serve_extension(realm, manifest=compact(manifest_of_size(chars)))
        out = install_board(net, realm, "call")
        assert out["ok"], out
        asked, stored, installed = left(realm)
        assert installed == ["board"] and len(stored) == 1  # the row was written, after the frontend


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("chars", [JSON_MAX + 1, 70_000])
def test_a_manifest_too_large_for_its_row_is_a_refusal_before_the_frontend_is_published(net, realm, chars, route):
    """It passed every check, copied the frontend, and trapped writing the row: a call raised, the vote did, and the
    timer left the proposal executed with no result, and no extension."""
    serve_extension(realm, manifest=compact(manifest_of_size(chars)))
    refused(install_board(net, realm, route), f"ext/board/1.0.0/manifest.json is too large for the realm to keep: "
                                              f"its manifest is {chars} characters, at most {JSON_MAX}")
    assert left(realm) == (0, [], [])  # no chunk asked for, nothing stored, nothing installed


@pytest.mark.parametrize("unit", ["é", "😀"])
def test_the_limit_counts_the_characters_the_canisters_dump_writes_whatever_the_text(net, realm, no_board, unit):
    """The canister's dump keeps non-ASCII text, a character each (in process it escapes it): the check dumps the
    manifest as the field does, so it is exact in both."""
    serve_extension(realm, manifest=compact(manifest_of_size(JSON_MAX, unit)))
    assert install_board(net, realm, "call")["ok"]
    serve_extension(realm, manifest=compact(manifest_of_size(JSON_MAX + 1, unit)))
    refused(install_board(net, realm, "call"), f"its manifest is {JSON_MAX + 1} characters, at most {JSON_MAX}")
    assert left(realm)[1] == []


def test_an_access_that_fills_its_field_exactly_installs(net, realm, no_board):
    serve_extension(realm, manifest=compact(with_access_of(JSON_MAX)))
    assert install_board(net, realm, "call")["ok"]


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("chars", [JSON_MAX + 1, JSON_MAX + 17], ids=["one over", "a manifest that fills its field"])
def test_profiles_the_access_of_a_new_extension_cannot_keep_are_a_refusal_though_the_manifest_fits(net, realm, chars,
                                                                                                    route):
    """The access is the profiles and ``"departments": []``: 17 characters more than the profiles take in the manifest
    (19 in process, where the dump has spaces)."""
    serve_extension(realm, manifest=compact(with_access_of(chars)))
    refused(install_board(net, realm, route), f"its access is {chars} characters, at most {JSON_MAX}")
    assert left(realm) == (0, [], [])


@pytest.mark.parametrize("route", ROUTES)
def test_an_upgrade_whose_profiles_fill_the_field_installs_and_keeps_the_access_its_row_has(net, realm, no_board,
                                                                                            route):
    """The first install sets the access, an upgrade keeps it: the profiles of the new manifest need not fit one."""
    serve_extension(realm)
    assert install_board(net, realm, "call")["ok"]
    kept = row_of(realm)["access"]
    big = with_access_of(JSON_MAX + 1)
    serve_extension(realm, manifest=compact(big))
    out = install_board(net, realm, route)
    assert out["ok"], out
    row = row_of(realm)
    assert row["access"] == kept and row["manifest"] == big  # the new manifest, the old access
    assert len(left(realm)[1]) == 1


def frontend_file_max(realm):
    return int(realm.shell("from api import extensions\nprint(extensions.FRONTEND_FILE_MAX)"))


@pytest.mark.parametrize("size", [1_056_837, None], ids=["the largest bundle file", "the limit"])
def test_a_file_up_to_the_limit_is_copied_whole_within_the_instructions_of_a_message(net, realm, costs, no_board, size):
    """1,056,837 bytes is maplibre-gl.js, the largest file of the extensions' built bundles at the pin bfa69152. The
    store call is one message: the last chunk's decode, the hex of the whole file, its Candid text and the encode of
    that, which is the part that sets the limit (a message may run 40 billion instructions)."""
    size = frontend_file_max(realm) if size is None else size
    serve_extension(realm, chunk=f"FILE {size}")
    out = install_board(net, realm, "call")
    assert out["ok"], out
    asked, [stored], installed = left(realm)
    assert stored["key"] == "/ext/board/1.0.0/frontend/a.js" and stored["bytes"] == size
    assert asked == -(-size // CHUNK_BYTES) and installed == ["board"]
    costs.report(f"extension_install: the message that stores a frontend file of {size:,} bytes",
                 stored["instructions"])
    assert stored["instructions"] < 20_000_000_000  # half of what a message may run


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("never", [True, False], ids=["a registry that never ends the file", "a file too large"])
def test_a_file_larger_than_the_frontend_takes_is_a_refusal_as_soon_as_it_is_over(net, realm, never, route):
    """Each chunk is its own message, so a registry that never says the file has ended was asked for chunks until the
    heap ran out, and kept the realm from stopping; one that ends a file of megabytes got it into one store call that
    cannot be made. Both stop at the limit, with the file named."""
    limit = frontend_file_max(realm)
    listing = [{"path": "frontend/a.js", "content_type": "application/javascript", "size": 3_000_000}]
    serve_extension(realm, chunk="NEVER" if never else "FILE 3000000", listing=json.dumps(listing))
    refused(install_board(net, realm, route), f"ext/board/1.0.0/frontend/a.js is larger than the {limit} bytes the "
                                              "realm copies to its frontend")
    assert left(realm) == (limit // CHUNK_BYTES + 1, [], [])  # it stopped asking, stored nothing, installed nothing


# What the registry says a chunk is must be what it sends: a chunk of no bytes that says it has some, or a byte at a
# time, keeps the loop going where the bytes alone never reach the limit.
EMPTY_CHUNKS = json.dumps({"content_b64": "", "length": CHUNK_BYTES, "eof": False})
BYTE_CHUNKS = json.dumps({"content_b64": b64("x"), "length": 1, "eof": False})
A_JS = "ext/board/1.0.0/frontend/a.js"


@pytest.mark.parametrize("route", ROUTES)
def test_a_registry_that_never_ends_a_file_and_sends_chunks_of_no_bytes_is_a_refusal_at_the_first(net, realm, route):
    serve_extension(realm, chunk=EMPTY_CHUNKS)
    refused(install_board(net, realm, route), f"{A_JS}: the reply to get_file_chunk says {CHUNK_BYTES} bytes and "
                                              "carries 0")
    assert left(realm) == (1, [], [])


@pytest.mark.parametrize("route", ROUTES)
def test_a_registry_that_never_ends_a_file_and_sends_a_byte_a_chunk_is_a_refusal_after_the_chunks_a_file_takes(
        net, realm, route):
    chunks = frontend_file_max(realm) // CHUNK_BYTES + 1
    serve_extension(realm, chunk=BYTE_CHUNKS)
    refused(install_board(net, realm, route), f"{A_JS} comes in more than {chunks} chunks")
    assert left(realm) == (chunks, [], [])  # not the 1.9 million that the bytes alone would take


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("says", [0, 6])
def test_a_chunk_that_says_it_has_other_bytes_than_it_carries_is_a_refusal(net, realm, says, route):
    serve_extension(realm, chunk=json.dumps({"content_b64": b64("x = 1"), "length": says, "eof": True}))
    refused(install_board(net, realm, route), f"{A_JS}: the reply to get_file_chunk says {says} bytes and carries 5")
    assert left(realm) == (1, [], [])


@pytest.mark.parametrize("chunk", ["NEVER", BYTE_CHUNKS], ids=["128 KB chunks", "a byte a chunk"])
def test_the_realm_stops_while_a_registry_that_never_ends_a_file_is_read(net, realm, chunk):
    """A canister that is stopping does not stop while it keeps making calls: a loop that never ended kept the realm
    from stopping. The install now ends in its refusal, and the stop completes."""
    serve_extension(realm, chunk=chunk)
    msg = realm.submit("extension_install", {"extension": "board", "version": "1.0.0"}, sender=net.people["ctl"])
    for _ in range(4):
        net.pic.tick()
    realm.stop()  # raises when it has not completed within 100 rounds
    realm.start()
    [out] = realm.finish(msg)
    assert out["ok"] is False and A_JS in out["error"], out
    assert left(realm)[2] == []
