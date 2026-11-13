"""``codex_install`` takes arguments of the types the install reads, or says which is not (review of realms-gos#695).

A manifest that is null, an empty string or a list, sent code that is not text: the install raised on them, so the call
trapped, and a trap inside a vote's execution or a timer rolls the message back, so a proposal that carried them could
never conclude. They are refused when the install is filed and when it is called; one stored before then fails, with the
reason recorded, and concludes.
"""

import json
import os

import fake_chain
import ggg_api
import pytest
import world
from api import extensions
from core import legislation, op
from ggg import Proposal, Realm
from kit import denied, fire_timers, later, ok

HOUR = 3600
FOUR = ("alice", "bob", "dana", "carla")  # more than half of the seven members: enough to carry a vote
MANIFEST = {"id": "lex", "version": "1.0", "codex_api_version": 2}
ROAD_WORKS = {"op": "mandate_create", "args": {"title": "Road works"}}
CHECKSUM = "sha256:" + "a" * 64
RELEASE = {"source": "arcadia", "version": "1.0.0"}

# What the install reads and does not find: the reason it names, by the arguments.
MALFORMED = {
    "a release with a null manifest": ({**RELEASE, "manifest": None}, "manifest is the codex's manifest.json"),
    "a release with an empty manifest text": ({**RELEASE, "manifest": ""}, "manifest is the codex's manifest.json"),
    "a release with a manifest that is a list": ({**RELEASE, "manifest": []}, "manifest is the codex's manifest.json"),
    "a release with an empty manifest": ({**RELEASE, "manifest": {}}, "manifest is the codex's manifest.json"),
    "a null manifest": ({"manifest": None, "source": "pass"}, "manifest is the codex's manifest.json"),
    "a manifest that is text": ({"manifest": "lex", "source": "pass"}, "manifest is the codex's manifest.json"),
    "a manifest that is a number": ({"manifest": 5, "source": "pass"}, "manifest is the codex's manifest.json"),
    "code that is a number": ({"manifest": MANIFEST, "source": 5}, "source is the codex's code"),
    "code that is null": ({"manifest": MANIFEST, "source": None}, "source is the codex's code"),
    "a manifest with no id": ({"manifest": {"version": "1.0"}, "source": "pass"}, "the manifest's id is text"),
    "a manifest whose version is a number": ({"manifest": {"id": "lex", "version": 1}, "source": "pass"},
                                             "the manifest's version is text"),
    "capabilities that are text": ({"manifest": {**MANIFEST, "capabilities": "read"}, "source": "pass"},
                                   "the manifest's capabilities is a list of text"),
    "capabilities with a record in them": ({"manifest": {**MANIFEST, "capabilities": [{}]}, "source": "pass"},
                                           "the manifest's capabilities is a list of text"),
    "capabilities with a list in them": ({"manifest": {**MANIFEST, "capabilities": [[1]]}, "source": "pass"},
                                         "the manifest's capabilities is a list of text"),
    "hooks that are null": ({"manifest": {**MANIFEST, "hooks": None}, "source": "pass"},
                            "the manifest's hooks is a list of text"),
    "hooks that are a number": ({"manifest": {**MANIFEST, "hooks": 5}, "source": "pass"},
                                "the manifest's hooks is a list of text"),
    "code that does not compile": ({"manifest": MANIFEST, "source": "def ("}, "source does not compile"),
    "code with a null byte": ({"manifest": MANIFEST, "source": "x = 1\x00"}, "source does not compile"),
    "a codex name that is a number": ({"codex": 5, "version": "1.0.0"}, "codex is the name of a codex"),
    "a codex name that is a list": ({"codex": ["lex"], "version": "1.0.0"}, "codex is the name of a codex"),
    "a codex version that is null": ({"codex": "lex", "version": None}, "version is the version of that codex"),
    "a release whose source is a number": ({"source": 5, "version": "1.0.0"}, "source is the id of a layer"),
    "a release whose version is a number": ({"source": "arcadia", "version": 5}, "version is the release's version"),
    "a release whose version is null": ({"source": "arcadia", "version": None}, "version is the release's version"),
    "a checksum that is a number": ({"checksum": 5}, "checksum is sha256"),
    "a change list address that is a number": ({"checksum": CHECKSUM, "url": 5}, "url is the address"),
}


