"""Lone surrogates (core.surrogates): no operation takes one, and no reply from another canister brings one in.

The canister's ``json.loads`` makes a lone surrogate of the escape ``"\\ud800"``, and its ``json.dumps`` writes it as it
is. Neither the stable map nor Candid can hold one: a call that carried one trapped where it first wrote or replied with
it, with a Python error for its message. These tests run in process, where the stable map is a dict that takes anything,
so they hold what the realm refuses before it gets there; tests/deployed/test_lone_surrogates.py holds the refusal of
the map itself, and the refusals on the real wasm.
"""

import base64
import hashlib
import json

import fake_chain
import pytest
import world
from api import extensions
from core import layered_codex, op, records, surrogates
from core.db import Fail
from fake_chain import peer
from ggg import Event, Organization, PendingAction, Proposal, Realm, SourceRecord, SyncSource
from kit import direct, ok
from test_installs import LIST_URL, job, run_job
from test_records import SRC, URL, capital, entry, failing, stand_in

LONE = "\ud800"


@pytest.fixture(autouse=True)
def realm():
    world.build()
    SyncSource(id=SRC, name="The source", status="active")


# ---- what a lone surrogate is

TEXTS = {"": True, "plain": True, "é日本語": True, "😀": True, "\ud83d\ude00": False, LONE: False, "a" + LONE + "b": False,
         "é" + "\udfff": False}


@pytest.mark.parametrize("text, encodable", TEXTS.items(), ids=[repr(t) for t in TEXTS])
def test_a_text_is_encodable_when_it_holds_no_lone_surrogate(text, encodable):
    assert surrogates.encodable(text) is encodable


def test_a_pair_the_parser_joins_is_a_character_and_two_surrogates_python_does_not_join_are_lone():
    assert json.loads('"\\ud83d\\ude00"') == "😀" and surrogates.encodable("😀")
    assert not surrogates.encodable("\ud83d\ude00")


def test_holds_finds_a_text_at_any_depth_and_in_a_key():
    assert not surrogates.holds({"a": [1, {"b": ("c", None, True, 1.5, b"\xff")}]})
    assert surrogates.holds(LONE)
    assert surrogates.holds({"a": [1, {"b": ["x", {"c": ("d", LONE)}]}]})
    assert surrogates.holds({"a" + LONE: 1})


def test_holds_reads_data_nested_deeper_than_python_recurses():
    nested = LONE
    for _ in range(5000):
        nested = [nested]
    assert surrogates.holds(nested)


def test_culprit_names_the_first_entry_that_holds_one():
    assert surrogates.culprit({"a": 1, "b": ["x"]}) is None
    assert surrogates.culprit({"a": 1, "b": ["x", {"c": LONE}], "d": LONE}) == "b"
    assert surrogates.culprit({"a" + LONE: 1}) == "a" + LONE


@pytest.mark.parametrize("text, holds", [
    ('{"a": "\\ud800"}', True), ('["x", {"k": "y\\uDFFF"}]', True), ('{"\\ud800": 1}', True),
    ('{"a": "\\ud83d\\ude00"}', False),  # a pair is a character
    ('{"a": "\\\\ud800"}', False),  # a backslash, then the letters: the scan finds it, the parse does not
    ('{"a": "ud800"}', False), ("plain text", False), ("x = '\\ud800'", False),  # Python source is no JSON
    ('{"a": "\\ud800"', False),  # nor is a broken one
])
def test_json_holds_what_the_text_holds_once_it_is_parsed(text, holds):
    assert surrogates.json_holds(text) is holds


# ---- a call that holds one is refused

CALLS = [
    ("proposal_create", {"title": "Road" + LONE}),
    ("proposal_create", {"title": "Road", "description": "x" + LONE}),
    ("proposal_create", {"title": "Road", "tags": ["parks", "p" + LONE]}),
    ("proposal_create", {"title": "Road", "kind": LONE}),
    ("proposal_create", {"title": "Road", "action": {"op": "organization_register", "args": {"organization": "x" + LONE}}}),
    ("proposal_create", {"title": "Road", "links": [{"kind": "relates_to", "proposal": "P-1" + LONE}]}),
    ("proposal_create", {"title": "Road", LONE: 1}),
    ("organization_register", {"organization": "club" + LONE}),
    ("organization_register", {"organization": "club", "description": LONE}),
    ("notification_send", {"recipient": "User:bob", "title": "Hello" + LONE}),
    ("notification_send", {"recipient": "User:bob", "title": "Hello", "translations": [{"language": "es", "body": LONE}]}),
    ("profile_update", {"nickname": "Ali" + LONE}),
    ("request_file", {"kind": "petition", "body": LONE}),
    ("proposal_vote", {"proposal": "P-1", "choice": "yes", "comment": LONE}),
]


