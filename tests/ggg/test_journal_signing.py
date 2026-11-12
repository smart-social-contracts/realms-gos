"""The documents of the official journal and their signatures (docs/codices/journal-signature.md), in process: the
journal as the first kind of document of core.signing (tests/ggg/test_signing.py tests the facility on its own).

Every closed day is made into a document, linked to the one before it, whether or not anyone signs it. A realm signs a day
when someone asks (``journal_sign``, the default) or each day as its sweep makes it (``journal_signing`` is ``daily``).

The management canister's Schnorr key is played by a peer that signs deterministically (``management``): what is
tested here is what the realm asks for, keeps and serves. tests/deployed/test_journal_signing.py runs the same on the
wasm with the IC's own key, and checks the signatures as Ed25519."""

import hashlib
import json
from datetime import datetime, timedelta, timezone

import fake_chain
import pytest
import world
from core import journal, journal_signing, legislation, localtime, op, signing
from core.candid import Reader, idl_hash
from ggg import Event, Extension, Realm, SignedDocument
from ic_python_db import Entity
from kit import call, ok
from signing_kit import climb, fake_key, fake_signature, management

DAY = 86400
FIELDS = ["codex", "layers", "proposal", "activates_at", "reverted", "source", "version", "effective_from"]
BEFORE_THE_FIRST_MIDNIGHT = (2026, 11, 9, 10)
ANONYMOUS = fake_chain.ANONYMOUS


@pytest.fixture(autouse=True)
def utc_clock():
    localtime.use("UTC")
    signing.IN_FLIGHT.clear()


def at(*when):
    fake_chain.state["now"] = int(datetime(*when, tzinfo=timezone.utc).timestamp())


def sweep():
    """One sweep of the realm, as its timer runs it."""
    op.run(journal_signing.sweep())


def daily():
    """The realm signs each day as its sweep makes it."""
    Realm["1"].journal_signing = "daily"


def zone(name):
    """The realm's founder moves it to ``name``, as setup and a realm-wide vote do."""
    Realm["1"].timezone = name
    localtime.use(name)
    journal.fix_first_day()


def failure_of(kind):
    return ok("codex_status", "ctl")["signing_failures"].get(kind)


def rules():
    Extension(id="journal", manifest={"config": {"personal_data": {"entry_fields": FIELDS}}})


def issue(day=None, who="alice", **more):
    args = {"day": day} if day else {}
    return ok("journal_issue", who, **args, **more)["issue"]


def started(*when):
    """A realm in its first day, with the management canister and the journal's rules in place."""
    at(*(when or BEFORE_THE_FIRST_MIDNIGHT))
    world.build()
    rules()
    return management()


def signed(day):
    proof = issue(day)["proof"]
    return proof, json.loads(proof["canonical"])


def made(day):
    """The text of the document of ``day``, signed or not."""
    return json.loads(signing.proof("journal", day)["canonical"])


def ask(day, who="alice"):
    """Somebody asks the realm to sign ``day``: the reply of ``journal_sign``."""
    return ok("journal_sign", who, day=day)


def refused(day, who="alice", **more):
    out = call("journal_sign", who, **({"day": day} if day is not None else {}), **more)
    assert not out["ok"], out
    return out["kind"]


def in_days(count):
    """The nights that pass from the first one, one sweep for each, as the realm's hourly timer would run."""
    for n in range(1, count + 1):
        at(2026, 11, 9 + n, 0, 5)
        sweep()


# ---- Today's issue is provisional; a closed day is made into a document at the next sweep


def test_todays_issue_is_provisional_and_has_no_proof():
    started()
    sweep()
    assert issue()["status"] == "provisional" and "proof" not in issue()


def test_a_realm_signs_on_request_unless_it_says_otherwise():
    started()
    assert Realm["1"].journal_signing == "on_request"
    assert ok("realm_info", "alice")["realm"]["journal_signing"] == "on_request"


def test_a_closed_day_is_made_at_the_next_sweep_and_not_before_and_asks_for_no_signature():
    peer = started()
    at(2026, 11, 9, 23, 59)
    sweep()
    assert peer.calls == [] and SignedDocument.count() == 0  # the 9th is not over
    at(2026, 11, 10, 0, 1)
    assert issue("2026-11-09")["status"] == "unsigned" and SignedDocument.count() == 0  # closed, and not made yet
    sweep()
    assert SignedDocument.count() == 1 and peer.sent("sign_with_schnorr") == []
    assert len(peer.sent("schnorr_public_key")) == 1
    assert fake_chain.state["attached"] == [("aaaaa-aa", "schnorr_public_key", 0)]  # no cycles go with it
    assert issue("2026-11-09")["status"] == "unsigned" and "proof" not in issue("2026-11-09")
    assert issue("2026-11-10")["status"] == "provisional"


def test_the_sweep_with_nothing_to_make_or_sign_asks_nobody_in_either_mode():
    peer = started()
    at(2026, 11, 10, 0, 5)
    sweep()
    ask("2026-11-09")  # the one day there is, signed
    calls = len(peer.calls)
    for mode in ("on_request", "daily"):
        Realm["1"].journal_signing = mode
        for _ in range(3):
            sweep()
    assert len(peer.calls) == calls


