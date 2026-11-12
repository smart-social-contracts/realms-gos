"""The verify page (docs/verify/index.html): its logic run under Node against proofs made with a real Ed25519 key and the
design's own rules (tests/deployed/verify_page.py), and its request to a realm against what the IC's HTTP interface and
Candid expect. The canister's own proofs go through the same page in test_journal_signing.py.

Needs Node 22 or later (WebCrypto's Ed25519), ``cryptography`` and ``cbor2``, which CI installs; without Node the tests are
skipped, and without the packages the module fails to import, so that a CI that lacks them is red and not quiet."""

import hashlib
import json

import cbor2
import pytest
from ic.candid import Types, decode, encode
from ic.principal import Principal

from verify_page import NODE, PAGE, Signer, canonical as canonical_text, code, issue_body, page

pytestmark = pytest.mark.skipif(NODE is None, reason="no node")

CANISTER = "lxzze-o7777-77777-aaaaa-cai"


def check(result, name):
    return next(c for c in result["checks"] if c["name"].startswith(name))


def statuses(result):
    return {c["name"]: c["status"] for c in result["checks"]}


def one(realm=CANISTER, **kind):
    signer = Signer(realm, **kind)
    return signer, signer.sign([("2026-11-09", issue_body(1))])[0]


# ---- A proof that is right passes, and says what it did not check


def test_a_proof_of_a_batch_of_one_passes_every_check_and_says_the_key_is_not_checked():
    signer, proof = one()
    result = page("verifyProof", proof)
    assert result["valid"] is True and result["supported"] is True
    assert [c["status"] for c in result["checks"][:5]] == ["pass"] * 5
    assert check(result, "The canister")["status"] == "note" and check(result, "The key is the canister's")["status"] == "note"
    assert result["doc"]["realm"] == CANISTER and result["doc"]["key"]["public_key"] == signer.public.hex()


@pytest.mark.parametrize("size", range(1, 9))
def test_every_document_of_a_batch_verifies_whatever_its_size(size):
    signer = Signer(CANISTER, kind="certificate")
    proofs = signer.sign([(f"C-{n}", {"holder": n}) for n in range(size)], chained=False)
    assert len({p["root"] for p in proofs}) == 1 and len({p["signature"] for p in proofs}) == 1
    for proof in proofs:
        assert page("verifyProof", proof, {"canister": CANISTER})["valid"] is True


def test_the_canister_and_the_key_are_compared_when_they_are_given():
    signer, proof = one()
    good = page("verifyProof", proof, {"canister": CANISTER, "expectedKey": signer.public.hex().upper()})
    assert good["valid"] is True and check(good, "The key is the expected one")["status"] == "pass"
    assert check(good, "The text names the canister")["status"] == "pass"
    other_key = page("verifyProof", proof, {"expectedKey": Signer().public.hex()})
    assert other_key["valid"] is False and check(other_key, "The key is the expected one")["status"] == "fail"
    elsewhere = page("verifyProof", proof, {"canister": "aaaaa-aa"})
    assert elsewhere["valid"] is False and check(elsewhere, "The text names the canister")["status"] == "fail"


def test_a_proof_is_the_one_asked_for_by_its_code_or_by_its_kind_and_subject_and_another_genuine_one_is_not():
    signer = Signer(CANISTER)
    first, second = signer.sign([("2026-11-09", issue_body(1)), ("2026-11-10", issue_body(2))])
    ok = page("verifyProof", second, {"canister": CANISTER, "requested": {"code": second["code"].lower().replace("-", " ")}})
    assert ok["valid"] is True and check(ok, "The document is the one asked for")["status"] == "pass"
    assert page("verifyProof", second, {"requested": {"kind": "journal", "subject": "2026-11-10"}})["valid"] is True
    for asked in ({"code": first["code"]}, {"kind": "journal", "subject": "2026-11-09"}, {"kind": "certificate", "subject": "2026-11-10"}):
        result = page("verifyProof", second, {"canister": CANISTER, "requested": asked})  # a genuine proof, but not that one
        assert result["valid"] is False and check(result, "The document is the one asked for")["status"] == "fail"


