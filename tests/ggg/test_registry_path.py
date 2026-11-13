"""A codex of the file registry that is not what the install reads is a refusal, never a trap (review of #711).

``codex_install`` of a ``codex`` and a ``version`` fetches the manifest and the code, and parsed what came back without
asking: a manifest that is not JSON raised in the parse, one that is a list or null in the ``.get`` that read it, and
code that is not UTF-8 in the decode. None of them is a ``Fail``, so the operation trapped, and a trap inside a vote's
execution or a timer rolls the message back: the proposal that carried it never concluded. They are Fails now, naming
the file, and the registry's own replies (the file, the approval) are held to the same: a record, or a refusal. So is a
call to the registry that fails (a plain Exception: out of instructions on a large file, a reply over the limit, a reply
that holds a lone surrogate): it traps after the await that made it, and the proposal ended ``executed`` with no result.
"""

import base64
import json

import fake_chain
import pytest
from core import op
from core.db import JSON_MAX
from ggg import Codex, Proposal, Realm
from fake_chain import rejects, traps
from kit import call, fire_timers, later, ok
from test_codex import SOURCE, build, manifest

HOUR = 3600
FOUR = ("alice", "bob", "dana", "carla")
APPROVED = {"namespace": "ext/test/1.0.0", "approved": True, "approver": "mkt"}
GOOD_MANIFEST = json.dumps(manifest(hooks=["init"], capabilities=[]))


def b64(content):
    return base64.b64encode(content if isinstance(content, bytes) else content.encode()).decode()


def serve(manifest_text=GOOD_MANIFEST, hooks=SOURCE, file_reply=None, approval=APPROVED, fail_on=None):
    """The file registry answers ``get_file`` with the content of the manifest and of the code (text or bytes), or with
    ``file_reply`` for both, or with a failed call for the path ``fail_on``; ``get_namespace_approval_icc`` with
    ``approval``."""
    files = {"manifest.json": manifest_text, "backend/hooks.py": hooks}

    def get_file(arg):
        path = json.loads(arg)["path"]
        if path == fail_on:
            return rejects("IC0522: the canister exceeded the instruction limit for a single call")
        return file_reply if file_reply is not None else {"content_b64": b64(files[path])}

    fake_chain.peer("registry", get_file=get_file, get_namespace_approval_icc=approval)
    Realm["1"].file_registry_canister_id, Realm["1"].marketplace_canister_id = "registry", "mkt"


def manifest_with(**changes):
    return json.dumps({**json.loads(GOOD_MANIFEST), **changes})


NESTED = "[" * 200000  # a RecursionError in the parse, not a ValueError