def test_the_text_of_a_day_holds_the_realm_the_day_the_entries_the_key_and_when_it_was_made():
    started()
    journal.record("codex_installed", codex="sha256:one", proposal="P-1", nickname="never kept")
    at(2026, 11, 10, 0, 5)
    sweep()
    doc = made("2026-11-09")
    assert doc == {
        "v": 1, "kind": "journal", "realm": fake_chain.self_id(), "subject": "2026-11-09",
        "body": {"number": 1, "timezone": "UTC", "entries": issue("2026-11-09")["entries"]}, "prev": None,
        "made_at": fake_chain.now(),
        "key": {"algorithm": "ed25519", "name": "key_1", "path": "realms:journal",
                "public_key": fake_key("key_1").hex()}}
    assert doc["body"]["entries"][0]["details"] == {"codex": "sha256:one", "proposal": "P-1"}


def test_the_text_is_canonical_json_and_its_digest_and_code_follow_from_it_and_a_signature_adds_only_the_signature():
    started()
    at(2026, 11, 10, 0, 5)
    sweep()
    unsigned = signing.proof("journal", "2026-11-09")
    text = unsigned["canonical"]
    assert text == json.dumps(made("2026-11-09"), sort_keys=True, separators=(",", ":")) and text.isascii()
    assert unsigned["digest"] == hashlib.sha256(text.encode()).hexdigest() and unsigned["code"] == signing.code_of(unsigned["digest"])
    assert unsigned["root"] == unsigned["signature"] == "" and unsigned["path"] == []
    digits = unsigned["code"].split("-")
    assert [len(part) for part in digits] == [4, 4, 4, 4] and all(part.isalnum() and part.isupper() for part in digits)
    ask("2026-11-09")
    proof, _ = signed("2026-11-09")
    assert (proof["canonical"], proof["digest"], proof["code"]) == (text, unsigned["digest"], unsigned["code"])
    assert proof["root"] == proof["digest"] and proof["path"] == []  # a batch of one: its root is the digest
    assert proof["signature"] == fake_signature(bytes.fromhex(proof["root"])).hex()  # it signs the root


def test_the_realm_asks_the_management_canister_for_its_own_key_on_the_journals_path():
    peer = started()
    at(2026, 11, 10, 0, 5)
    sweep()
    ask("2026-11-09")
    [key_args], [sign_args] = peer.sent("schnorr_public_key"), peer.sent("sign_with_schnorr")  # the key is asked once
    asked = Reader(key_args).args()[0]
    assert asked["canister_id"] is None  # the caller's own key
    assert asked["derivation_path"] == [b"realms:journal"]  # the journal's own: it signs nothing else
    assert asked["key_id"] == {"algorithm": {"ed25519": None}, "name": "key_1"}
    assert Reader(sign_args).args()[0]["derivation_path"] == [b"realms:journal"]


def test_the_management_canisters_field_numbers_are_the_ones_the_realm_reads():
    assert idl_hash("public_key") == 881_350_601 and idl_hash("signature") == 359_375_608


# ---- A day is signed when someone asks, by anyone, once


def test_a_request_signs_the_day_and_returns_its_proof_to_anyone_even_an_anonymous_caller():
    peer = started()
    at(2026, 11, 10, 0, 5)
    sweep()
    reply = ask("2026-11-09", ANONYMOUS)
    proof = reply["proof"]
    assert (reply["day"], reply["status"]) == ("2026-11-09", "signed")
    assert proof == issue("2026-11-09")["proof"] == ok("signature_proof", "alice", kind="journal", subject="2026-11-09")["proof"]
    assert issue("2026-11-09")["status"] == "signed"
    assert proof["signature"] == fake_signature(bytes.fromhex(proof["root"])).hex()
    assert len(peer.sent("sign_with_schnorr")) == 1


def test_a_second_request_returns_the_same_proof_for_nothing_and_makes_no_call_at_all():
    peer = started()
    at(2026, 11, 10, 0, 5)
    sweep()
    first = ask("2026-11-09")
    calls, attached = len(peer.calls), len(fake_chain.state["attached"])
    for who in ("alice", ANONYMOUS, "bob"):
        assert ask("2026-11-09", who) == first
    sweep()
    assert len(peer.calls) == calls and len(fake_chain.state["attached"]) == attached


def test_a_day_is_signed_once_however_many_ask_so_the_cost_is_never_above_that_of_signing_every_day():
    peer = started()
    in_days(4)
    days = [f"2026-11-{d:02d}" for d in (9, 10, 11, 12)]
    for day in days:
        for who in ("alice", "bob", ANONYMOUS):
            ask(day, who)
    assert len(peer.sent("sign_with_schnorr")) == len(days)  # twelve requests, four signatures
    assert [issue(day)["status"] for day in days] == ["signed"] * 4


def test_a_request_is_refused_for_a_day_not_closed_before_the_journal_began_or_that_is_no_date_and_signs_nothing():
    peer = started()
    at(2026, 11, 10, 0, 5)
    sweep()
    assert refused("2026-11-10") == "conflict" and refused("2026-11-11") == "conflict"  # today, and tomorrow
    assert refused("2026-11-08") == "not_found"  # the journal began on the 9th
    for bad in (None, "", "tomorrow", "2026-02-30", "2026-11-9", 20261109):
        assert refused(bad) == "invalid_argument", bad
    assert peer.sent("sign_with_schnorr") == []