def test_the_key_of_a_document_is_the_one_of_its_kind_and_nothing_else_is_accepted_for_a_name_or_a_path():
    signer = Signer(CANISTER)
    for field, value in (("path", "realms:certificate"), ("name", "key 1; touch /tmp/x"), ("name", "KEY_1")):
        doc = json.loads(signer.sign([("2026-11-09", issue_body(1))])[0]["canonical"])
        doc["key"][field] = value
        text = canonical_text(doc)
        forged = signer.sign_text(text)
        assert page("verifyProof", forged)["valid"] is False and page("verifyProof", forged)["shaped"] is False, (field, value)


def test_a_document_of_a_realm_that_is_no_canister_id_is_not_well_formed():
    for realm in ("not a canister", "AAAAA-AA", "rrkah-fqaaa-aaaaa-aaaaq-caa"):
        proof = Signer(realm).sign([("2026-11-09", issue_body(1))])[0]
        assert page("verifyProof", proof)["shaped"] is False, realm


def test_a_key_that_is_no_key_is_an_invalid_document_and_not_a_browser_that_cannot_check():
    signer = Signer(CANISTER)
    signer.public = bytes([0xFF]) * 32
    result = page("verifyProof", signer.sign([("2026-11-09", issue_body(1))])[0])
    assert result["supported"] is True and result["valid"] is False


# ---- The verdict: green only for a key the reader derived, red for a document that fails, amber for the rest


def test_the_verdict_is_amber_while_the_key_is_not_checked_and_red_for_a_key_that_is_not_the_one_derived():
    _, proof = one()
    unchecked = page("verdictOf", page("verifyProof", proof, {"canister": CANISTER}))
    assert unchecked["level"] == "note" and "key is not yet checked" in unchecked["title"]
    wrong = page("verdictOf", page("verifyProof", proof, {"canister": CANISTER, "expectedKey": "00" * 32}))
    assert wrong["level"] == "fail"


def test_a_proof_whose_own_signature_or_binding_fails_is_red():
    _, proof = one()
    for options, changed in (({"canister": "aaaaa-aa"}, proof), ({"canister": CANISTER, "requested": {"code": "AAAA-AAAA-AAAA-AAAA"}}, proof),
                             ({"canister": CANISTER}, {**proof, "signature": "00" * 64})):
        said = page("verdictOf", page("verifyProof", changed, options))
        assert said["level"] == "fail" and said["title"].startswith("Not valid"), options


BROKEN_LINK = {"status": "fail", "name": "Link to the previous document", "detail": "x"}


def test_a_chain_link_that_alone_fails_is_amber_and_never_reads_stronger_than_an_intact_chain():
    signer, proof = one()
    checked = page("verifyProof", proof, {"canister": CANISTER, "expectedKey": signer.public.hex()})
    assert page("verdictOf", checked)["level"] == "pass"  # the document, on its own, is right and its key is the reader's
    said = page("verdictOf", {**checked, "checks": checked["checks"] + [BROKEN_LINK]})
    assert said["level"] == "note" and "withholds" in said["detail"]
    assert said["title"] == "Signed by the key you derived for this canister, but the document it follows could not be confirmed"
    # With the key unchecked, the headline is no stronger than the one of an intact chain, which does not name the realm
    unchecked = page("verifyProof", proof, {"canister": CANISTER})
    intact = page("verdictOf", unchecked)
    broken = page("verdictOf", {**unchecked, "checks": unchecked["checks"] + [BROKEN_LINK]})
    assert intact["level"] == broken["level"] == "note"
    assert broken["title"] == "The signature is valid, but the key is not yet checked, and the document it follows could not be confirmed"
    assert "signed by the realm" not in (broken["title"] + broken["detail"]).lower()
    # ...while a document that fails itself stays red, whatever its chain link says
    ruined = page("verifyProof", {**proof, "signature": "00" * 64}, {"canister": CANISTER})
    assert page("verdictOf", {**ruined, "checks": ruined["checks"] + [BROKEN_LINK]})["level"] == "fail"


def test_the_page_never_says_the_realm_signed_something():
    assert "signed by the realm" not in PAGE.read_text().lower()


# ---- What the page will not trust from a link, and what it will not put in a command


