"""The facility that lets the realm's canister sign documents of any kind (docs/codices/journal-signature.md), in process,
with a second kind, ``certificate``, so that nothing here rests on the journal: a key and a path per kind, batches under
one signature over a Merkle root, the chain a kind may have, and ``signature_proof``, the one read that serves them all."""

import base64
import hashlib
import json
from types import SimpleNamespace

import fake_chain
import pytest
import world
from core import op, signing
from core.candid import Reader
from core.db import Fail
from ggg import Realm, SignedDocument
from kit import call, ok
from signing_kit import climb, fake_key, fake_signature, management


def sign(kind, documents, chained=False):
    return op.run(signing.sign_batch(kind, documents, chained))


def sign_fresh(kind, documents, fresh):
    return op.run(signing.sign_batch(kind, documents, False, fresh))


def certificates(count):
    return [(f"C-{n}", {"holder": f"member-{n}", "course": "first aid"}) for n in range(1, count + 1)]


def document(kind, subject):
    return json.loads(signing.proof(kind, subject)["canonical"])


# ---- The tree: one root for a batch, a path for each document


@pytest.mark.parametrize("count", range(1, 10))
def test_every_leafs_path_climbs_to_the_root_whatever_the_size_of_the_batch(count):
    leaves = [hashlib.sha256(bytes([n])).digest() for n in range(count)]
    root, paths = signing.merkle(leaves)
    assert [climb(leaf.hex(), path) for leaf, path in zip(leaves, paths)] == [root.hex()] * count
    if count == 1:
        assert root == leaves[0] and paths == [[]]  # a batch of one: the root is the digest


def test_a_path_leads_to_the_root_only_from_its_own_leaf_and_a_changed_sibling_breaks_it():
    leaves = [hashlib.sha256(bytes([n])).digest() for n in range(5)]
    root, paths = signing.merkle(leaves)
    assert climb(leaves[1].hex(), paths[2]) != root.hex()
    side, sibling = paths[3][0]
    broken = [[side, hashlib.sha256(bytes.fromhex(sibling)).hexdigest()]] + paths[3][1:]
    assert climb(leaves[3].hex(), broken) != root.hex()


def test_a_node_is_hashed_with_a_prefix_so_that_it_cannot_pass_for_a_leaf():
    left, right = hashlib.sha256(b"a").digest(), hashlib.sha256(b"b").digest()
    root, _ = signing.merkle([left, right])
    assert root == hashlib.sha256(b"\x01" + left + right).digest() and root != hashlib.sha256(left + right).digest()


# ---- A kind is a key, and a batch is one signature


def test_each_kind_signs_under_its_own_path_and_key_and_the_same_subject_may_be_in_two_kinds():
    world.build()
    peer = management()
    sign("certificate", certificates(1))
    sign("decision", [("C-1", {"text": "approved"})])
    paths = [Reader(arg).args()[0]["derivation_path"] for arg in peer.sent("sign_with_schnorr")]
    assert paths == [[b"realms:certificate"], [b"realms:decision"]]
    keys = [document(kind, "C-1")["key"] for kind in ("certificate", "decision")]
    assert [k["path"] for k in keys] == ["realms:certificate", "realms:decision"]
    assert keys[0]["public_key"] == fake_key("key_1", b"realms:certificate").hex() != keys[1]["public_key"]


def test_a_batch_of_many_documents_is_one_signature_over_one_root_and_each_climbs_to_it():
    world.build()
    peer = management()
    assert sign("certificate", certificates(5)) is True
    proofs = [signing.proof("certificate", f"C-{n}") for n in range(1, 6)]
    assert len(peer.sent("sign_with_schnorr")) == 1
    assert len({p["root"] for p in proofs}) == 1 and len({p["signature"] for p in proofs}) == 1
    for p in proofs:
        assert p["digest"] == hashlib.sha256(p["canonical"].encode()).hexdigest()
        assert climb(p["digest"], p["path"]) == p["root"]
        assert p["signature"] == fake_signature(bytes.fromhex(p["root"])).hex()


def test_a_kind_that_is_not_chained_links_nothing_and_keeps_no_head():
    world.build()
    management()
    sign("certificate", certificates(2))
    sign("certificate", certificates(3)[2:])
    assert [document("certificate", f"C-{n}")["prev"] for n in (1, 2, 3)] == [None, None, None]
    assert signing.head("certificate") is None


def test_a_chained_kind_links_each_document_to_the_one_before_across_batches():
    world.build()
    management()
    sign("certificate", certificates(2), chained=True)
    sign("certificate", certificates(4)[2:], chained=True)
    digests = [signing.proof("certificate", f"C-{n}")["digest"] for n in range(1, 5)]
    assert [document("certificate", f"C-{n}")["prev"] for n in range(1, 5)] == [None] + digests[:3]
    assert signing.head("certificate") == {"subject": "C-4", "digest": digests[3]}


