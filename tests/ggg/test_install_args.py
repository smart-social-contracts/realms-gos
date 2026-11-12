"""``codex_install`` takes arguments of the types the install reads, or says which is not (review of realms-gos#695).

A manifest that is null, an empty string or a list, sent code that is not text: the install raised on them, so the call
trapped, and a trap inside a vote's execution or a timer rolls the message back, so a proposal that carried them could
never conclude. They are refused when the install is filed and when it is called; one stored before then fails, with the
reason recorded, and concludes.
"""

import fake_chain
import pytest
import world
from core import legislation
from ggg import Proposal
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
                                   "the manifest's capabilities is a list"),
    "a codex name that is a number": ({"codex": 5, "version": "1.0.0"}, "codex is the name of a codex"),
    "a codex name that is a list": ({"codex": ["lex"], "version": "1.0.0"}, "codex is the name of a codex"),
    "a codex version that is null": ({"codex": "lex", "version": None}, "version is the version of that codex"),
    "a release whose source is a number": ({"source": 5, "version": "1.0.0"}, "source is the id of a layer"),
    "a release whose version is a number": ({"source": "arcadia", "version": 5}, "version is the release's version"),
    "a release whose version is null": ({"source": "arcadia", "version": None}, "version is the release's version"),
    "a checksum that is a number": ({"checksum": 5}, "checksum is sha256"),
    "a change list address that is a number": ({"checksum": CHECKSUM, "url": 5}, "url is the address"),
}


@pytest.fixture(autouse=True)
def realm():
    world.build()
    legislation._state.update(codex=legislation.UNKNOWN, box=None, failing=False)
    yield
    legislation.load(None)


def vote_yes(proposal):
    for voter in FOUR:
        ok("proposal_vote", voter, proposal=proposal, choice="yes")


@pytest.mark.parametrize("name", list(MALFORMED))
def test_arguments_of_the_wrong_types_are_refused_when_the_install_is_filed_and_when_it_is_called(name):
    args, reason = MALFORMED[name]
    action = {"op": "codex_install", "args": args}
    assert reason in denied("proposal_create", "alice", title="Install", action=action)
    assert reason in denied("codex_install", "ctl", **args)  # a call raises nothing: it is a refusal
    assert Proposal.count() == 0


@pytest.mark.parametrize("name", ["a release with a null manifest", "a manifest that is text", "code that is a number",
                                  "a release whose version is a number"])
def test_a_stored_proposal_with_such_arguments_fails_with_its_reason_and_concludes(name):
    """One stored before the refusal: its vote closes, the action fails (a Fail, recorded), and nothing traps."""
    args, reason = MALFORMED[name]
    ok("governance_update", "ctl", voting_window=0)
    p = ok("proposal_create", "alice", title="Road works", action=ROAD_WORKS)["proposal"]
    Proposal[p].action = {"op": "codex_install", "args": args}  # as stored
    vote_yes(p)  # no trap: the last vote runs the action in its own message
    row = Proposal[p]
    assert row.status == "failed" and row.result["ok"] is False and reason in row.result["error"]


def test_a_stored_proposal_that_waits_for_its_effective_time_fails_there_and_concludes():
    ok("governance_update", "ctl", voting_window=0)
    p = ok("proposal_create", "alice", title="Road works", action=ROAD_WORKS,
           effective_at=fake_chain.now() + HOUR)["proposal"]
    vote_yes(p)
    assert Proposal[p].status == "execution_scheduled"
    Proposal[p].action = {"op": "codex_install", "args": {**RELEASE, "manifest": None}}  # as stored
    later(HOUR)
    fire_timers()  # the timer raises nothing
    row = Proposal[p]
    assert row.status == "failed" and "manifest is the codex's manifest.json" in row.result["error"]


def test_well_typed_arguments_get_past_the_check_to_the_reason_of_their_own():
    """A package of text, a release, a codex of the registry and a change list: each is refused here for what it lacks,
    not for its types (code that is not trusted, no layer of the realm's own, no file registry, an address that is not
    https), or accepted as before."""
    assert "code sha256:" in denied("codex_install", "ctl", manifest=MANIFEST, source="def hook(): pass")
    assert "no layer of its own yet" in denied("codex_install", "ctl", **RELEASE)
    assert "no file registry is configured" in denied("codex_install", "ctl", codex="lex", version="1.0")
    assert "fetched over https" in denied("codex_install", "ctl", checksum=CHECKSUM, url="http://example.test/a.json")