def test_a_link_can_ask_for_a_document_and_nothing_else_least_of_all_a_key_or_a_replica():
    link = "?canister=aaaaa-aa&code=AAAA-AAAA-AAAA-AAAA&kind=journal&subject=2026-11-09&key=" + "ab" * 32 + "&host=https://evil.example"
    assert page("requestFromUrl", link) == {"canister": "aaaaa-aa", "code": "AAAA-AAAA-AAAA-AAAA", "kind": "journal",
                                            "subject": "2026-11-09"}
    assert page("requestFromUrl", "") == {"canister": "", "code": "", "kind": "", "subject": ""}


def test_a_replica_other_than_the_public_one_is_named_to_the_reader():
    assert page("hostNote", "https://icp-api.io") == page("hostNote", "https://icp-api.io/") == ""
    note = page("hostNote", "https://evil.example")
    assert "https://evil.example" in note and "a key you derived yourself" in note
    assert page("hostNote", "http://127.0.0.1:4943").startswith("This proof was fetched from http://127.0.0.1:4943")


HOSTILE = "'; touch /tmp/PWNED_BY_PROOF; echo '"


@pytest.mark.parametrize("field", ["signature", "root", "digest"])
def test_no_command_is_written_from_a_proof_whose_values_are_not_in_their_form(field):
    _, proof = one()
    doc = json.loads(proof["canonical"])
    hostile = {**proof, field: HOSTILE + "0" * (128 if field == "signature" else 64)}
    assert page("snippets", doc, hostile) is None
    assert page("verifyProof", hostile)["shaped"] is False
    assert page("snippets", doc, {**proof, "path": "no"}) is None


def test_the_commands_of_a_right_proof_hold_hex_and_nothing_a_shell_would_run():
    signer, proof = one()
    commands = page("snippets", json.loads(proof["canonical"]), proof)
    assert signer.public.hex() in commands["python"] and proof["signature"] in commands["openssl"]
    assert "PWNED" not in json.dumps(commands) and CANISTER in commands["derive"] and 'forMainnetKey("key_1")' in commands["derive"]
    assert "realms:journal" in commands["derive"]


def test_the_key_of_a_local_replica_has_no_derivation_to_show():
    signer = Signer(CANISTER, key_name="dfx_test_key")
    proof = signer.sign([("2026-11-09", issue_body(1))])[0]
    commands = page("snippets", json.loads(proof["canonical"]), proof)
    assert commands["derive"] is None and "dfx_test_key" not in commands["python"]
    assert page("verifyProof", proof)["valid"] is True


# ---- A proof that was touched does not


def tampered(proof, field, value):
    return {**proof, field: value}


def flip(text):
    return text[:-1] + ("0" if text[-1] != "0" else "1")


@pytest.mark.parametrize("field,make", [
    ("canonical", lambda p: p["canonical"].replace("2026-11-09", "2026-11-10")),
    ("digest", lambda p: flip(p["digest"])),
    ("root", lambda p: flip(p["root"])),
    ("signature", lambda p: flip(p["signature"])),
    ("code", lambda p: code("0" * 64)),
    ("path", lambda p: [["r", "00" * 32]]),
])
def test_a_proof_with_any_field_changed_is_not_valid(field, make):
    _, proof = one()
    assert page("verifyProof", tampered(proof, field, make(proof)), {"canister": CANISTER})["valid"] is False


def test_a_document_changed_and_signed_again_with_another_key_is_valid_in_itself_but_not_the_canisters():
    signer, proof = one()
    forger = Signer(CANISTER)
    forged = forger.sign([("2026-11-09", issue_body(1, [{"id": "E1", "at": 1, "kind": "codex_installed", "details": {}}]))])[0]
    assert page("verifyProof", forged, {"canister": CANISTER})["valid"] is True  # the signature alone proves nothing more
    caught = page("verifyProof", forged, {"canister": CANISTER, "expectedKey": signer.public.hex()})
    assert caught["valid"] is False and check(caught, "The key is the expected one")["status"] == "fail"


def test_a_leaf_swapped_into_another_batch_does_not_climb_to_its_root():
    signer = Signer(CANISTER, kind="certificate")
    first, second = signer.sign([("a", {}), ("b", {}), ("c", {})], chained=False), Signer(CANISTER).sign([("x", {})])
    mixed = {**first[0], "root": second[0]["root"], "signature": second[0]["signature"]}
    assert page("verifyProof", mixed)["valid"] is False