def test_a_day_that_closed_within_the_hour_is_made_and_signed_by_the_request_that_comes_first():
    peer = started()
    at(2026, 11, 10, 0, 1)  # the sweep has not run
    assert SignedDocument.count() == 0
    assert ask("2026-11-09")["status"] == "signed"
    assert SignedDocument.count() == 1 and len(peer.sent("sign_with_schnorr")) == 1


def test_a_request_for_a_later_day_makes_the_days_before_it_so_that_the_chain_has_no_gap_and_signs_only_that_day():
    peer = started()
    at(2026, 11, 12, 0, 1)  # the 9th, 10th and 11th have closed, and nothing is made
    ask("2026-11-11")
    assert SignedDocument.count() == 3 and len(peer.sent("sign_with_schnorr")) == 1
    assert [issue(f"2026-11-{d:02d}")["status"] for d in (9, 10, 11)] == ["unsigned", "unsigned", "signed"]
    assert made("2026-11-11")["prev"] == signing.proof("journal", "2026-11-10")["digest"]


def test_a_request_for_a_day_the_realm_could_not_make_is_an_error_and_may_be_asked_again():
    peer = started()
    at(2026, 11, 10, 0, 1)
    peer.answer("schnorr_public_key", fake_chain.rejects("management canister busy"))
    out = call("journal_sign", ANONYMOUS, day="2026-11-09")
    assert out["kind"] == "unavailable" and "busy" not in out["error"] and SignedDocument.count() == 0  # told only: not now
    assert "busy" in failure_of("journal")["error"]  # the admins read why
    management()
    assert ask("2026-11-09")["status"] == "signed"


def test_a_signature_that_fails_on_request_is_an_error_kept_for_the_admins_and_the_day_may_be_asked_again():
    peer = started()
    at(2026, 11, 10, 0, 5)
    sweep()
    peer.answer("sign_with_schnorr", fake_chain.rejects("Rejection code 4, 5 cycles attached, 26_153_846_153 required"))
    out = call("journal_sign", ANONYMOUS, day="2026-11-09")
    assert not out["ok"] and out["kind"] == "unavailable"
    assert "26_153_846_153" not in out["error"] and "cycles" not in out["error"]  # what the management canister said is not told
    failure = failure_of("journal")
    assert failure["subjects"] == ["2026-11-09"] and failure["at"] == fake_chain.now() and "26_153_846_153" in failure["error"]
    assert issue("2026-11-09")["status"] == "unsigned" and not signing.IN_FLIGHT
    management()  # the cycles are back
    assert ask("2026-11-09")["status"] == "signed" and failure_of("journal") is None


def test_a_day_being_signed_is_pending_for_the_next_request_which_asks_for_no_second_signature():
    peer = started()
    at(2026, 11, 10, 0, 5)
    sweep()
    plain, seen = peer.replies["sign_with_schnorr"], []

    def meanwhile(arg):
        peer.answer("sign_with_schnorr", plain)
        seen.append(ask("2026-11-09", ANONYMOUS))  # another call arrives while the signature is out
        return plain(arg)

    peer.answer("sign_with_schnorr", meanwhile)
    first = ask("2026-11-09")
    assert seen == [{"ok": True, "day": "2026-11-09", "status": "pending"}] and first["status"] == "signed"
    assert len(peer.sent("sign_with_schnorr")) == 1


def test_a_quarter_issues_nothing_signs_nothing_and_refuses_a_request():
    peer = started()
    Realm["1"].is_quarter, Realm["1"].is_capital = True, False
    at(2026, 11, 12, 0, 5)
    sweep()
    assert peer.calls == [] and SignedDocument.count() == 0
    assert ok("journal_issue", "alice")["issued"] is False
    assert refused("2026-11-09") == "conflict" and peer.calls == []


def test_a_signature_made_is_on_the_audit_trail_once_and_calls_that_change_nothing_leave_no_event():
    started()
    at(2026, 11, 10, 0, 5)
    sweep()
    before = Event.count()
    digest = ok("journal_sign", ANONYMOUS, day="2026-11-09", junk="x" * 500)["proof"]["digest"]  # an argument it does not take
    event = Event[str(Event.max_id())]
    assert Event.count() == before + 1
    assert (event.op, event.user, event.args, event.result) == ("journal_sign", ANONYMOUS, {"day": "2026-11-09"},
                                                                {"day": "2026-11-09", "digest": digest})
    for _ in range(3):
        ask("2026-11-09", ANONYMOUS)  # already signed
    refused("2026-11-10"), refused("tomorrow")
    assert Event.count() == before + 1


def test_no_legislation_runs_for_a_request_so_a_codex_can_neither_forbid_nor_hook_it(monkeypatch):
    started()
    at(2026, 11, 10, 0, 5)
    sweep()

    def never(*args, **kwargs):
        raise AssertionError("legislation ran for journal_sign")

    monkeypatch.setattr(legislation, "_guard", never)
    monkeypatch.setattr(legislation, "_hooks", never)
    assert op.OPS["journal_sign"]["only_reads"] and ask("2026-11-09")["status"] == "signed"