@pytest.mark.parametrize("name, args", CALLS, ids=[f"{n} {sorted(a)}" for n, a in CALLS])
def test_a_call_with_a_lone_surrogate_anywhere_in_its_arguments_is_refused(name, args):
    before = (Proposal.count(), Organization.count(), Event.count())
    out = direct(name, "alice", **args)
    culprit = surrogates.culprit(args)
    assert out["ok"] is False and out["kind"] == "invalid_argument", out
    assert out["error"] == f"argument {culprit!r} holds a lone surrogate (U+D800 to U+DFFF), which is not text"
    assert (Proposal.count(), Organization.count(), Event.count()) == before  # nothing is stored, not even an audit event


def test_every_operation_refuses_a_lone_surrogate_before_anything_else():
    """Before the permission, the arguments the operation needs and the state of the realm: none of the 270 operations
    can keep what it never sees."""
    names = sorted(op.ops())
    assert len(names) > 250
    for name in names:
        for caller in ("alice", "ctl"):
            out = direct(name, caller, note="x" + LONE)
            assert out["ok"] is False and "holds a lone surrogate" in out["error"], (name, out)
        # a read refuses it the same; an update called as a read is refused for that, as it was
        reply = json.loads(op.run(op.dispatch(name, json.dumps({"note": LONE}), query=True)))
        assert reply["ok"] is False and ("holds a lone surrogate" in reply["error"]
                                         or "changes the realm" in reply["error"]), name


@pytest.mark.parametrize("origin", ["user", "replay", "codex", "extension", "confirmed"])
def test_a_call_is_refused_whichever_way_it_reaches_the_operation(origin):
    """A stored proposal's action is replayed, a task's step, a hook's effect and an extension's call run through the
    same dispatch."""
    out = json.loads(op.run(op.dispatch("organization_register", {"organization": "club" + LONE}, origin=origin,
                                        signer="alice")))
    assert out["ok"] is False and out["kind"] == "invalid_argument"
    assert Organization["club" + LONE] is None


def test_a_pair_written_as_escapes_is_one_character_and_is_taken():
    p = ok("proposal_create", "alice", title="Party 😀 \ud83d\ude00")["proposal"]
    assert Proposal[p].title == "Party 😀 😀"  # kit writes the escapes, the parser joins them


def test_two_surrogates_that_python_did_not_join_are_refused():
    """What a pair is in a ``str`` that a caller from Python (the shell, a codex) builds."""
    out = json.loads(op.run(op.dispatch("proposal_create", {"title": "\ud83d\ude00"}, signer="alice")))
    assert out["ok"] is False and out["kind"] == "invalid_argument"


def test_a_delegates_call_that_asks_the_member_is_refused_before_it_is_filed():
    """pending.prepare runs before the permission is checked: the refusal must come before it, or an action with the
    text would be filed, and shown to the member, who could approve it."""
    account = dict(network="ICP", address="aaaaa-bbbbb", symbol="REALM")
    ok("delegation_grant", "alice", delegate="claude-conn", groups=["payout"], expires_in=86400)
    filed = ok("payment_account_add", "claude-conn", on_behalf_of="alice", label="Savings", **account)
    assert filed["pending"].startswith("AC-") and PendingAction.count() == 1  # what the same call does without it
    out = direct("payment_account_add", "claude-conn", on_behalf_of="alice", label="Sav" + LONE, **account)
    assert out["ok"] is False and out["kind"] == "invalid_argument", out
    assert out["error"].startswith("argument 'label' holds a lone surrogate")
    assert PendingAction.count() == 1


@pytest.fixture
def walked(monkeypatch):
    """The arguments ``dispatch`` walked in search of a lone surrogate."""
    seen = []
    culprit = surrogates.culprit
    monkeypatch.setattr(surrogates, "culprit", lambda args: seen.append(args) or culprit(args))
    return seen


@pytest.mark.parametrize("arg, looked", [
    ('{"a": ["x", {"b": "é日本語"}]}', False),  # no escape in the text: there is nothing to look for
    ("{}", False), ("", False),
    ('{"a": "\\ud83d\\ude00"}', True),  # the start of an escape, a pair here
    ('{"a": "\\uD800"}', True),
    ('{"a": "x%s"}' % LONE, True),  # a text made in the canister may hold the surrogate itself
    ({"a": "x"}, True),  # a call from Python is always looked at
])
def test_the_arguments_are_walked_only_when_the_text_of_the_call_may_hold_one(walked, arg, looked):
    json.loads(op.run(op.dispatch("version", arg, query=True)))
    assert bool(walked) is looked