def test_the_text_names_its_kind_subject_and_realm_so_a_document_cannot_be_passed_for_another():
    world.build()
    management()
    sign("certificate", certificates(1))
    doc = document("certificate", "C-1")
    assert (doc["v"], doc["kind"], doc["subject"], doc["realm"]) == (1, "certificate", "C-1", fake_chain.self_id())
    assert doc["body"] == {"holder": "member-1", "course": "first aid"}
    assert doc["signed_at"] == fake_chain.now()


def test_the_key_of_the_network_signs_the_text_and_is_named_in_it():
    world.build()
    management()
    Realm["1"].network = "local"
    sign("certificate", certificates(1))
    assert document("certificate", "C-1")["key"]["name"] == "dfx_test_key"


# ---- What is refused, and what a failure leaves


def test_a_subject_is_signed_once_and_a_second_try_is_refused():
    world.build()
    peer = management()
    sign("certificate", certificates(1))
    first = signing.proof("certificate", "C-1")
    calls = len(peer.calls)
    assert sign("certificate", [("C-1", {"holder": "someone else"})]) is False
    assert "signed already" in signing.failures()["certificate"]["error"] and len(peer.calls) == calls
    assert signing.proof("certificate", "C-1") == first


@pytest.mark.parametrize("kind", ["", "Certificate", "a b", "x:y", "x/y", "é"])
def test_a_kind_is_lower_case_letters_digits_and_dashes(kind):
    world.build()
    peer = management()
    assert sign(kind, certificates(1)) is False and "lower-case letters" in signing.failures()[kind]["error"]
    assert peer.calls == [] and SignedDocument.count() == 0


def test_a_failed_batch_keeps_nothing_and_the_next_try_signs_it_and_clears_the_failure():
    world.build()
    peer = management()
    peer.answer("sign_with_schnorr", fake_chain.rejects("no cycles"))
    assert sign("certificate", certificates(3)) is False
    failure = signing.failures()["certificate"]
    assert failure["subjects"] == ["C-1", "C-2", "C-3"] and failure["at"] == fake_chain.now()
    assert "no cycles" in failure["error"] and SignedDocument.count() == 0
    management()
    assert sign("certificate", certificates(3)) is True
    assert signing.failures() == {} and SignedDocument.count() == 3


def test_a_failed_kind_does_not_hold_another_back():
    world.build()
    peer = management()
    peer.answer("sign_with_schnorr", fake_chain.rejects("no cycles"))
    sign("certificate", certificates(1))
    management()
    assert sign("decision", [("D-1", {"text": "approved"})]) is True
    assert list(signing.failures()) == ["certificate"] and signing.proof("decision", "D-1") is not None


# ---- signature_proof: any kind's proof, by subject or by code


def proof_of(who="alice", **args):
    return ok("signature_proof", who, **args)


def test_the_proof_of_any_kind_is_served_by_its_kind_and_subject_to_anyone():
    world.build()
    management()
    sign("certificate", certificates(2))
    out = proof_of(fake_chain.ANONYMOUS, kind="certificate", subject="C-2")
    assert (out["kind"], out["subject"]) == ("certificate", "C-2")
    assert out["proof"] == signing.proof("certificate", "C-2")
    assert set(out["proof"]) == {"canonical", "digest", "root", "path", "signature", "code"}


def test_the_proof_is_found_by_its_code_in_any_case_with_or_without_dashes():
    world.build()
    management()
    sign("certificate", certificates(2))
    code = signing.proof("certificate", "C-2")["code"]
    for written in (code, code.lower(), code.replace("-", ""), " ".join(code.split("-"))):
        out = proof_of(code=written)
        assert (out["kind"], out["subject"]) == ("certificate", "C-2")


def test_a_second_read_gives_the_same_proof():
    world.build()
    management()
    sign("certificate", certificates(1))
    assert proof_of(kind="certificate", subject="C-1") == proof_of(kind="certificate", subject="C-1")


def test_an_unknown_document_or_code_is_not_found_and_a_bad_request_is_refused():
    world.build()
    management()
    sign("certificate", certificates(1))
    for args in ({"kind": "certificate", "subject": "C-9"}, {"kind": "decision", "subject": "C-1"},
                 {"code": "AAAA-AAAA-AAAA-AAAA"}):
        assert call("signature_proof", "alice", **args)["kind"] == "not_found"
    for args in ({}, {"kind": "certificate"}, {"kind": 1, "subject": "C-1"}, {"code": "AAAA"}):
        assert call("signature_proof", "alice", **args)["kind"] == "invalid_argument"


def test_the_refusal_does_not_echo_what_it_was_given_beyond_a_few_characters():
    world.build()
    out = call("signature_proof", "alice", kind="x" * 5000, subject="y" * 5000)
    assert out["kind"] == "not_found" and len(out["error"]) < 120