# ---- Every day is made, each linking to the one before it, whether or not it is signed


def test_every_closed_day_is_made_each_linking_to_the_one_before_by_its_digest_with_no_signature():
    peer = started()
    journal.record("codex_installed", codex="sha256:0")  # the 9th
    at(2026, 11, 10, 0, 5)
    sweep()
    at(2026, 11, 11, 0, 5)
    sweep()  # the 10th has no entries, and is made
    journal.record("codex_installed", codex="sha256:2")  # the 11th
    at(2026, 11, 12, 0, 5)
    sweep()
    days = [f"2026-11-{d:02d}" for d in (9, 10, 11)]
    docs, proofs = [made(day) for day in days], [signing.proof("journal", day) for day in days]
    assert [d["body"]["number"] for d in docs] == [1, 2, 3] and docs[1]["body"]["entries"] == []
    assert [d["prev"] for d in docs] == [None, proofs[0]["digest"], proofs[1]["digest"]]
    assert [p["signature"] for p in proofs] == [""] * 3 and peer.sent("sign_with_schnorr") == []
    assert journal.sealed_head() == {"subject": "2026-11-11", "digest": proofs[2]["digest"]}


def test_one_signature_covers_every_day_before_it_through_the_digests_of_days_nobody_signed():
    peer = started()
    in_days(5)
    days = [f"2026-11-{d:02d}" for d in range(9, 14)]
    ask(days[0])  # the first day is signed on request,
    ask(days[4])  # and so is the fifth, with the three between them unsigned
    assert [issue(day)["status"] for day in days] == ["signed", "unsigned", "unsigned", "unsigned", "signed"]
    texts = [signing.proof("journal", day)["canonical"] for day in days]
    assert made(days[4])["prev"] == signing.proof("journal", days[3])["digest"]  # the fifth names the fourth
    for later, earlier in zip(days[1:], texts):
        assert made(later)["prev"] == hashlib.sha256(earlier.encode()).hexdigest()  # and each names the one before
    assert made(days[0])["prev"] is None and len(peer.sent("sign_with_schnorr")) == 2


def test_days_that_closed_while_the_realm_could_not_make_them_are_made_oldest_first_by_one_call_for_the_key():
    peer = started()
    at(2026, 11, 13, 0, 5)  # the 9th, 10th, 11th and 12th have closed
    sweep()
    assert [made(f"2026-11-{d:02d}")["body"]["number"] for d in (9, 10, 11, 12)] == [1, 2, 3, 4]
    assert len(peer.sent("schnorr_public_key")) == 1 and peer.sent("sign_with_schnorr") == []
    assert made("2026-11-12")["prev"] == signing.proof("journal", "2026-11-11")["digest"]
    assert issue("2026-11-13")["status"] == "provisional"


def test_a_long_backlog_is_made_in_batches_of_a_month():
    peer = started()
    at(2027, 1, 20, 0, 5)  # 72 days have closed
    sweep()
    assert len(peer.sent("schnorr_public_key")) == 3 and SignedDocument.count() == 72 and peer.sent("sign_with_schnorr") == []
    assert made("2027-01-19")["body"]["number"] == 72


def test_an_issue_made_late_says_when_it_was_made():
    started()
    at(2026, 11, 12, 6)
    sweep()
    assert made("2026-11-09")["made_at"] == fake_chain.now() and issue("2026-11-09")["entries"] == []


def test_the_realm_makes_its_closed_days_on_the_first_sweep_after_an_upgrade():
    peer = started()
    at(2026, 11, 10, 0, 5)
    fake_chain.state["timers"].clear()
    journal_signing.resume()  # main.py's post_upgrade: its timers are gone
    [(seconds, sweeping)] = [t for t in fake_chain.state["timers"] if t[1] is journal_signing.sweep]
    assert seconds == 0
    op.run(sweeping())
    assert SignedDocument.count() == 1 and peer.sent("sign_with_schnorr") == []


def test_every_sweep_sets_the_next_in_an_hour():
    started()
    fake_chain.state["timers"].clear()
    sweep()
    assert fake_chain.state["timers"] == [(3600, journal_signing.sweep)]


# ---- A realm that signs daily signs each day as it makes it


def test_a_daily_realm_signs_a_day_at_the_sweep_that_makes_it_and_not_before():
    peer = started()
    daily()
    at(2026, 11, 9, 23, 59)
    sweep()
    assert peer.sent("sign_with_schnorr") == []  # the 9th is not over
    at(2026, 11, 10, 0, 1)
    sweep()
    assert issue("2026-11-09")["status"] == "signed" and issue("2026-11-10")["status"] == "provisional"
    assert len(peer.sent("sign_with_schnorr")) == 1 and len(peer.sent("schnorr_public_key")) == 1  # the key is asked once


def test_a_daily_realms_days_are_signed_one_signature_a_day_each_linking_to_the_one_before_by_its_digest():
    peer = started()
    daily()
    journal.record("codex_installed", codex="sha256:0")
    at(2026, 11, 10, 0, 5)
    sweep()
    at(2026, 11, 11, 0, 5)
    sweep()
    at(2026, 11, 12, 0, 5)
    sweep()
    proofs = [issue(f"2026-11-{d:02d}")["proof"] for d in (9, 10, 11)]
    assert [made(f"2026-11-{d:02d}")["prev"] for d in (9, 10, 11)] == [None, proofs[0]["digest"], proofs[1]["digest"]]
    assert len({p["signature"] for p in proofs}) == 3 and len(peer.sent("sign_with_schnorr")) == 3
    assert all(p["signature"] == fake_signature(bytes.fromhex(p["root"])).hex() for p in proofs)