def test_a_text_made_in_the_canister_that_holds_the_surrogate_itself_is_refused():
    reply = json.loads(op.run(op.dispatch("proposal_create", '{"title": "x%s"}' % LONE, signer="alice")))
    assert reply["ok"] is False and reply["error"].startswith("argument 'title' holds a lone surrogate")


# ---- a reply that holds one is refused

REASON = r"holds a lone surrogate \(U\+D800 to U\+DFFF\), which is not text"


def test_a_realms_record_that_holds_one_is_refused_like_a_call_that_failed():
    """The realm would keep it in the source's record, which the stable map refuses: a trap in the timer of every check."""
    capital(entry("eu", notes={"en": "First" + LONE}), source="eu/es")  # the peer's json writes the escape
    failing(SRC, f"{SRC}: legislation_releases on {SRC} failed: its reply {REASON}")
    assert SourceRecord.count() == 0


def test_a_reply_that_holds_a_pair_is_read():
    capital(entry("eu", notes={"en": "First 😀"}), source="eu/es")
    assert op.run(records.read_record(SRC))["releases"][0]["notes"] == {"en": "First 😀"}


def test_a_peer_that_replies_with_a_surrogate_fails_the_call_of_text_and_of_an_operation():
    peer("quarter", status=json.dumps({"ok": True, "name": "Quarter" + LONE}))
    with pytest.raises(Exception, match=f"status on quarter failed: its reply {REASON}"):
        op.run(fake_chain.call_text("quarter", "status"))
    with pytest.raises(Exception, match=f"status on quarter failed: its reply {REASON}"):
        op.run(fake_chain.call_op("quarter", "status", {}))


def test_a_stand_in_record_that_holds_one_is_refused_naming_its_url():
    text = json.dumps({"source": "eu", "releases": [entry("eu", notes={"en": LONE})]})
    stand_in(text)
    failing(URL, f"{URL}: {REASON}")
    assert SourceRecord.count() == 0


def test_a_body_fetched_by_https_is_refused_when_it_is_json_and_holds_one():
    body = json.dumps({"id": "lex" + LONE}).encode()
    with pytest.raises(Fail, match=f"{URL}: {REASON}"):
        layered_codex.decoded(URL, 200, body, 1024, is_json=True)
    assert layered_codex.decoded(URL, 200, body, 1024) == body.decode()  # a chapter is code: no JSON is parsed from it


def test_code_is_not_scanned_even_when_it_starts_with_a_string_that_holds_an_escape():
    """The canister's json.loads reads the first value and ignores the rest, so code that begins with a docstring would
    be taken for JSON that holds one: only the bodies that are JSON are scanned."""
    code = b'"\\ud800 a docstring"\nx = "\\ud800"\n'
    assert layered_codex.decoded(URL, 200, code, 1024) == code.decode()


def test_a_change_list_that_holds_one_fails_the_install_job_naming_the_reason():
    ok("governance_update", "ctl", voting_window=0)
    text = json.dumps({"kind": "realms-gos/legislation-change", "format": 1, "realm": "arcadia", "replaces": None,
                       "set": [], "remove": [], "release": None, "summary": {"en": "Water" + LONE}}).encode()
    fake_chain.serve(LIST_URL, text)
    ok("codex_install", "ctl", url=LIST_URL, checksum="sha256:" + hashlib.sha256(text).hexdigest())
    run_job()
    assert job()["status"] == "failed" and f"{LIST_URL}: holds a lone surrogate" in job()["error"], job()


def test_a_json_file_of_the_registry_that_holds_one_is_refused_inside_its_base64():
    """The file comes in the reply as base64, which the scan of the reply cannot see into."""
    Realm["1"].file_registry_canister_id = "registry"
    files = {"manifest.json": json.dumps({"name": "x", "note": "a" + LONE}), "other.json": json.dumps({"note": "😀"}),
             "backend/entry.py": "x = '\\ud800'\n"}
    peer("registry", get_file=lambda arg: {"content_b64": base64.b64encode(files[json.loads(arg)["path"]].encode()).decode()})
    with pytest.raises(Fail, match=f"ext/x/1/manifest.json: {REASON}"):
        op.run(extensions.fetch("ext/x/1", "manifest.json"))
    for path in ("other.json", "backend/entry.py"):  # a pair is a character, and code is no JSON
        assert op.run(extensions.fetch("ext/x/1", path)) == files[path]


def test_a_check_that_meets_one_records_why_the_source_is_unknown():
    """``failed`` keeps the reason of any check that raised: the flag against the source is unknown, and why."""
    from api import sync_checks
    from core import compliance

    text = json.dumps({"source": "eu", "releases": [entry("eu", notes={"en": LONE})]})
    stand_in(text)
    op.run(sync_checks.check())
    link = SyncSource[URL]
    assert link.compliance == compliance.valid("unknown") and "lone surrogate" in link.check_error
