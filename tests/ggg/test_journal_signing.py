"""The signature on each closed issue of the official journal (docs/codices/journal-signature.md), in process: the
journal as the first kind of document of core.signing (tests/ggg/test_signing.py tests the facility on its own).

The management canister's Schnorr key is played by a peer that signs deterministically (``management``): what is
tested here is what the realm asks for, keeps and serves. tests/deployed/test_journal_signing.py runs the same on the
wasm with the IC's own key, and checks the signatures as Ed25519."""

import hashlib
import json
from datetime import datetime, timezone

import fake_chain
import pytest
import world
from core import journal, journal_signing, localtime, op, signing
from core.candid import Reader, idl_hash
from ggg import Extension, Realm, SignedDocument
from kit import call, ok
from signing_kit import climb, fake_key, fake_signature, management

DAY = 86400
FIELDS = ["codex", "layers", "proposal", "activates_at", "reverted", "source", "version", "effective_from"]
BEFORE_THE_FIRST_MIDNIGHT = (2026, 11, 9, 10)


@pytest.fixture(autouse=True)
def utc_clock():
    localtime.use("UTC")


def at(*when):
    fake_chain.state["now"] = int(datetime(*when, tzinfo=timezone.utc).timestamp())


def sweep():
    """One sweep of the realm, as its timer runs it."""
    op.run(journal_signing.sweep())


def zone(name):
    """The realm's founder moves it to ``name``, as setup and a realm-wide vote do."""
    Realm["1"].timezone = name
    localtime.use(name)
    journal.fix_first_day()


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


# ---- Today's issue is provisional, a closed one is signed once its day has closed


def test_todays_issue_is_provisional_and_has_no_proof():
    started()
    sweep()
    assert issue()["status"] == "provisional" and "proof" not in issue()


def test_a_closed_day_is_signed_at_the_next_sweep_and_not_before():
    peer = started()
    at(2026, 11, 9, 23, 59)
    sweep()
    assert peer.sent("sign_with_schnorr") == []  # the 9th is not over
    at(2026, 11, 10, 0, 1)
    assert issue("2026-11-09")["status"] == "unsigned"  # closed, and the sweep has not run yet
    sweep()
    assert issue("2026-11-09")["status"] == "signed" and issue("2026-11-10")["status"] == "provisional"


def test_the_signed_text_holds_the_realm_the_day_the_entries_the_key_and_when_it_was_signed():
    started()
    journal.record("codex_installed", codex="sha256:one", proposal="P-1", nickname="never kept")
    at(2026, 11, 10, 0, 5)
    sweep()
    proof, doc = signed("2026-11-09")
    assert doc == {
        "v": 1, "kind": "journal", "realm": fake_chain.self_id(), "subject": "2026-11-09",
        "body": {"number": 1, "timezone": "UTC", "entries": issue("2026-11-09")["entries"]}, "prev": None,
        "signed_at": fake_chain.now(),
        "key": {"algorithm": "ed25519", "name": "key_1", "path": "realms:journal",
                "public_key": fake_key("key_1").hex()}}
    assert doc["body"]["entries"][0]["details"] == {"codex": "sha256:one", "proposal": "P-1"}


def test_the_text_is_canonical_json_and_its_digest_signature_and_code_follow_from_it():
    started()
    at(2026, 11, 10, 0, 5)
    sweep()
    proof, doc = signed("2026-11-09")
    text = proof["canonical"]
    assert text == json.dumps(doc, sort_keys=True, separators=(",", ":")) and text.isascii()
    assert proof["digest"] == hashlib.sha256(text.encode()).hexdigest()
    assert proof["root"] == proof["digest"] and proof["path"] == []  # a batch of one: its root is the digest
    assert proof["signature"] == fake_signature(bytes.fromhex(proof["root"])).hex()  # it signs the root
    assert proof["code"] == signing.code_of(proof["digest"])
    digits = proof["code"].split("-")
    assert [len(part) for part in digits] == [4, 4, 4, 4] and all(part.isalnum() and part.isupper() for part in digits)


def test_the_realm_asks_the_management_canister_for_its_own_key_on_the_journals_path():
    peer = started()
    at(2026, 11, 10, 0, 5)
    sweep()
    [key_args], [sign_args] = peer.sent("schnorr_public_key"), peer.sent("sign_with_schnorr")
    asked = Reader(key_args).args()[0]
    assert asked["canister_id"] is None  # the caller's own key
    assert asked["derivation_path"] == [b"realms:journal"]  # the journal's own: it signs nothing else
    assert asked["key_id"] == {"algorithm": {"ed25519": None}, "name": "key_1"}
    assert Reader(sign_args).args()[0]["derivation_path"] == [b"realms:journal"]


def test_the_management_canisters_field_numbers_are_the_ones_the_realm_reads():
    assert idl_hash("public_key") == 881_350_601 and idl_hash("signature") == 359_375_608


# ---- Once, and never again