def test_the_days_a_daily_sweep_makes_together_are_one_batch_and_one_signature_oldest_first():
    peer = started()
    daily()
    at(2026, 11, 13, 0, 5)  # the 9th, 10th, 11th and 12th have closed
    sweep()
    assert [signed(f"2026-11-{d:02d}")[1]["body"]["number"] for d in (9, 10, 11, 12)] == [1, 2, 3, 4]
    assert len(peer.sent("sign_with_schnorr")) == 1 and issue("2026-11-13")["status"] == "provisional"
    proofs = [issue(f"2026-11-{d:02d}")["proof"] for d in (9, 10, 11, 12)]
    assert len({p["root"] for p in proofs}) == 1 and len({p["signature"] for p in proofs}) == 1
    for p in proofs:
        assert climb(p["digest"], p["path"]) == p["root"] and p["signature"] == fake_signature(bytes.fromhex(p["root"])).hex()


def test_a_daily_realms_long_backlog_is_signed_in_batches_of_a_month():
    peer = started()
    daily()
    at(2027, 1, 20, 0, 5)  # 72 days have closed
    sweep()
    assert len(peer.sent("sign_with_schnorr")) == 3 and len(peer.sent("schnorr_public_key")) == 3
    assert signed("2027-01-19")[1]["body"]["number"] == 72


def test_a_second_read_and_a_second_sweep_give_the_same_signature_and_sign_nothing():
    peer = started()
    daily()
    at(2026, 11, 10, 0, 5)
    sweep()
    first = issue("2026-11-09")["proof"]
    calls = len(peer.calls)
    for _ in range(3):
        sweep()
    assert issue("2026-11-09")["proof"] == first and len(peer.calls) == calls and SignedDocument.count() == 1


def test_a_realm_that_turns_daily_signs_every_day_made_after_the_last_signed_one_and_one_that_turns_back_stops():
    peer = started()
    in_days(2)  # the 9th and 10th are made, on request: unsigned
    daily()
    at(2026, 11, 12, 0, 5)
    sweep()  # the 11th is made; the 9th, 10th and 11th are made and come after the last signed day (none): one batch
    assert [issue(f"2026-11-{d:02d}")["status"] for d in (9, 10, 11)] == ["signed"] * 3
    assert len(peer.sent("sign_with_schnorr")) == 1
    assert len({issue(f"2026-11-{d:02d}")["proof"]["root"] for d in (9, 10, 11)}) == 1
    Realm["1"].journal_signing = "on_request"
    at(2026, 11, 13, 0, 5)
    sweep()
    assert issue("2026-11-12")["status"] == "unsigned" and len(peer.sent("sign_with_schnorr")) == 1


def test_a_daily_realm_signs_only_the_days_after_the_last_one_signed_the_days_before_it_are_covered_by_it():
    peer = started()
    in_days(4)  # the 9th to the 12th are made, on request
    ask("2026-11-11")  # the 11th is signed on request: the 9th and 10th are before it
    daily()
    at(2026, 11, 14, 0, 5)
    sweep()  # the 13th is made
    assert [issue(f"2026-11-{d:02d}")["status"] for d in (9, 10, 11, 12, 13)] == ["unsigned", "unsigned", "signed", "signed", "signed"]
    assert len(peer.sent("sign_with_schnorr")) == 2  # the request, and one batch for the 12th and 13th
    assert made("2026-11-11")["prev"] == signing.proof("journal", "2026-11-10")["digest"]  # which it covers by the chain


def test_a_daily_realm_whose_signature_fails_keeps_the_days_made_and_signs_them_all_in_one_batch_at_a_later_sweep():
    peer = started()
    daily()
    peer.answer("sign_with_schnorr", fake_chain.rejects("no cycles"))
    at(2026, 11, 12, 0, 5)  # the 9th, 10th and 11th have closed
    sweep()
    assert [issue(f"2026-11-{d:02d}")["status"] for d in (9, 10, 11)] == ["unsigned"] * 3
    assert len(peer.sent("sign_with_schnorr")) == 1 and SignedDocument.count() == 3 and journal.sealed_head()
    assert failure_of("journal")["subjects"] == ["2026-11-09", "2026-11-10", "2026-11-11"]
    management()
    fake_chain.state["now"] += 3600
    sweep()
    assert [signed(f"2026-11-{d:02d}")[1]["body"]["number"] for d in (9, 10, 11)] == [1, 2, 3]
    assert failure_of("journal") is None
    assert len({issue(f"2026-11-{d:02d}")["proof"]["root"] for d in (9, 10, 11)}) == 1


def test_a_reply_that_is_not_a_signature_is_a_failure_and_the_day_stays_unsigned():
    peer = started()
    daily()
    peer.answer("sign_with_schnorr", '(record { 359_375_608 = blob "\\00\\01" })')
    at(2026, 11, 10, 0, 5)
    sweep()
    assert issue("2026-11-09")["status"] == "unsigned" and SignedDocument["journal:2026-11-09"].signature == ""
    assert "not an Ed25519 signature" in failure_of("journal")["error"]