# What the registry can send that the install does not read: the arguments of serve(), and the reason it names.
BAD_FILES = {
    "a manifest that is not JSON": ({"manifest_text": "{not json"}, "manifest.json is not JSON"),
    "an empty manifest file": ({"manifest_text": ""}, "manifest.json is not JSON"),
    "a manifest nested too deep": ({"manifest_text": NESTED}, "manifest.json is not JSON"),
    "a manifest that is null": ({"manifest_text": "null"}, "manifest.json: manifest is the codex's manifest.json"),
    "a manifest that is a list": ({"manifest_text": "[1, 2]"}, "manifest.json: manifest is the codex's manifest.json"),
    "a manifest that is text": ({"manifest_text": '"text"'}, "manifest.json: manifest is the codex's manifest.json"),
    "a manifest that is a number": ({"manifest_text": "5"}, "manifest.json: manifest is the codex's manifest.json"),
    "a manifest that is empty": ({"manifest_text": "{}"}, "manifest.json: manifest is the codex's manifest.json"),
    "an id that is a number": ({"manifest_text": manifest_with(id=5)}, "the manifest's id is text"),
    "a version that is a number": ({"manifest_text": manifest_with(version=5)}, "the manifest's version is text"),
    "hooks that are null": ({"manifest_text": manifest_with(hooks=None)}, "the manifest's hooks is a list of text"),
    "capabilities with a record": ({"manifest_text": manifest_with(capabilities=[{}])},
                                   "the manifest's capabilities is a list of text"),
    "code that is not UTF-8": ({"hooks": b"def init(args):\n    return '\xff\xfe'\n"},
                               "backend/hooks.py is not UTF-8 text"),
    "content that is not base64": ({"file_reply": {"content_b64": "abc"}}, "manifest.json is not base64"),
    "content that is a number": ({"file_reply": {"content_b64": 5}}, "manifest.json: content_b64 is not text"),
    "a file reply that is not JSON": ({"file_reply": "oops, not JSON"},
                                      "reply to get_file ext/test/1.0.0/manifest.json is not JSON"),
    "a file reply that is a list": ({"file_reply": [1]}, "is not a record"),
    "a file reply that is null": ({"file_reply": "null"}, "is not a record"),
    "an approval that is not JSON": ({"approval": "oops"},
                                     "reply to get_namespace_approval_icc ext/test/1.0.0 is not JSON"),
    "an approval that is a list": ({"approval": []},
                                   "reply to get_namespace_approval_icc ext/test/1.0.0 is not a record"),
    "an approval that is null": ({"approval": "null"}, "is not a record"),
    # A reply the parse cannot take: nested so deep that it raises a RecursionError, which is no ValueError
    "a file reply nested too deep": ({"file_reply": NESTED},
                                     "reply to get_file ext/test/1.0.0/manifest.json is not JSON"),
    "an approval nested too deep": ({"approval": NESTED},
                                    "reply to get_namespace_approval_icc ext/test/1.0.0 is not JSON"),
    # A call that fails is a plain Exception, which trapped after the await: the proposal ended executed, with no reason
    "a call for the manifest that is rejected": ({"file_reply": rejects("IC0522: out of instructions")},
                                                 "get_file for ext/test/1.0.0/manifest.json failed"),
    "a call for the manifest that traps": ({"file_reply": traps("the registry trapped")},
                                           "get_file for ext/test/1.0.0/manifest.json failed"),
    "a call for the code that fails": ({"fail_on": "backend/hooks.py"},
                                       "the file registry's get_file for ext/test/1.0.0/backend/hooks.py failed"),
    "a call for the approval that fails": ({"approval": rejects("the reply is over the limit")},
                                           "the file registry's get_namespace_approval_icc for ext/test/1.0.0 failed"),
    "a file reply that holds a lone surrogate": ({"file_reply": r'{"error": "\ud800"}'},
                                                 "its reply holds a lone surrogate (U+D800 to U+DFFF)"),
    "an approval that holds a lone surrogate": ({"approval": r'{"approved": true, "approver": "\ud800"}'},
                                                "its reply holds a lone surrogate (U+D800 to U+DFFF)"),
}


@pytest.fixture(autouse=True)
def realm():
    build(approval=True)  # the default: the registry's approval decides, and a pin is not needed for what it approves
    yield


def install():
    return call("codex_install", "ctl", codex="test", version="1.0.0")


def test_a_codex_of_the_registry_that_is_as_the_install_reads_it_installs():
    serve()
    assert ok("codex_install", "ctl", codex="test", version="1.0.0")["codex"] == "test@1.0.0"


@pytest.mark.parametrize("name", list(BAD_FILES))
def test_what_the_registry_sends_that_the_install_does_not_read_is_a_refusal_that_names_it(name):
    served, reason = BAD_FILES[name]
    serve(**served)
    out = install()  # a call raises nothing: it is a refusal
    assert out["ok"] is False and reason in out["error"], out
    assert Codex["test@1.0.0"] is None


@pytest.mark.parametrize("timed", [False, True], ids=["vote", "timer"])
@pytest.mark.parametrize("name", list(BAD_FILES))
def test_a_proposal_that_installs_such_a_codex_fails_with_the_reason_and_concludes(name, timed):
    served, reason = BAD_FILES[name]
    serve(**served)
    ok("governance_update", "ctl", voting_window=0)
    filing = {"title": "Install test", "action": {"op": "codex_install", "args": {"codex": "test", "version": "1.0.0"}}}
    if timed:
        filing["effective_at"] = fake_chain.now() + HOUR
    pid = ok("proposal_create", "alice", **filing)["proposal"]
    for voter in FOUR:
        ok("proposal_vote", voter, proposal=pid, choice="yes")  # no trap: the last vote runs the action or schedules it
    if timed:
        assert Proposal[pid].status == "execution_scheduled"
        later(HOUR)
        fire_timers()  # no trap in the timer
    row = Proposal[pid]
    assert row.status == "failed" and reason in row.result["error"], row.result
    assert Codex["test@1.0.0"] is None


