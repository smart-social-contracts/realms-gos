"""The verify page (docs/verify/index.html): its logic run under Node against proofs made with a real Ed25519 key and the
design's own rules (tests/deployed/verify_page.py), and its request to a realm against what the IC's HTTP interface and
Candid expect. The canister's own proofs go through the same page in test_journal_signing.py.

Skipped where there is no Node (22 or later, for WebCrypto's Ed25519) or no ``cryptography``."""

import hashlib
import json

import cbor2
import pytest
from ic.candid import Types, decode, encode
from ic.principal import Principal

from verify_page import NODE, Signer, code, issue_body, page

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