def test_a_request_made_while_a_daily_sweep_is_signing_the_same_day_is_pending_and_the_sweep_signs_it_once():
    peer = started()
    daily()
    at(2026, 11, 10, 0, 5)
    plain, seen = peer.replies["sign_with_schnorr"], []

    def meanwhile(arg):
        peer.answer("sign_with_schnorr", plain)
        seen.append(ask("2026-11-09")["status"])
        return plain(arg)

    peer.answer("sign_with_schnorr", meanwhile)
    sweep()
    assert seen == ["pending"] and issue("2026-11-09")["status"] == "signed" and len(peer.sent("sign_with_schnorr")) == 1


# ---- A daily realm's sweep works from the documents, so no failure loses a day

def unsigned_among(days):
    return [day for day in days if not signing.proof("journal", day)["signature"]]


def all_days(count):
    return [f"2026-{d.month:02d}-{d.day:02d}" for d in (datetime(2026, 11, 9) + timedelta(days=n) for n in range(count))]


def test_a_backlog_of_forty_one_days_signed_by_a_daily_realm_short_of_cycles_is_signed_whole_when_the_cycles_are_back():
    peer = started()
    daily()
    enough = fake_chain.state["balance"]
    fake_chain.state["balance"] = 1_000_000_000  # the realm is short of cycles
    at(2026, 12, 20, 0, 5)  # 41 days have closed
    sweep()
    assert SignedDocument.count() == 41 and peer.sent("sign_with_schnorr") == []  # all made, none signed
    assert len(failure_of("journal")["subjects"]) == 31 and journal_signing.signed_through() is None  # a record of one batch
    fake_chain.state["balance"] = enough  # the cycles are back
    for night in range(3):  # and three more nights
        at(2026, 12, 21 + night, 0, 5)
        sweep()
    assert unsigned_among(all_days(44)) == []  # not a day lost: ten of them used to stay unsigned for good
    assert len(peer.sent("sign_with_schnorr")) == 4  # 31 and 13 at the first sweep, then a day at a time
    assert failure_of("journal") is None


def test_a_request_that_fails_and_a_sweep_that_fails_lose_no_day_when_the_realm_can_sign_again():
    peer = started()
    daily()
    at(2026, 11, 12, 0, 5)  # 9th to 11th closed
    sweep()
    assert unsigned_among(all_days(3)) == []
    peer.answer("sign_with_schnorr", fake_chain.rejects("no cycles"))
    at(2026, 11, 14, 0, 5)  # the 12th and 13th close
    sweep()  # a sweep that fails
    assert unsigned_among(all_days(5)) == ["2026-11-12", "2026-11-13"]
    assert call("journal_sign", "alice", day="2026-11-12")["kind"] == "unavailable"  # a request that fails too
    assert failure_of("journal")["subjects"] == ["2026-11-12"]  # the record names only the last failure...
    management()
    sweep()  # ...and nothing waits on it
    assert unsigned_among(all_days(5)) == []


def test_days_made_by_a_request_are_signed_by_the_next_sweep_of_a_daily_realm():
    peer = started()
    daily()
    at(2026, 11, 12, 0, 1)  # the 9th to the 11th have closed, and the sweep has not run
    assert ask("2026-11-09")["status"] == "signed"  # the request makes the three, and signs the first
    assert unsigned_among(all_days(3)) == ["2026-11-10", "2026-11-11"] and journal_signing.signed_through() == "2026-11-09"
    sweep()
    assert unsigned_among(all_days(3)) == [] and len(peer.sent("sign_with_schnorr")) == 2


def test_a_request_for_an_old_day_does_not_move_the_last_signed_day_back_and_a_later_one_moves_it_forward():
    started()
    in_days(4)
    ask("2026-11-11")
    ask("2026-11-09")
    assert journal_signing.signed_through() == "2026-11-11"
    ask("2026-11-12")
    assert journal_signing.signed_through() == "2026-11-12"


def test_an_idempotent_request_a_pending_one_and_a_refusal_write_nothing_and_clear_no_failure():
    peer = started()
    in_days(3)
    ask("2026-11-09")
    peer.answer("sign_with_schnorr", fake_chain.rejects("no cycles"))
    assert call("journal_sign", "alice", day="2026-11-10")["kind"] == "unavailable"  # a failure is on record
    kept = failure_of("journal")
    writes = []
    real_save = Entity._save
    monkeypatch_save(real_save, writes)
    try:
        assert ask("2026-11-09", ANONYMOUS)["status"] == "signed"  # signed already
        assert ask("2026-11-09")["status"] == "signed"
        refused("2026-11-12"), refused("tomorrow"), refused("2026-11-08")  # not closed, no date, before the first day
        sweep()  # nothing to make, and on request nothing to sign
    finally:
        Entity._save = real_save
    assert writes == [] and failure_of("journal") == kept  # not a save, and the failure is still there


def test_the_counter_of_saves_does_see_a_signature_being_stored():
    started()
    at(2026, 11, 10, 0, 5)
    sweep()
    writes, real_save = [], Entity._save
    monkeypatch_save(real_save, writes)
    try:
        ask("2026-11-09")
    finally:
        Entity._save = real_save
    assert "SignedDocument" in writes and "Realm" in writes  # the signature, and the last signed day