# A manifest that parses and cannot be dumped. In process there is a window of depths (about 940 here) in which the
# parse succeeds and a dump, deeper in the stack, raises a RecursionError: first the checksum, before the trust policy,
# and a few levels shallower the dump that writes the row. Each trapped all three routes, at a depth that depends on
# the stack under the call. A manifest nests at most ``codex.MANIFEST_DEPTH`` levels, which the same check of a sent
# package and a fetched one enforces (the canister's loads and dumps stop at the same depth, and have no such window).
HEAD = '{"id": "test", "version": "1.0.0", "codex_api_version": 2, "hooks": ["init"], "capabilities": [], "x": '
NESTED_REASON = "the manifest nests lists or records more than 100 levels deep"
WINDOW = range(880, 950, 2)  # below the depth at which dispatch's own parse of a call's arguments gives out


def deep(depth):
    return HEAD + "[" * depth + "]" * depth + "}"


def sent(operation, caller, depth, wrap="{}"):
    """``operation`` of ``caller`` with a package whose manifest is ``depth`` deep, given as text (a dict cannot be)."""
    package = f'{{"manifest": {deep(depth)}, "source": "pass"}}'
    fake_chain.state["caller"] = caller
    return json.loads(op.run(op.dispatch(operation, wrap.replace("{}", package))))


def test_a_manifest_nested_deeper_than_a_codex_needs_is_refused_whatever_the_stack_it_would_be_dumped_in():
    """Every depth of the window: a trap (the Trap of the fake chain) would raise out of the call. Where the parse
    itself runs out of stack, deeper in the registry's route than in a call's arguments, that is the refusal."""
    for depth in WINDOW:
        serve(manifest_text=deep(depth))
        out = install()
        assert out["ok"] is False and (NESTED_REASON in out["error"] or "is not JSON" in out["error"]), (depth, out)
        out = sent("codex_install", "ctl", depth)
        assert out["ok"] is False and NESTED_REASON in out["error"], (depth, out)
    assert Codex["test@1.0.0"] is None


@pytest.mark.parametrize("depth", [101, 300])
def test_a_registry_manifest_nested_deeper_than_a_codex_needs_is_refused_when_called_and_by_vote_and_timer(depth):
    serve(manifest_text=deep(depth))
    assert NESTED_REASON in install()["error"]
    ok("governance_update", "ctl", voting_window=0)
    for timed in (False, True):
        filing = {"title": "Install test",
                  "action": {"op": "codex_install", "args": {"codex": "test", "version": "1.0.0"}}}
        if timed:
            filing["effective_at"] = fake_chain.now() + HOUR
        pid = ok("proposal_create", "alice", **filing)["proposal"]
        for voter in FOUR:
            ok("proposal_vote", voter, proposal=pid, choice="yes")  # no trap
        if timed:
            later(HOUR)
            fire_timers()  # no trap
        row = Proposal[pid]
        assert row.status == "failed" and NESTED_REASON in row.result["error"], (timed, row.result)
    assert Codex["test@1.0.0"] is None


def test_a_sent_package_nested_deeper_than_a_codex_needs_is_refused_where_it_is_called_and_filed():
    filing = '{"title": "Install", "action": {"op": "codex_install", "args": {}}}'
    for operation, caller, wrap in (("codex_install", "ctl", "{}"), ("proposal_create", "alice", filing)):
        out = sent(operation, caller, 101, wrap)
        assert out["ok"] is False and NESTED_REASON in out["error"], out
    assert Proposal.count() == 0 and Codex["test@1.0.0"] is None


def test_a_manifest_of_the_depth_the_codices_have_installs():
    nested = {"x": {"a": [{"b": [{"c": [1]}]}]}}
    serve(manifest_text=json.dumps({**json.loads(GOOD_MANIFEST), **nested}))
    assert ok("codex_install", "ctl", codex="test", version="1.0.0")["codex"] == "test@1.0.0"


def test_a_checksum_that_raises_a_recursion_error_is_a_refusal_not_a_trap(monkeypatch):
    """Whatever the depth limit does not see: the hash of the manifest's JSON is a Fail if the dump runs out."""
    from api import codex

    def exhausted(manifest, source):
        raise RecursionError("maximum recursion depth exceeded while encoding a JSON object")

    monkeypatch.setattr(codex, "checksum", exhausted)
    out = call("codex_install", "ctl", manifest=json.loads(GOOD_MANIFEST), source=SOURCE)
    assert out["ok"] is False and "the manifest is nested too deep to be hashed" in out["error"], out
    serve()
    out = install()
    assert out["ok"] is False and "the manifest is nested too deep to be hashed" in out["error"], out


# ---- a refusal that echoes what the registry sent

