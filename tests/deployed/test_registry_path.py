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
import json
from core import chain
from core.candid import quote, unquote
from ggg import Realm

if not hasattr(chain, "_real_call"):
    chain._real_call = chain.call
chain._stub = {}


def stubbed(cid, method, candid="()", cycles=0):
    if cid not in ("registry-stub", "frontend-stub"):
        return (yield from chain._real_call(cid, method, candid, cycles))
    what = chain._stub[method]
    if isinstance(what, dict):  # by the path asked for
        what = what[json.loads(unquote(candid))["path"]]
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
    out = realm.shell(f"from core import chain\nchain._stub = {stub!r}\nprint('served')")
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