def test_a_request_that_is_pending_writes_nothing_either():
    peer = started()
    at(2026, 11, 10, 0, 5)
    sweep()
    plain, writes = peer.replies["sign_with_schnorr"], []

    def meanwhile(arg):
        peer.answer("sign_with_schnorr", plain)
        real_save = Entity._save
        monkeypatch_save(real_save, writes)
        try:
            assert ask("2026-11-09", ANONYMOUS)["status"] == "pending"
        finally:
            Entity._save = real_save
        return plain(arg)

    peer.answer("sign_with_schnorr", meanwhile)
    ask("2026-11-09")
    assert writes == []


def monkeypatch_save(real_save, writes):
    """Count the saves of entities from now until ``Entity._save`` is put back."""
    def counting(self, *args, **kwargs):
        writes.append(self._type)
        return real_save(self, *args, **kwargs)

    Entity._save = counting


def test_the_last_signed_day_of_a_realm_upgraded_from_the_daily_release_starts_at_its_last_signed_day():
    started()
    at(2026, 11, 11, 0, 5)
    signed_the_old_way(["2026-11-09", "2026-11-10"])
    assert journal_signing.signed_through() is None  # the earlier release kept no such day: its head was the last signed
    at(2026, 11, 12, 0, 5)
    sweep()
    assert journal_signing.signed_through() == "2026-11-10" and issue("2026-11-11")["status"] == "unsigned"


def test_a_new_realm_has_signed_no_day_and_the_cursor_is_kept_from_the_first_sweep():
    started()
    sweep()
    assert "signed" in signing.state("journal") and journal_signing.signed_through() is None


# ---- Changing the mode


def test_changing_the_mode_leaves_the_documents_and_signatures_made_as_they_were():
    started()
    in_days(2)
    ask("2026-11-09")
    before = [signing.proof("journal", d) for d in ("2026-11-09", "2026-11-10")]
    daily()
    Realm["1"].journal_signing = "on_request"
    assert [signing.proof("journal", d) for d in ("2026-11-09", "2026-11-10")] == before


# ---- A realm upgraded from the release that signed every day keeps what it signed


def signed_the_old_way(days):
    """The documents of ``days`` as the release that signed every day wrote them: ``signed_at`` and no ``made_at``, each
    linked to the one signed before it, one signature each, and the last one the head."""
    prev, key = None, fake_key("key_1").hex()
    for number, day in enumerate(days, 1):
        text = signing.canonical({"v": 1, "kind": "journal", "realm": fake_chain.self_id(), "subject": day,
                                  "body": {"number": number, "timezone": "UTC", "entries": []}, "prev": prev,
                                  "signed_at": fake_chain.now(),
                                  "key": {"algorithm": "ed25519", "name": "key_1", "path": "realms:journal",
                                          "public_key": key}})
        digest = signing.digest_of(text)
        SignedDocument(id=f"journal:{day}", code=signing.code_of(digest), canonical=text, digest=digest, root=digest,
                       path=[], signature=fake_signature(bytes.fromhex(digest)).hex())
        prev = digest
    signing.keep("journal", head={"subject": days[-1], "digest": prev})


def test_days_signed_by_the_release_that_signed_every_day_stay_as_they_were_and_the_next_day_links_to_the_last_of_them():
    peer = started()
    at(2026, 11, 11, 0, 5)  # the 9th and 10th have closed
    old = ["2026-11-09", "2026-11-10"]
    signed_the_old_way(old)
    before = [signing.proof("journal", day) for day in old]
    at(2026, 11, 12, 0, 5)
    sweep()  # the upgraded realm's first sweep: the 11th has closed
    at(2026, 11, 13, 0, 5)
    sweep()
    assert [signing.proof("journal", day) for day in old] == before  # the text, digest and proof, byte for byte
    assert [issue(day)["proof"] for day in old] == before and [issue(day)["status"] for day in old] == ["signed"] * 2
    assert all(p["signature"] == fake_signature(bytes.fromhex(p["root"])).hex() for p in before)
    assert "signed_at" in json.loads(before[0]["canonical"]) and "made_at" not in json.loads(before[0]["canonical"])
    assert peer.sent("sign_with_schnorr") == []  # the default now: nothing is signed by itself
    first_new = made("2026-11-11")
    assert first_new["prev"] == before[1]["digest"] and "made_at" in first_new and "signed_at" not in first_new
    assert first_new["body"]["number"] == 3 and made("2026-11-12")["prev"] == signing.proof("journal", "2026-11-11")["digest"]
    assert issue("2026-11-11")["status"] == "unsigned"
    assert ask("2026-11-10") == {"ok": True, "day": "2026-11-10", "status": "signed", "proof": before[1]}  # for nothing
    assert peer.sent("sign_with_schnorr") == []


def test_a_daily_realm_upgraded_from_that_release_goes_on_signing_each_new_day_from_the_last_it_signed():
    peer = started()
    at(2026, 11, 11, 0, 5)
    signed_the_old_way(["2026-11-09", "2026-11-10"])
    daily()
    at(2026, 11, 12, 0, 5)
    sweep()
    proof = issue("2026-11-11")["proof"]
    assert json.loads(proof["canonical"])["prev"] == signing.proof("journal", "2026-11-10")["digest"]
    assert len(peer.sent("sign_with_schnorr")) == 1