# Through the GGG methods (GGG_MIRROR) a call's arguments are Candid values: a text that is null is not one, and the
# mirror turns it into an empty text or none, so the realm never sees the null these cases are about.
NULL_TEXT = {"code that is null", "a codex version that is null", "a release whose version is null"}


@pytest.fixture(autouse=True)
def realm():
    world.build()
    Realm["1"].require_marketplace_approval = False  # sent code then passes the trust policy: only the check stops it
    legislation._state.update(codex=legislation.UNKNOWN, box=None, failing=False)
    yield
    legislation.load(None)


def vote_yes(proposal):
    for voter in FOUR:
        ok("proposal_vote", voter, proposal=proposal, choice="yes")


@pytest.mark.parametrize("name", list(MALFORMED))
def test_arguments_of_the_wrong_types_are_refused_when_the_install_is_filed_and_when_it_is_called(name):
    if name in NULL_TEXT and os.environ.get("GGG_MIRROR"):
        pytest.skip("GGG's Candid types carry no null text: the mirror changes the argument")
    args, reason = MALFORMED[name]
    action = {"op": "codex_install", "args": args}
    assert reason in denied("proposal_create", "alice", title="Install", action=action)
    assert reason in denied("codex_install", "ctl", **args)  # a call raises nothing: it is a refusal
    assert Proposal.count() == 0


STORED = ["a release with a null manifest", "a manifest that is text", "code that is a number",
          "a release whose version is a number", "hooks that are null", "capabilities with a record in them",
          "code that does not compile", "code with a null byte"]


@pytest.mark.parametrize("name", STORED)
def test_a_stored_proposal_with_such_arguments_fails_with_its_reason_and_concludes(name):
    """One stored before the refusal: its vote closes, the action fails (a Fail, recorded), and nothing traps."""
    args, reason = MALFORMED[name]
    ok("governance_update", "ctl", voting_window=0)
    p = ok("proposal_create", "alice", title="Road works", action=ROAD_WORKS)["proposal"]
    Proposal[p].action = {"op": "codex_install", "args": args}  # as stored
    vote_yes(p)  # no trap: the last vote runs the action in its own message
    row = Proposal[p]
    assert row.status == "failed" and row.result["ok"] is False and reason in row.result["error"]


@pytest.mark.parametrize("name", STORED)
def test_a_stored_proposal_that_waits_for_its_effective_time_fails_there_and_concludes(name):
    args, reason = MALFORMED[name]
    ok("governance_update", "ctl", voting_window=0)
    p = ok("proposal_create", "alice", title="Road works", action=ROAD_WORKS,
           effective_at=fake_chain.now() + HOUR)["proposal"]
    Proposal[p].action = {"op": "codex_install", "args": args}  # as stored, before the vote closes
    vote_yes(p)  # the deciding vote stages what the action names: it raises nothing
    assert Proposal[p].status == "execution_scheduled"
    later(HOUR)
    fire_timers()  # the timer raises nothing
    row = Proposal[p]
    assert row.status == "failed" and reason in row.result["error"]
    from core import layered_codex as store

    assert store.live_job() is None  # no install is left running to refuse the next one


# What a proposal can carry that a filing now refuses: args that are not a record (stored, they trapped in the vote, in
# the message that stages an install at the deciding vote, and in the view GGG has of the action).
NOT_A_RECORD = {"text": "checksum", "a list": ["checksum"], "a number": 5, "true": True}
NOT_A_RECORD_REASON = "args a record"