def test_the_codes_of_two_documents_differ_and_a_code_is_sixteen_base32_digits_in_four_groups():
    world.build()
    management()
    sign("certificate", certificates(40))
    codes = [signing.proof("certificate", f"C-{n}")["code"] for n in range(1, 41)]
    assert len(set(codes)) == 40
    assert all(len(code) == 19 and code.count("-") == 3 and set(code.replace("-", "")) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")
               for code in codes)


# ---- The canonical form is ASCII with escapes, whoever writes the JSON


TEXT = {"b": "é中😀 \x7f \" \\ \n \u2028", "a": [1, None, True], "é": {"z": "x"}}


def test_the_text_is_what_ensure_ascii_writes_with_sorted_keys_and_no_spaces():
    expected = json.dumps(TEXT, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    assert signing.canonical(TEXT) == expected and expected.isascii()
    assert "\\ud83d\\ude00" in expected and "\\u007f" in expected  # a pair for what is above U+FFFF, and DEL
    assert json.loads(signing.canonical(TEXT)) == TEXT


def test_the_text_is_the_same_when_json_writes_non_ascii_as_it_is_as_the_canisters_does(monkeypatch):
    expected = signing.canonical(TEXT)
    monkeypatch.setattr(signing, "json", SimpleNamespace(
        dumps=lambda document, **kw: json.dumps(document, **{**kw, "ensure_ascii": False})))
    assert signing.canonical(TEXT) == expected


def test_a_signed_document_with_non_ascii_facts_is_ascii_and_reads_back_the_same():
    world.build()
    management()
    sign("certificate", [("C-é", {"holder": "José 中 😀"})])
    proof = signing.proof("certificate", "C-é")
    assert proof["canonical"].isascii() and document("certificate", "C-é")["body"] == {"holder": "José 中 😀"}
    assert proof["digest"] == hashlib.sha256(proof["canonical"].encode()).hexdigest()


# ---- What a signature is sent with, and what the realm checks first


@pytest.mark.parametrize("network,fee", [("ic", 26_153_846_153), ("", 26_153_846_153), ("local", 26_153_846_153),
                                         ("staging", 10_000_000_000), ("test", 10_000_000_000)])
def test_a_signature_is_sent_with_the_fee_of_its_key_and_half_as_much_again(network, fee):
    world.build()
    management()
    Realm["1"].network = network
    sign("certificate", certificates(1))
    assert [cycles for _, method, cycles in fake_chain.state["attached"] if method == "sign_with_schnorr"] == [fee * 3 // 2]
    assert [cycles for _, method, cycles in fake_chain.state["attached"] if method == "schnorr_public_key"] == [0]


def test_a_realm_without_the_cycles_asks_for_nothing_and_says_what_it_lacks():
    world.build()
    peer = management()
    fake_chain.state["balance"] = 1_000_000_000
    assert sign("certificate", certificates(1)) is False
    error = signing.failures()["certificate"]["error"]
    assert "1000000000 cycles" in error and "key_1" in error and "39230769229" in error
    assert peer.calls == [] and SignedDocument.count() == 0


def test_the_key_is_named_once_so_that_a_change_of_network_in_flight_cannot_split_text_and_signature():
    world.build()
    peer = management()
    Realm["1"].network = "ic"
    asked = peer.replies["schnorr_public_key"]

    def moved(arg):
        Realm["1"].network = "staging"  # while the calls are out
        return asked(arg)

    peer.answer("schnorr_public_key", moved)
    sign("certificate", certificates(1))
    named = Reader(peer.sent("sign_with_schnorr")[0]).args()[0]["key_id"]["name"]
    assert named == "key_1" == document("certificate", "C-1")["key"]["name"]


def test_a_batch_whose_documents_changed_while_it_was_signed_is_not_kept_and_says_so():
    world.build()
    management()
    assert sign_fresh("certificate", certificates(2), lambda: certificates(2)) is True
    assert sign_fresh("decision", [("D-1", {"n": 1})], lambda: [("D-1", {"n": 2})]) is False
    assert "changed while they were signed" in signing.failures()["decision"]["error"]
    assert signing.proof("decision", "D-1") is None and signing.proof("certificate", "C-2") is not None


def test_a_bad_kind_is_refused_before_anything_is_asked_of_anyone():
    world.build()
    peer = management()
    assert sign("Bad kind", certificates(1)) is False and peer.calls == [] and fake_chain.state["attached"] == []


@pytest.mark.parametrize("n", range(8))
def test_the_code_is_the_first_ten_bytes_of_the_digest_in_standard_base32(n):
    digest = hashlib.sha256(bytes([n])).hexdigest()
    standard = base64.b32encode(bytes.fromhex(digest)[:10]).decode()  # the canister's base64 has no b32encode
    assert signing.code_of(digest) == "-".join(standard[i:i + 4] for i in range(0, 16, 4))


def test_a_bad_kind_name_is_a_fail_when_the_path_is_asked_for():
    with pytest.raises(Fail):
        signing.path("Certificate")