def test_no_entry_is_stamped_into_a_day_the_old_release_signed():
    started()
    at(2026, 11, 11, 0, 5)
    signed_the_old_way(["2026-11-09", "2026-11-10"])
    zone("Etc/GMT+12")  # UTC-12: the 10th, still
    journal.record("codex_installed", codex="sha256:after")
    assert (issue()["day"], issue()["number"]) == ("2026-11-11", 3)


# ---- The day ends at midnight on the realm's clock


def test_the_day_closes_at_midnight_in_the_realms_zone():
    peer = started(2026, 11, 9, 1)
    zone("America/New_York")  # the 8th there: the journal begins on it
    at(2026, 11, 9, 4, 59)  # 23:59 on the 8th
    sweep()
    assert peer.calls == []
    at(2026, 11, 9, 5, 1)  # the 9th has begun there
    sweep()
    assert made("2026-11-08")["body"]["timezone"] == "America/New_York"


def test_a_zone_change_west_after_a_day_is_made_puts_no_entry_in_it_and_moves_no_first_day():
    started()  # 10:00 UTC on the 9th
    zone("Pacific/Kiritimati")  # UTC+14: already the 10th
    sweep()  # the 9th is over there, and is made
    assert SignedDocument["journal:2026-11-09"] is not None
    zone("Etc/GMT+12")  # UTC-12: the 8th, a day before the journal began
    journal.record("codex_installed", codex="sha256:after")
    assert (issue()["day"], issue()["number"], issue()["first_day"]) == ("2026-11-10", 2, "2026-11-09")
    assert [e["details"]["codex"] for e in issue()["entries"]] == ["sha256:after"]
    assert issue("2026-11-09")["entries"] == [] and issue("2026-11-09")["status"] == "unsigned"
    sweep()
    assert issue()["status"] == "provisional" and SignedDocument.count() == 1  # the open day is not made early


def test_an_entry_put_into_a_day_while_the_key_for_its_document_is_asked_for_is_not_lost():
    peer = started()
    at(2026, 11, 10, 0, 5)  # the 9th has closed
    plain = peer.replies["schnorr_public_key"]

    def west(arg):
        peer.answer("schnorr_public_key", plain)
        zone("America/Los_Angeles")  # while the call is out: the 9th, 16:05 there, is open again...
        journal.record("codex_installed", codex="sha256:late")  # ...and an entry is made in it
        return plain(arg)

    peer.answer("schnorr_public_key", west)
    sweep()
    assert SignedDocument.count() == 0  # the 9th is no longer closed: nothing is made of it
    at(2026, 11, 10, 8, 5)  # the 9th has closed in Los Angeles
    sweep()
    assert [e["details"]["codex"] for e in made("2026-11-09")["body"]["entries"]] == ["sha256:late"]


# ---- The key depends on the network


@pytest.mark.parametrize("network,name", [("local", "dfx_test_key"), ("test", "test_key_1"), ("staging", "test_key_1"),
                                          ("demo", "test_key_1"), ("ic", "key_1"), ("", "key_1")])
def test_the_key_is_the_networks(network, name):
    started()
    Realm["1"].network = network
    at(2026, 11, 10, 0, 5)
    sweep()
    assert made("2026-11-09")["key"]["name"] == name
    assert ask("2026-11-09")["status"] == "signed"


# ---- Looking an issue up by its verification code


def test_a_verification_code_finds_the_issue_written_with_or_without_dashes_and_in_any_case():
    started()
    at(2026, 11, 10, 0, 5)
    sweep()
    ask("2026-11-09")
    code = issue("2026-11-09")["proof"]["code"]
    for written in (code, code.lower(), code.replace("-", ""), code.replace("-", " ")):
        assert issue(code=written)["day"] == "2026-11-09"
    assert issue(code=code) == issue("2026-11-09")


def test_the_code_of_a_day_made_and_not_signed_finds_it_as_unsigned_and_serves_its_document():
    started()
    at(2026, 11, 10, 0, 5)
    sweep()
    unsigned = signing.proof("journal", "2026-11-09")
    assert issue(code=unsigned["code"])["status"] == "unsigned"
    out = ok("signature_proof", ANONYMOUS, code=unsigned["code"])
    assert out["proof"] == unsigned and out["proof"]["signature"] == ""
    assert call("signature_proof", ANONYMOUS, kind="journal", subject="2026-11-10")["kind"] == "not_found"  # not made


def test_an_unknown_or_malformed_code_is_refused_and_so_is_a_day_with_a_code():
    started()
    at(2026, 11, 10, 0, 5)
    sweep()
    ask("2026-11-09")
    code = issue("2026-11-09")["proof"]["code"]
    assert call("journal_issue", "alice", code="AAAA-AAAA-AAAA-AAAA")["kind"] == "not_found"
    assert call("journal_issue", "alice", code="AAAA")["kind"] == "invalid_argument"
    assert call("journal_issue", "alice", code=code, day="2026-11-09")["kind"] == "invalid_argument"