@pytest.mark.parametrize("name", list(NOT_A_RECORD))
def test_args_that_are_not_a_record_are_refused_when_filed_and_called(name):
    if os.environ.get("GGG_MIRROR"):
        pytest.skip("through the GGG methods an action is a Candid value: it cannot carry args that are not a record")
    args = NOT_A_RECORD[name]
    action = {"op": "codex_install", "args": args}
    assert NOT_A_RECORD_REASON in denied("proposal_create", "alice", title="Install", action=action)
    fake_chain.state["caller"] = "ctl"
    for operation in ("codex_install", "mandate_create"):  # any operation: dispatch refuses it, as a refusal
        out = json.loads(op.run(op.dispatch(operation, json.dumps(args))))
        assert out["ok"] is False and "are a record" in out["error"]
    assert Proposal.count() == 0


@pytest.mark.parametrize("name", list(NOT_A_RECORD))
def test_a_stored_action_whose_args_are_not_a_record_fails_and_concludes_and_ggg_can_read_it(name):
    args = NOT_A_RECORD[name]
    stored = {"op": "codex_install", "args": args}
    ok("governance_update", "ctl", voting_window=0)
    p = ok("proposal_create", "alice", title="Road works", action=ROAD_WORKS)["proposal"]
    Proposal[p].action = stored
    vote_yes(p)  # no trap in the vote
    row = Proposal[p]
    assert row.status == "failed" and NOT_A_RECORD_REASON in row.result["error"]
    call = ggg_api.action(stored)["Call"]  # GGG's view of the action keeps the operation and its JSON
    assert call["method"] == "codex_install" and json.loads(call["arg"]) == args
    # and the one that waits for its time
    q = ok("proposal_create", "alice", title="Road works", action=ROAD_WORKS,
           effective_at=fake_chain.now() + HOUR)["proposal"]
    Proposal[q].action = stored
    vote_yes(q)  # the deciding vote stages nothing: it does not trap in prepare
    assert Proposal[q].status == "execution_scheduled"
    later(HOUR)
    fire_timers()
    assert Proposal[q].status == "failed" and NOT_A_RECORD_REASON in Proposal[q].result["error"]


def test_well_typed_arguments_get_past_the_check_to_the_reason_of_their_own():
    """A package of text, a release, a codex of the registry and a change list: each is refused here for what it lacks,
    not for its types (code that is not trusted, no layer of the realm's own, no file registry, an address that is not
    https), or accepted as before."""
    Realm["1"].require_marketplace_approval = True
    assert "code sha256:" in denied("codex_install", "ctl", manifest=MANIFEST, source="def hook(): pass")
    assert "no layer of its own yet" in denied("codex_install", "ctl", **RELEASE)
    assert "no file registry is configured" in denied("codex_install", "ctl", codex="lex", version="1.0")
    assert "fetched over https" in denied("codex_install", "ctl", checksum=CHECKSUM, url="http://example.test/a.json")


def test_a_package_of_well_typed_arguments_installs_as_before():
    """The compile check does not turn a good package away: sent code that compiles, hooks and capabilities as text."""
    manifest = {**MANIFEST, "hooks": ["init"], "capabilities": ["realm.data_view"]}
    source = "from ggg_sdk import hook\n\n@hook\ndef init(args):\n    return {}\n"
    reply = ok("codex_install", "ctl", manifest=manifest, source=source)
    assert reply["codex"] == "lex@1.0"


# Code the compiler cannot take: nested so deep that it raises a RecursionError, a MemoryError or a SyntaxError of its
# own. Each is catchable, and each trapped the install, so that the vote or timer that carried it never concluded
# (review of realms-gos#697, with the approval the realm requires by default).
DEEP = {"binary operators": "1" + "+1" * 5000, "unary operators": "-" * 5000 + "1", "nots": "not " * 5000 + "x",
        "lambdas": "lambda: " * 3000 + "1"}
NOT_TRUSTED = "neither allowlisted nor approved"


def trust_policy(approval, source):
    """The realm's trust policy: no approval required, approval required (this code unpinned), or its digest pinned."""
    realm = Realm["1"]
    realm.require_marketplace_approval = approval != "off"
    if approval == "pinned":
        realm.trusted_approvers = [f"sha256:{extensions.checksum(MANIFEST, source)}"]