@pytest.mark.parametrize("proof", [{}, {"canonical": "{"}, {"canonical": "[]"}, {"canonical": "{\"v\":2}"}, None])
def test_what_is_not_a_proof_is_refused_with_a_reason_and_not_an_error(proof):
    result = page("verifyProof", proof)
    assert result["valid"] is False and result["checks"][0]["status"] == "fail"


def test_a_signature_that_is_not_hex_or_a_path_step_with_no_side_is_not_valid():
    _, proof = one()
    assert page("verifyProof", {**proof, "signature": "zz" * 64})["valid"] is False
    assert page("verifyProof", {**proof, "path": [["x", "00" * 32]]})["valid"] is False


# ---- Reading what a realm answers with


def test_the_proof_is_found_in_a_reply_of_signature_proof_in_an_issue_or_on_its_own():
    _, proof = one()
    for shape in (proof, {"ok": True, "kind": "journal", "subject": "2026-11-09", "proof": proof},
                  {"ok": True, "issue": {"day": "2026-11-09", "proof": proof}}):
        assert page("findProof", shape) == proof
    assert page("findProof", {"ok": True, "issue": {"status": "provisional"}}) is None


@pytest.mark.parametrize("digest", [hashlib.sha256(bytes([n])).hexdigest() for n in range(6)])
def test_the_code_is_the_one_the_realm_prints(digest):
    assert page("codeOf", digest) == code(digest)


def test_a_chain_link_is_checked_by_fetching_the_previous_document_by_its_code():
    signer = Signer(CANISTER)
    first, second = signer.sign([("2026-11-09", issue_body(1)), ("2026-11-10", issue_body(2))])
    doc = json.loads(second["canonical"])
    assert code(doc["prev"]) == first["code"]
    assert page("checkPrevious", doc, {"$fetcher": {first["code"]: first}})["status"] == "pass"
    other = Signer(CANISTER).sign([("2026-11-09", issue_body(1, [{"id": "E1", "at": 1, "kind": "x", "details": {}}]))])[0]
    assert page("checkPrevious", doc, {"$fetcher": {first["code"]: other}})["status"] == "fail"  # not the one it names
    assert page("checkPrevious", json.loads(first["canonical"]), {"$fetcher": {}})["status"] == "note"  # the first


# ---- A realm signs a day when asked, so the documents before a signed one are often only made

def days(signer, count):
    return signer.make([(f"2026-11-{9 + n:02d}", issue_body(n + 1)) for n in range(count)])


def test_a_document_made_and_signed_later_verifies_and_says_when_it_was_made():
    signer = Signer(CANISTER)
    made = days(signer, 3)
    proof = signer.sign_made(made[2])
    result = page("verifyProof", proof, {"canister": CANISTER, "requested": {"kind": "journal", "subject": "2026-11-11"}})
    assert result["valid"] is True and all(c["status"] in ("pass", "note") for c in result["checks"])
    assert page("timeRow", result["doc"]) == ["Made at", "2026-09-21T14:13:20.000Z"]
    assert page("timeRow", json.loads(one()[1]["canonical"])) == ["Signed at", "2026-09-21T14:13:20.000Z"]  # an earlier release's
    assert page("timeRow", {}) == ["Signed at", ""]


def test_the_document_before_may_be_only_made_and_the_link_is_its_digest():
    signer = Signer(CANISTER)
    made = days(signer, 3)
    signed = signer.sign_made(made[2])
    doc = json.loads(signed["canonical"])
    assert code(doc["prev"]) == made[1]["code"] and made[1]["signature"] == ""
    link = page("checkPrevious", doc, {"$fetcher": {made[1]["code"]: made[1]}})
    assert link["status"] == "pass" and "made and not signed" in link["detail"]
    altered = {**made[1], "canonical": made[1]["canonical"].replace("2026-11-10", "2026-11-12")}
    assert page("checkPrevious", doc, {"$fetcher": {made[1]["code"]: altered}})["status"] == "fail"  # not the one it names