ECHO = "this realm runs codex API 2, not ['x', 'x'"

def test_a_registry_manifest_whose_codex_api_version_echoes_past_the_record_concludes_with_the_marker():
    """``runnable`` names the codex API a manifest asks for with ``str()``: a registry's manifest may be a megabyte, so
    the refusal can be larger than ``Proposal.result`` holds, and the write that exceeded it trapped the vote and the
    timer (#716 bounds what a proposal records; the registry path reaches it with no stored action at all)."""
    serve(manifest_text=json.dumps({**json.loads(GOOD_MANIFEST), "codex_api_version": ["x"] * 30000}))
    out = install()
    assert out["ok"] is False and out["error"].startswith(ECHO), out["error"][:80]
    assert len(out["error"]) > JSON_MAX  # a call's reply is not recorded: it comes whole
    ok("governance_update", "ctl", voting_window=0)
    for timed in (False, True):
        filing = {"title": "Install test",
                  "action": {"op": "codex_install", "args": {"codex": "test", "version": "1.0.0"}}}
        if timed:
            filing["effective_at"] = fake_chain.now() + HOUR
        pid = ok("proposal_create", "alice", **filing)["proposal"]
        for voter in FOUR:
            ok("proposal_vote", voter, proposal=pid, choice="yes")  # no trap
        if timed:
            later(HOUR)
            fire_timers()  # no trap
        row = Proposal[pid]
        assert row.status == "failed", (timed, row.status)
        assert row.result["error"].startswith(ECHO)
        assert "[cut: the reply was" in row.result["error"] and len(json.dumps(row.result)) <= JSON_MAX
    assert Codex["test@1.0.0"] is None


# ---- extension_install shares the registry's helpers, and parsed its manifest the same way

EXT_APPROVED = {"namespace": "ext/board/1.0.0", "approved": True, "approver": "mkt"}
EXT_MANIFEST = {"id": "board", "version": "1.0.0", "operations": ["proposal_list"], "profiles": ["member"]}


def serve_extension(manifest_text=None, approval=EXT_APPROVED, file_reply=None):
    text = json.dumps(EXT_MANIFEST) if manifest_text is None else manifest_text

    def get_file(arg):
        if file_reply is not None:
            return file_reply
        assert json.loads(arg)["path"] == "manifest.json"  # a frontend-only extension: no backend, nothing else
        return {"content_b64": b64(text)}

    fake_chain.peer("registry", get_file=get_file, get_namespace_approval_icc=approval)
    Realm["1"].file_registry_canister_id, Realm["1"].marketplace_canister_id = "registry", "mkt"


BAD_EXTENSIONS = {
    "a manifest that is not JSON": ({"manifest_text": "{not json"}, "manifest.json is not JSON"),
    "a manifest nested too deep": ({"manifest_text": NESTED}, "manifest.json is not JSON"),
    "a manifest that is null": ({"manifest_text": "null"}, "manifest.json is a record that is not empty"),
    "a manifest that is a list": ({"manifest_text": "[]"}, "manifest.json is a record that is not empty"),
    "a manifest that is empty": ({"manifest_text": "{}"}, "manifest.json is a record that is not empty"),
    "operations that are a number": ({"manifest_text": json.dumps({**EXT_MANIFEST, "operations": 5})},
                                     "manifest.json: operations is a list of text"),
    "operations with a number in them": ({"manifest_text": json.dumps({**EXT_MANIFEST, "operations": ["a", 5]})},
                                         "manifest.json: operations is a list of text"),
    "profiles that are text": ({"manifest_text": json.dumps({**EXT_MANIFEST, "profiles": "member"})},
                               "manifest.json: profiles is a list of text"),
    "a call for the manifest that fails": ({"file_reply": rejects("IC0522: out of instructions")},
                                           "get_file for ext/board/1.0.0/manifest.json failed"),
    "a call for the approval that fails": ({"approval": rejects("the reply is over the limit")},
                                           "get_namespace_approval_icc for ext/board/1.0.0 failed"),
    "an approval that is a list": ({"approval": []}, "get_namespace_approval_icc ext/board/1.0.0 is not a record"),
}


def test_an_extension_of_the_registry_that_is_as_the_install_reads_it_installs():
    serve_extension()
    out = ok("extension_install", "ctl", extension="board", version="1.0.0")
    assert out["extension"] == "board" and out["approved_by"] == "mkt"