@pytest.mark.parametrize("approval", ["off", "on", "pinned"])
@pytest.mark.parametrize("name", list(DEEP))
def test_code_the_compiler_cannot_take_is_a_refusal_never_a_trap_whatever_the_trust_policy(name, approval):
    """Code the realm would refuse as untrusted is refused for that, without compiling it (what it did before the
    check); code it takes, because it asks no approval or pins the digest, is refused where it is filed and where it is
    called, with the compiler's reason, and one stored before concludes failed at the vote and at the timer."""
    source, reason = DEEP[name], "source does not compile"
    args = {"manifest": MANIFEST, "source": source}
    trust_policy(approval, source)
    filed = {"op": "codex_install", "args": args}
    if approval == "on":
        assert NOT_TRUSTED in denied("codex_install", "ctl", **args)  # base's order: the trust refusal first
        # filed all the same: trust is asked when the vote has passed, as before the check
        assert ok("proposal_create", "alice", title="Install", action=filed)["proposal"]
        reason = NOT_TRUSTED
    else:
        assert reason in denied("codex_install", "ctl", **args)
        assert reason in denied("proposal_create", "alice", title="Install", action=filed)
    ok("governance_update", "ctl", voting_window=0)
    for timed in (False, True):  # a stored one, as it would have been filed before the check
        extra = {"effective_at": fake_chain.now() + HOUR} if timed else {}
        pid = ok("proposal_create", "alice", title="Road works", action=ROAD_WORKS, **extra)["proposal"]
        Proposal[pid].action = filed
        vote_yes(pid)  # no trap: the last vote runs the action, or schedules it
        if timed:
            assert Proposal[pid].status == "execution_scheduled"
            later(HOUR)
            fire_timers()  # no trap in the timer
            fake_chain.state["timers"].clear()
        row = Proposal[pid]
        assert row.status == "failed" and reason in row.result["error"], (timed, row.status, row.result)


def test_an_exception_whose_text_cannot_be_made_is_still_a_refusal(monkeypatch):
    class Unprintable(Exception):
        def __str__(self):
            raise RuntimeError("no text")

    def refuse(*args, **kwargs):
        raise Unprintable()

    from api import codex

    monkeypatch.setattr("builtins.compile", refuse, raising=False)
    with pytest.raises(codex.Fail, match="source does not compile: Unprintable"):
        codex.compiled("pass")


# Text that is not UTF-8: the canister's JSON accepts a lone surrogate, and the hash of the text's bytes raises a
# UnicodeEncodeError, which dispatch trapped on (review of realms-gos#697, with approval required or not). Dispatch now
# refuses the call, a stored action too when it runs (core.surrogates): a Fail naming the argument, before the install's
# own checks.
SURROGATES = {"in the code": {"manifest": MANIFEST, "source": "x = '\ud800'\n"},
              "alone, with a manifest that cannot run": {"manifest": {"id": "lex", "version": "1.0"},
                                                         "source": "\ud800"}}


@pytest.mark.parametrize("approval", ["off", "on"])
@pytest.mark.parametrize("name", list(SURROGATES))
def test_text_that_is_not_utf8_is_a_refusal_never_a_trap(name, approval):
    args = SURROGATES[name]
    trust_policy(approval, "pass")
    refusal = "argument '%s' holds a lone surrogate (U+D800 to U+DFFF), which is not text"
    action = {"op": "codex_install", "args": args}
    assert refusal % "source" in denied("codex_install", "ctl", **args)
    assert refusal % "action" in denied("proposal_create", "alice", title="Install", action=action)
    ok("governance_update", "ctl", voting_window=0)
    for timed in (False, True):  # stored, as it would have been filed before the check
        extra = {"effective_at": fake_chain.now() + HOUR} if timed else {}
        pid = ok("proposal_create", "alice", title="Road works", action=ROAD_WORKS, **extra)["proposal"]
        Proposal[pid].action = action
        vote_yes(pid)  # no trap: the last vote runs the action, or schedules it
        if timed:
            later(HOUR)
            fire_timers()  # no trap in the timer
            fake_chain.state["timers"].clear()
        row = Proposal[pid]
        assert row.status == "failed" and refusal % "source" in row.result["error"], (timed, row.status, row.result)