def test_the_walk_back_through_days_nobody_signed_is_checked_by_their_digests_and_a_signed_one_in_full():
    signer = Signer(CANISTER)
    made = days(signer, 5)
    made[1] = signer.sign_made(made[1])  # the second day was signed on request too
    after = json.loads(signer.sign_made(made[4])["canonical"])
    for position in (3, 2, 1, 0):
        earlier = page("checkEarlier", made[position], after, {"canister": CANISTER, "requested": {"code": made[position]["code"]}})
        assert earlier["ok"] is True and earlier["signed"] is (position == 1)
        after = earlier["doc"]
    assert after["prev"] is None


def test_the_walk_stops_at_an_earlier_document_that_is_not_the_one_named_another_realms_or_badly_signed():
    signer = Signer(CANISTER)
    made = days(signer, 3)
    after = json.loads(signer.sign_made(made[2])["canonical"])
    options = {"canister": CANISTER}
    altered = {**made[1], "canonical": made[1]["canonical"].replace("2026-11-10", "2026-11-12")}
    assert page("checkEarlier", altered, after, options)["ok"] is False
    stranger = Signer("aaaaa-aa", kind="journal")
    stranger.head = None
    foreign = stranger.make([("2026-11-10", issue_body(2))])[0]
    assert page("checkEarlier", foreign, {**after, "prev": foreign["digest"]}, options)["ok"] is False  # another realm's
    other_kind = Signer(CANISTER, kind="certificate").make([("C-1", {"holder": 1})])[0]
    assert page("checkEarlier", other_kind, {**after, "prev": other_kind["digest"]}, options)["ok"] is False
    forged = {**signer.sign_made(made[1]), "signature": "00" * 64}
    assert page("checkEarlier", forged, after, options)["ok"] is False  # signed, and the signature does not verify


def test_a_document_that_is_only_made_is_neither_valid_nor_red_and_never_reads_as_signed():
    signer = Signer(CANISTER)
    made = days(signer, 1)[0]
    result = page("verifyProof", made, {"canister": CANISTER})
    assert result["valid"] is False and result["shaped"] is False and result["unsigned"] is True
    assert all(c["status"] == "pass" for c in result["checks"])  # its text hashes to its digest, and the code is the digest's
    said = page("verdictOf", result)
    assert said["level"] == "neutral" and said["title"] == "Made, not signed yet" and "Request signature" in said["detail"]
    assert "valid" not in said["title"].lower()
    assert f"says it was made by {CANISTER}" in said["detail"] and "The realm made" not in said["detail"]  # a claim, not a fact
    pasted = page("verifyProof", made)  # not fetched from a canister: the name in the text is a claim
    assert pasted["unsigned"] is True and check(pasted, "The canister")["status"] == "note"


def test_a_made_document_of_another_canister_or_another_code_than_the_one_asked_for_is_red():
    signer = Signer(CANISTER)
    made = days(signer, 2)
    stranger = Signer("aaaaa-aa").make([("2026-11-09", issue_body(1))])[0]
    for options, proof in (({"canister": CANISTER}, stranger),  # the text names another canister than the one asked
                           ({"canister": CANISTER, "requested": {"code": made[1]["code"]}}, made[0]),  # another code
                           ({"canister": CANISTER, "requested": {"kind": "journal", "subject": "2026-11-10"}}, made[0])):
        result = page("verifyProof", proof, options)
        assert result["unsigned"] is False and result["valid"] is False and page("verdictOf", result)["level"] == "fail", options
    asked = {"canister": CANISTER, "requested": {"code": made[0]["code"]}}
    assert page("verifyProof", made[0], asked)["unsigned"] is True  # the one asked for, of the canister asked: neutral


def test_a_made_document_whose_text_or_code_was_changed_is_red_and_not_the_neutral_kind():
    signer = Signer(CANISTER)
    made = days(signer, 1)[0]
    for changed in ({**made, "canonical": made["canonical"].replace("2026-11-09", "2026-11-12")}, {**made, "code": "AAAA-AAAA-AAAA-AAAA"}):
        result = page("verifyProof", changed, {"canister": CANISTER})
        assert result["unsigned"] is False and result["valid"] is False
        assert page("verdictOf", result)["level"] == "fail"


# ---- The query to a realm: what the IC's HTTP interface and Candid expect