def test_a_second_read_and_a_second_sweep_give_the_same_signature_and_sign_nothing():
    peer = started()
    at(2026, 11, 10, 0, 5)
    sweep()
    first = issue("2026-11-09")["proof"]
    calls = len(peer.calls)
    for _ in range(3):
        sweep()
    assert issue("2026-11-09")["proof"] == first and len(peer.calls) == calls and SignedDocument.count() == 1


def test_a_signature_made_while_another_was_out_is_dropped():
    peer = started()
    at(2026, 11, 10, 0, 5)
    plain = peer.replies["sign_with_schnorr"]

    def racing(arg):
        peer.answer("sign_with_schnorr", plain)  # the other sweep's own call is answered as usual...
        op.run(journal_signing.sign_closed())  # ...and it keeps its signature first
        return plain(arg)

    peer.answer("sign_with_schnorr", racing)
    op.run(signing.sign_batch("journal", [("2026-11-09", journal_signing.body(journal.issue("2026-11-09")))], True))
    assert len(peer.sent("sign_with_schnorr")) == 2  # both asked,
    assert SignedDocument.count() == 1 and journal.signed_head()["subject"] == "2026-11-09"  # one signature the day


# ---- Every day is signed, and each issue links to the one before it


def test_every_closed_day_is_signed_each_linking_to_the_one_before_by_its_digest():
    started()
    journal.record("codex_installed", codex="sha256:0")  # the 9th
    at(2026, 11, 10, 0, 5)
    sweep()
    at(2026, 11, 11, 0, 5)
    sweep()  # the 10th has no entries, and is signed
    journal.record("codex_installed", codex="sha256:2")  # the 11th
    at(2026, 11, 12, 0, 5)
    sweep()
    docs = [signed(f"2026-11-{d:02d}")[1] for d in (9, 10, 11)]
    proofs = [issue(f"2026-11-{d:02d}")["proof"] for d in (9, 10, 11)]
    assert [d["body"]["number"] for d in docs] == [1, 2, 3] and docs[1]["body"]["entries"] == []
    assert [d["prev"] for d in docs] == [None, proofs[0]["digest"], proofs[1]["digest"]]
    assert len({p["signature"] for p in proofs}) == 3


def test_days_that_closed_while_the_realm_could_not_sign_are_one_batch_one_signature_oldest_first():
    peer = started()
    at(2026, 11, 13, 0, 5)  # the 9th, 10th, 11th and 12th have closed
    sweep()
    assert [signed(f"2026-11-{d:02d}")[1]["body"]["number"] for d in (9, 10, 11, 12)] == [1, 2, 3, 4]
    assert len(peer.sent("sign_with_schnorr")) == 1 and issue("2026-11-13")["status"] == "provisional"
    assert signed("2026-11-12")[1]["prev"] == issue("2026-11-11")["proof"]["digest"]
    proofs = [issue(f"2026-11-{d:02d}")["proof"] for d in (9, 10, 11, 12)]
    assert len({p["root"] for p in proofs}) == 1 and len({p["signature"] for p in proofs}) == 1
    for p in proofs:
        assert climb(p["digest"], p["path"]) == p["root"] and p["signature"] == fake_signature(bytes.fromhex(p["root"])).hex()


def test_a_long_backlog_is_signed_in_batches_of_a_month():
    peer = started()
    at(2027, 1, 20, 0, 5)  # 72 days have closed
    sweep()
    assert len(peer.sent("sign_with_schnorr")) == 3 and SignedDocument.count() == 72
    assert signed("2027-01-19")[1]["body"]["number"] == 72


def test_an_issue_signed_late_says_when_it_was_signed():
    started()
    at(2026, 11, 12, 6)
    sweep()
    assert signed("2026-11-09")[1]["signed_at"] == fake_chain.now() and issue("2026-11-09")["entries"] == []


def test_the_realm_signs_from_a_start_on_the_first_sweep_after_an_upgrade():
    peer = started()
    at(2026, 11, 10, 0, 5)
    fake_chain.state["timers"].clear()
    journal_signing.resume()  # main.py's post_upgrade: its timers are gone
    [(seconds, sweeping)] = [t for t in fake_chain.state["timers"] if t[1] is journal_signing.sweep]
    assert seconds == 0
    op.run(sweeping())
    assert issue("2026-11-09")["status"] == "signed" and len(peer.sent("sign_with_schnorr")) == 1


def test_every_sweep_sets_the_next_in_an_hour():
    started()
    fake_chain.state["timers"].clear()
    sweep()
    assert fake_chain.state["timers"] == [(3600, journal_signing.sweep)]


# ---- The day ends at midnight on the realm's clock


def test_the_day_closes_at_midnight_in_the_realms_zone():
    peer = started(2026, 11, 9, 1)
    zone("America/New_York")  # the 8th there: the journal begins on it
    at(2026, 11, 9, 4, 59)  # 23:59 on the 8th
    sweep()
    assert peer.sent("sign_with_schnorr") == []
    at(2026, 11, 9, 5, 1)  # the 9th has begun there
    sweep()
    assert signed("2026-11-08")[1]["body"]["timezone"] == "America/New_York"