@pytest.mark.parametrize("name", list(BAD_EXTENSIONS))
def test_an_extension_of_the_registry_that_is_not_what_the_install_reads_is_a_refusal_on_every_route(name):
    served, reason = BAD_EXTENSIONS[name]
    serve_extension(**served)
    out = call("extension_install", "ctl", extension="board", version="1.0.0")
    assert out["ok"] is False and reason in out["error"], out
    ok("governance_update", "ctl", voting_window=0)
    for timed in (False, True):
        filing = {"title": "Install board",
                  "action": {"op": "extension_install", "args": {"extension": "board", "version": "1.0.0"}}}
        if timed:
            filing["effective_at"] = fake_chain.now() + HOUR
        pid = ok("proposal_create", "alice", **filing)["proposal"]
        for voter in FOUR:
            ok("proposal_vote", voter, proposal=pid, choice="yes")  # no trap
        if timed:
            later(HOUR)
            fire_timers()
        row = Proposal[pid]
        assert row.status == "failed" and reason in row.result["error"], (timed, row.result)


# An extension's manifest is held to the depth a codex's is: in process a manifest nested about 940 deep parses, and
# the dump of it (the checksum, the row that keeps it) raised a RecursionError at a depth that depends on the stack, a
# trap by a call, a vote and a timer. (The guard around the checksum that this replaces was never reached by a test.)
EXT_NESTED = "manifest.json nests lists or records more than 100 levels deep"


def deep_extension(depth):
    return json.dumps(EXT_MANIFEST)[:-1] + ', "x": ' + "[" * depth + "]" * depth + "}"


def test_an_extension_manifest_nested_deeper_than_a_codex_needs_is_refused_at_every_depth_of_the_window():
    """Every depth in which the parse succeeds and a dump, deeper in the stack, gives out: a trap (the Trap of the fake
    chain) would raise out of the call. Where the parse itself runs out of stack, that is the refusal."""
    nested = 0
    for depth in WINDOW:
        serve_extension(manifest_text=deep_extension(depth))
        out = call("extension_install", "ctl", extension="board", version="1.0.0")
        assert out["ok"] is False and (EXT_NESTED in out["error"] or "is not JSON" in out["error"]), (depth, out)
        nested += EXT_NESTED in out["error"]
    assert nested  # the limit, not only the parse, refused some of them


@pytest.mark.parametrize("timed", [None, False, True], ids=["call", "vote", "timer"])
def test_an_extension_manifest_942_levels_deep_is_refused_by_a_call_a_vote_and_a_timer(timed):
    serve_extension(manifest_text=deep_extension(942))
    if timed is None:
        out = call("extension_install", "ctl", extension="board", version="1.0.0")
        assert out["ok"] is False and EXT_NESTED in out["error"], out
        return
    ok("governance_update", "ctl", voting_window=0)
    filing = {"title": "Install board",
              "action": {"op": "extension_install", "args": {"extension": "board", "version": "1.0.0"}}}
    if timed:
        filing["effective_at"] = fake_chain.now() + HOUR
    pid = ok("proposal_create", "alice", **filing)["proposal"]
    for voter in FOUR:
        ok("proposal_vote", voter, proposal=pid, choice="yes")  # no trap
    if timed:
        later(HOUR)
        fire_timers()  # no trap
    row = Proposal[pid]
    assert row.status == "failed" and EXT_NESTED in row.result["error"], row.result


def test_an_extension_manifest_of_the_depth_the_extensions_have_installs():
    serve_extension(manifest_text=json.dumps({**EXT_MANIFEST, "x": {"a": [{"b": [{"c": [1]}]}]}}))
    assert ok("extension_install", "ctl", extension="board", version="1.0.0")["extension"] == "board"


# ---- the frontend an extension brings is copied from the registry in chunks: the same replies, the same refusals

LISTING = [{"path": "frontend/a.js", "content_type": "application/javascript"}, {"path": "README.md"}]
CHUNK = {"content_b64": b64("x = 1"), "length": 5, "eof": True}
FRONTEND_PATH = "ext/board/1.0.0/frontend/a.js"


def serve_frontend(listing=None, chunk=None, store=None):
    """The registry holds ``board`` with a frontend of one file; the realm has a frontend canister to store it in."""
    listing = LISTING if listing is None else listing
    fake_chain.peer("registry", get_file=lambda arg: {"content_b64": b64(json.dumps(EXT_MANIFEST))},
                    get_namespace_approval_icc=EXT_APPROVED, list_files=listing,
                    get_file_chunk=CHUNK if chunk is None else chunk)
    stored = fake_chain.peer("frontend", store=store if store is not None else "()")
    Realm["1"].file_registry_canister_id, Realm["1"].marketplace_canister_id = "registry", "mkt"
    Realm["1"].frontend_canister_id = "frontend"
    return stored