@pytest.mark.parametrize("text", [CANISTER, "aaaaa-aa", "ryjl3-tyaaa-aaaaa-aaaba-cai", "rrkah-fqaaa-aaaaa-aaaaq-cai"])
def test_a_canister_id_is_read_as_the_ic_reads_it(text):
    assert page("principalBytes", text) == Principal.from_str(text).bytes
    assert page("principalText", Principal.from_str(text).bytes) == text


@pytest.mark.parametrize("text", ["lxzze-o7777-77777-aaaaa-caa", "lxzze-o7777-77777-aaaaa", "not a canister"])
def test_a_canister_id_with_a_wrong_checksum_or_form_is_refused(text):
    with pytest.raises(RuntimeError):
        page("principalBytes", text)


def test_the_query_is_an_anonymous_cbor_request_to_the_replica_for_the_read_method():
    reply = cbor2.dumps(cbor2.CBORTag(55799, {"status": "replied", "reply": {"arg": encode([{"type": Types.Text,
                                                                                         "value": json.dumps({"ok": True})}])}}))
    out, request = page("readOp", "https://icp-api.io/", CANISTER, "signature_proof", {"code": "AAAA-AAAA-AAAA-AAAA"},
                        fetch={"status": 200, "body": reply})
    assert out == {"ok": True}
    assert request["url"] == f"https://icp-api.io/api/v2/canister/{CANISTER}/query" and request["method"] == "POST"
    assert request["headers"] == {"Content-Type": "application/cbor"}
    body = bytes.fromhex(request["body"])
    assert body[:3] == bytes([0xD9, 0xD9, 0xF7])  # the self-describing tag
    content = cbor2.loads(body)
    content = content.value["content"] if isinstance(content, cbor2.CBORTag) else content["content"]
    assert content["request_type"] == "query" and content["method_name"] == "read"
    assert content["canister_id"] == Principal.from_str(CANISTER).bytes and content["sender"] == b"\x04"
    assert 0 < content["ingress_expiry"] - 1_790_000_000 * 10**9 and content["ingress_expiry"] % 1 == 0
    args = [item["value"] for item in decode(content["arg"])]
    assert args == ["signature_proof", json.dumps({"code": "AAAA-AAAA-AAAA-AAAA"}, separators=(",", ":"))]


def test_a_rejected_query_and_a_refused_status_are_errors_with_the_reason():
    refused = cbor2.dumps({"status": "rejected", "reject_code": 3, "reject_message": "no such method"})
    with pytest.raises(RuntimeError, match="no such method"):
        page("readOp", "https://icp-api.io", CANISTER, "x", {}, fetch={"status": 200, "body": refused})
    with pytest.raises(RuntimeError, match="answered 429"):
        page("readOp", "https://icp-api.io", CANISTER, "x", {}, fetch={"status": 429, "body": b""})


def test_a_reply_that_is_not_a_text_is_refused():
    reply = cbor2.dumps({"status": "replied", "reply": {"arg": encode([{"type": Types.Nat, "value": 1}])}})
    with pytest.raises(RuntimeError, match="not a text"):
        page("readOp", "https://icp-api.io", CANISTER, "x", {}, fetch={"status": 200, "body": reply})


def test_fetching_a_proof_gives_the_proof_of_the_realms_reply_and_the_realms_refusal_as_an_error():
    _, proof = one()
    body = json.dumps({"ok": True, "kind": "journal", "subject": "2026-11-09", "proof": proof})
    reply = cbor2.dumps({"status": "replied", "reply": {"arg": encode([{"type": Types.Text, "value": body}])}})
    out, _ = page("fetchProof", "https://icp-api.io", CANISTER, {"code": proof["code"]}, fetch={"status": 200, "body": reply})
    assert out == proof
    refusal = json.dumps({"ok": False, "error": "no document has that verification code"})
    reply = cbor2.dumps({"status": "replied", "reply": {"arg": encode([{"type": Types.Text, "value": refusal}])}})
    with pytest.raises(RuntimeError, match="no document has that verification code"):
        page("fetchProof", "https://icp-api.io", CANISTER, {"code": "AAAA-AAAA-AAAA-AAAA"}, fetch={"status": 200, "body": reply})