def test_a_zone_change_west_after_a_signature_puts_no_entry_in_the_signed_day_and_moves_no_first_day():
    started()  # 10:00 UTC on the 9th
    zone("Pacific/Kiritimati")  # UTC+14: already the 10th
    sweep()  # the 9th is over there, and is signed
    assert issue("2026-11-09")["status"] == "signed"
    zone("Etc/GMT+12")  # UTC-12: the 8th, a day before the journal began
    journal.record("codex_installed", codex="sha256:after")
    assert (issue()["day"], issue()["number"], issue()["first_day"]) == ("2026-11-10", 2, "2026-11-09")
    assert [e["details"]["codex"] for e in issue()["entries"]] == ["sha256:after"]
    assert issue("2026-11-09")["entries"] == [] and issue("2026-11-09")["status"] == "signed"
    sweep()
    assert issue()["status"] == "provisional" and SignedDocument.count() == 1  # the open day is not signed early


# ---- Failures delay a signature and stop nothing else


def test_a_signature_that_fails_leaves_the_day_unsigned_and_the_next_sweep_tries_again():
    peer = started()
    peer.answer("sign_with_schnorr", fake_chain.rejects("Rejection code 4, 5 cycles attached, 26_153_846_153 required"))
    journal.record("codex_installed", codex="sha256:one")  # recording goes on
    at(2026, 11, 10, 0, 5)
    sweep()
    assert issue("2026-11-09")["status"] == "unsigned" and "proof" not in issue("2026-11-09")
    failure = ok("codex_status", "ctl")["signing_failures"]["journal"]
    assert failure["subjects"] == ["2026-11-09"] and failure["at"] == fake_chain.now()
    assert "26_153_846_153" in failure["error"]
    management()  # the cycles are back
    fake_chain.state["now"] += 3600
    sweep()
    assert issue("2026-11-09")["status"] == "signed" and ok("codex_status", "ctl")["signing_failures"] == {}


def test_a_failed_batch_keeps_nothing_so_that_the_chain_has_no_gap():
    peer = started()
    peer.answer("sign_with_schnorr", fake_chain.rejects("no cycles"))
    at(2026, 11, 12, 0, 5)
    sweep()
    assert [issue(f"2026-11-{d:02d}")["status"] for d in (9, 10, 11)] == ["unsigned"] * 3
    assert len(peer.sent("sign_with_schnorr")) == 1 and SignedDocument.count() == 0 and journal.signed_head() is None
    management()
    sweep()
    assert [signed(f"2026-11-{d:02d}")[1]["body"]["number"] for d in (9, 10, 11)] == [1, 2, 3]


def test_a_reply_that_is_not_a_signature_is_a_failure_and_nothing_is_kept():
    peer = started()
    peer.answer("sign_with_schnorr", '(record { 359_375_608 = blob "\\00\\01" })')
    at(2026, 11, 10, 0, 5)
    sweep()
    assert issue("2026-11-09")["status"] == "unsigned" and SignedDocument.count() == 0
    assert "not an Ed25519 signature" in ok("codex_status", "ctl")["signing_failures"]["journal"]["error"]


# ---- The key depends on the network


@pytest.mark.parametrize("network,name", [("local", "dfx_test_key"), ("test", "test_key_1"), ("staging", "test_key_1"),
                                          ("demo", "test_key_1"), ("ic", "key_1"), ("", "key_1")])
def test_the_key_is_the_networks(network, name):
    started()
    Realm["1"].network = network
    at(2026, 11, 10, 0, 5)
    sweep()
    assert signed("2026-11-09")[1]["key"]["name"] == name


# ---- Looking an issue up by its verification code


def test_a_verification_code_finds_the_issue_written_with_or_without_dashes_and_in_any_case():
    started()
    at(2026, 11, 10, 0, 5)
    sweep()
    code = issue("2026-11-09")["proof"]["code"]
    for written in (code, code.lower(), code.replace("-", ""), code.replace("-", " ")):
        assert issue(code=written)["day"] == "2026-11-09"
    assert issue(code=code) == issue("2026-11-09")


def test_an_unknown_or_malformed_code_is_refused_and_so_is_a_day_with_a_code():
    started()
    at(2026, 11, 10, 0, 5)
    sweep()
    code = issue("2026-11-09")["proof"]["code"]
    assert call("journal_issue", "alice", code="AAAA-AAAA-AAAA-AAAA")["kind"] == "not_found"
    assert call("journal_issue", "alice", code="AAAA")["kind"] == "invalid_argument"
    assert call("journal_issue", "alice", code=code, day="2026-11-09")["kind"] == "invalid_argument"


# ---- A quarter signs nothing


def test_a_quarter_issues_nothing_and_signs_nothing():
    peer = started()
    Realm["1"].is_quarter, Realm["1"].is_capital = True, False
    at(2026, 11, 12, 0, 5)
    sweep()
    assert peer.calls == [] and SignedDocument.count() == 0
    assert ok("journal_issue", "alice")["issued"] is False
