"""A codex of the file registry that is not what the install reads is a refusal, never a trap (review of #711).

``codex_install`` of a ``codex`` and a ``version`` fetches the manifest and the code, and parsed what came back without
asking: a manifest that is not JSON raised in the parse, one that is a list or null in the ``.get`` that read it, and
code that is not UTF-8 in the decode. None of them is a ``Fail``, so the operation trapped, and a trap inside a vote's
execution or a timer rolls the message back: the proposal that carried it never concluded. They are Fails now, naming
the file, and the registry's own replies (the file, the approval) are held to the same: a record, or a refusal.
"""

import base64
import json

import fake_chain
import pytest
from ggg import Codex, Proposal, Realm
from kit import call, fire_timers, later, ok
from test_codex import SOURCE, build, manifest

HOUR = 3600
FOUR = ("alice", "bob", "dana", "carla")
APPROVED = {"namespace": "ext/test/1.0.0", "approved": True, "approver": "mkt"}
GOOD_MANIFEST = json.dumps(manifest(hooks=["init"], capabilities=[]))


def b64(content):
    return base64.b64encode(content if isinstance(content, bytes) else content.encode()).decode()


def serve(manifest_text=GOOD_MANIFEST, hooks=SOURCE, file_reply=None, approval=APPROVED):
    """The file registry answers ``get_file`` with the content of the manifest and of the code (text or bytes), or with
    ``file_reply`` for both; ``get_namespace_approval_icc`` with ``approval``."""
    files = {"manifest.json": manifest_text, "backend/hooks.py": hooks}

    def get_file(arg):
        return file_reply if file_reply is not None else {"content_b64": b64(files[json.loads(arg)["path"]])}

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
    "content that is not base64": ({"file_reply": {"content_b64": "abc"}}, "is not UTF-8 text"),
    "content that is a number": ({"file_reply": {"content_b64": 5}}, "is not UTF-8 text"),
    "a file reply that is not JSON": ({"file_reply": "oops, not JSON"},
                                      "reply to get_file ext/test/1.0.0/manifest.json is not JSON"),
    "a file reply that is a list": ({"file_reply": [1]}, "is not a record"),
    "a file reply that is null": ({"file_reply": "null"}, "is not a record"),
    "an approval that is not JSON": ({"approval": "oops"},
                                     "reply to get_namespace_approval_icc ext/test/1.0.0 is not JSON"),
    "an approval that is a list": ({"approval": []},
                                   "reply to get_namespace_approval_icc ext/test/1.0.0 is not a record"),
    "an approval that is null": ({"approval": "null"}, "is not a record"),
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