BAD_FRONTENDS = {
    "a listing that is not JSON": ({"listing": "oops"}, "reply to list_files ext/board/1.0.0 is not JSON"),
    "a listing nested too deep": ({"listing": NESTED}, "reply to list_files ext/board/1.0.0 is not JSON"),
    "a listing that is a record": ({"listing": "{}"}, "reply to list_files ext/board/1.0.0 is not a list"),
    "a listing that is null": ({"listing": "null"}, "reply to list_files ext/board/1.0.0 is not a list"),
    "an entry that is a number": ({"listing": [5]}, "lists an entry without a path"),
    "an entry without a path": ({"listing": [{"content_type": "text/css"}]}, "lists an entry without a path"),
    "a path that is a number": ({"listing": [{"path": 5}]}, "lists an entry without a path"),
    "a content type that is a number": ({"listing": [{"path": "frontend/a.js", "content_type": 5}]},
                                        "or with a content type that is not text"),
    "a listing call that fails": ({"listing": rejects("IC0522: out of instructions")},
                                  "the file registry's list_files for ext/board/1.0.0 failed"),
    "a chunk that is not JSON": ({"chunk": "oops"}, f"reply to get_file_chunk {FRONTEND_PATH} is not JSON"),
    "a chunk nested too deep": ({"chunk": NESTED}, f"reply to get_file_chunk {FRONTEND_PATH} is not JSON"),
    "a chunk that is a list": ({"chunk": [1]}, f"reply to get_file_chunk {FRONTEND_PATH} is not a record"),
    "a chunk whose content is a number": ({"chunk": {**CHUNK, "content_b64": 5}}, "content_b64 is not text"),
    "a chunk whose content is not base64": ({"chunk": {**CHUNK, "content_b64": "abc"}}, "is not base64"),
    "a chunk whose length is text": ({"chunk": {**CHUNK, "length": "5"}}, "has no length (a count) or no eof"),
    "a chunk whose length is negative": ({"chunk": {**CHUNK, "length": -1}}, "has no length (a count) or no eof"),
    "a chunk whose length is true": ({"chunk": {**CHUNK, "length": True}}, "has no length (a count) or no eof"),
    "a chunk without eof": ({"chunk": {"content_b64": CHUNK["content_b64"], "length": 5}},
                            "has no length (a count) or no eof"),
    "a chunk call that fails": ({"chunk": rejects("the reply is over the limit")},
                                f"the file registry's get_file_chunk for {FRONTEND_PATH} failed"),
    "a chunk that holds a lone surrogate": ({"chunk": r'{"error": "\ud800"}'}, "its reply holds a lone surrogate"),
    "a frontend that rejects the file": ({"store": rejects("the canister is out of cycles")},
                                         "the realm's frontend did not store /ext/board/1.0.0/frontend/a.js"),
}


def test_an_extension_with_a_frontend_installs_and_its_file_is_stored():
    stored = serve_frontend()
    assert ok("extension_install", "ctl", extension="board", version="1.0.0")["extension"] == "board"
    [(method, candid)] = stored.calls
    assert method == "store" and 'key = "/ext/board/1.0.0/frontend/a.js"' in candid


@pytest.mark.parametrize("name", list(BAD_FRONTENDS))
def test_a_frontend_the_registry_does_not_serve_as_the_install_reads_it_is_a_refusal_on_every_route(name):
    served, reason = BAD_FRONTENDS[name]
    serve_frontend(**served)
    out = call("extension_install", "ctl", extension="board", version="1.0.0")
    assert out["ok"] is False and reason in out["error"], out
    ok("governance_update", "ctl", voting_window=0)
    for timed in (False, True):
        filing = {"title": "Install board",
                  "action": {"op": "extension_install", "args": {"extension": "board", "version": "1.0.0"}}}
        if timed:
            filing["effective_at"] = fake_chain.now() + HOUR
        pid = ok("proposal_create", "alice", **filing)["proposal"]
        for voter in FOUR:
            ok("proposal_vote", voter, proposal=pid, choice="yes")  # no trap
        if timed:
            later(HOUR)
            fire_timers()  # no trap
        row = Proposal[pid]
        assert row.status == "failed" and reason in row.result["error"], (timed, row.result)
