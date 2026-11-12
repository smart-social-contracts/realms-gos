"""The documents of the official journal and their signatures on the real wasm, with the IC's own threshold Schnorr keys
(docs/codices/journal-signature.md): every closed day is made into a document linked to the one before it, whether or not
anyone signs it; a day is signed when anyone asks (``journal_sign``), or as it closes when the realm is set to sign daily.
A signature verifies as Ed25519 under the key the IC derives for the canister, and an altered or dropped document shows.
tests/ggg/test_journal_signing.py runs the same rules in process with a fake key.

The instance has a fiduciary subnet, which holds the keys: only ``key_1`` on the pocket-ic that CI runs (dfx 0.31's also
has ``dfx_test_key`` and ``test_key_1``, dfx 0.32's does not), so the realms are ``ic`` realms, which sign with ``key_1``,
and the names the other networks use are checked in the wasm without signing. Proofs are checked three ways: in Python
with ``cryptography`` (verify_page.reference_valid), by the verify page's own script under Node (docs/verify/index.html),
and, for the key, by asking the IC through another canister, which may ask for any canister's key.

The tests of this module run in order on one network: the clock only goes forward. One test starts from the release before
this one (``REALM_PREVIOUS_WASM``) and is skipped without it, as tests/deployed/test_upgrade_chain.py is."""

import json
import os
from datetime import datetime, timezone

import pytest
from network import Network
from pocket_ic import SubnetConfig
from verify_page import NODE, code, page, reference_valid

DAY = 86400
HOUR = 3600
ANONYMOUS = "2vxsx-fae"
PREVIOUS = os.environ.get("REALM_PREVIOUS_WASM")
RULES = ("import ggg\nggg.Extension(id='journal', manifest={'config': {'personal_data': {'entry_fields': "
         "['codex', 'layers', 'proposal', 'activates_at', 'reverted', 'source', 'version', 'effective_from']}}})")
RECORD = "from core import journal\njournal.record('codex_installed', codex='sha256:{n}')"
# Another canister asks the IC for the key of the journal's path for ``canister``: the IC answers whoever asks.
ASK = ("from core import chain\nfrom core.candid import Reader, idl_hash\nchain.ASKED = {}\n"
       "def ask():\n    reply = yield from chain.call('aaaaa-aa', 'schnorr_public_key', %s)\n"
       "    chain.ASKED['key'] = Reader(reply).args()[0][idl_hash('public_key')].hex()\nchain.set_timer(0, ask)")
FEE = 26_153_846_153  # what the IC charges a signature with key_1; PocketIC charges it for every key name
SLACK = 100_000_000  # the realm's own work around a signature and a sweep, in cycles
BURN = {}  # what an idle realm costs a second, measured in test_b: storage is paid for while nothing is signed
MEASURED = {}  # the cycles a thing cost, for the numbers the tests print


@pytest.fixture(scope="module")
def net(wasm_of, people):
    return Network(wasm_of("production"), people, SubnetConfig(application=1, fiduciary=True))


@pytest.fixture(scope="module")
def capital(net):
    realm = net.realm("capital", members=("alice",))
    shell(realm, "from ggg import Realm\nRealm['1'].network = 'ic'")  # the network whose key every pocket-ic holds
    return realm


@pytest.fixture(scope="module")
def other(net):
    realm = net.realm("other", members=("alice",))
    shell(realm, "from ggg import Realm\nRealm['1'].network = 'ic'")  # its own journal signs with key_1 too
    return realm


def wait(net, seconds):
    """Let time pass and the sweeps, with the calls they make to the management canister, run."""
    net.wait(seconds, rounds=30)


def date(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%d")


def issue(realm, day=None):
    return realm.ok("journal_issue", "alice", query=True, **({"day": day} if day else {}))["issue"]


def proof_of(realm, day):
    """The document of ``day`` as the realm serves it, signed or not."""
    out = realm.ok("signature_proof", "alice", query=True, kind="journal", subject=day)
    assert (out["kind"], out["subject"]) == ("journal", day)
    return out["proof"]


def ask(realm, day):
    """Somebody nobody knows asks for the signature of ``day``, as the realm's frontend does for a reader not signed in."""
    return realm.ok("journal_sign", ANONYMOUS, day=day)


def document(proof):
    return json.loads(proof["canonical"])


def shell(realm, code_):
    out = realm.shell(code_)
    assert "Traceback" not in out, out
    return out


def derived_key(net, asker, canister, name):
    """The public key the IC derives for ``canister``'s journal path under key ``name``, from the IC, through ``asker``."""
    path = "".join("\\%02x" % b for b in b"realms:journal")
    arg = (f'(record {{ canister_id = opt principal "{canister}"; derivation_path = vec {{ blob "{path}" }}; '
           f'key_id = record {{ algorithm = variant {{ ed25519 }}; name = "{name}" }} }})')
    shell(asker, ASK % repr(arg))
    wait(net, 5)
    return shell(asker, "print(chain.ASKED.get('key'))").strip()


def balance(net, realm):
    return net.pic.get_cycles_balance(realm.id)


def spent_on_signing(net, realm, seconds):
    """The cycles the realm spends while ``seconds`` pass, less what an idle realm costs in that time."""
    before = balance(net, realm)
    wait(net, seconds)
    return before - balance(net, realm) - round(BURN["per_second"] * seconds)


def unsigned_days(realm):
    """The closed days nobody has signed, oldest first."""
    return [day for day in closed_days(realm) if not proof_of(realm, day)["signature"]]


def stopped_for(net, realm, days):
    """The cycles the realm loses while it is stopped for ``days`` and an hour after it starts, which is time enough for the
    sweep's calls to be back (read as soon as it starts, the balance still holds the cycles of the calls a sweep has out). A
    stopped realm is charged its storage, which an idle realm's burn in this module over-estimates, so the tests compare the
    loss with bounds and not with that burn."""
    before = balance(net, realm)
    realm.stop()
    wait(net, days * DAY)
    realm.start()
    wait(net, HOUR)
    return before - balance(net, realm)


def closed_days(realm):
    """Every day of the journal that has closed and been made into a document, oldest first. A day that closed within the last
    hour is not made until the sweep comes, and one whose document was dropped (test_h) is gone: both are left out."""
    first = issue(realm)["first_day"]
    days, day = [], datetime.fromisoformat(first)
    while day.strftime("%Y-%m-%d") < issue(realm)["today"]:
        text = day.strftime("%Y-%m-%d")
        if realm.call("signature_proof", {"kind": "journal", "subject": text}, query=True)["ok"]:
            days.append(text)
        day = datetime.fromordinal(day.toordinal() + 1)
    return days


# ---- A day is made when it closes, and signed when anyone asks


def test_a_a_closed_issue_is_made_and_anyone_may_ask_for_its_signature_which_verifies_under_the_key_the_ic_derives(net, capital, other):
    shell(capital, RULES)
    shell(capital, RECORD.format(n=1))
    first = issue(capital)["first_day"]
    assert issue(capital)["status"] == "provisional" and "proof" not in issue(capital)
    wait(net, 6 * HOUR)  # past the first midnight
    closed = issue(capital, first)
    assert closed["status"] == "unsigned" and "proof" not in closed and closed["today"] != first  # made; nobody has asked
    made = proof_of(capital, first)
    assert made["root"] == made["signature"] == "" and made["path"] == [] and made["digest"]
    before = balance(net, capital)
    reply = ask(capital, first)  # an anonymous caller
    MEASURED["a signature on request"] = before - balance(net, capital)
    proof = reply["proof"]
    assert (reply["day"], reply["status"]) == (first, "signed")
    assert proof == proof_of(capital, first) == issue(capital, first)["proof"]
    assert (proof["canonical"], proof["digest"], proof["code"]) == (made["canonical"], made["digest"], made["code"])  # only a signature is added
    doc = document(proof)
    assert (doc["kind"], doc["realm"], doc["subject"], doc["prev"]) == ("journal", capital.cid, first, None)
    assert doc["body"]["number"] == 1 and doc["body"]["entries"] == closed["entries"] and len(closed["entries"]) == 1
    assert doc["key"]["algorithm"] == "ed25519" and doc["key"]["path"] == "realms:journal"
    assert doc["key"]["name"] == "key_1"  # an ic realm's key
    assert "made_at" in doc and "signed_at" not in doc
    assert proof["root"] == proof["digest"] and proof["path"] == []  # a batch of one
    assert json.dumps(doc, sort_keys=True, separators=(",", ":")) == proof["canonical"]  # the text CPython writes
    assert reference_valid(proof)
    key = derived_key(net, other, capital.cid, "key_1")
    assert doc["key"]["public_key"] == key  # the IC's own answer for this canister, not the realm's word
    assert derived_key(net, other, other.cid, "key_1") != key  # another canister, another key
    if NODE:
        result = page("verifyProof", proof, {"canister": capital.cid, "expectedKey": key})
        assert result["valid"] is True and all(c["status"] == "pass" for c in result["checks"]), result["checks"]
    spent = MEASURED["a signature on request"]
    print(f"a signature on request: {spent:,} cycles ({FEE:,} is the IC's fee)")
    assert abs(spent - FEE) < SLACK


def test_b_a_second_request_makes_no_second_signature_and_the_sweeps_after_it_cost_nothing(net, capital):
    first = issue(capital)["first_day"]
    before, cycles = proof_of(capital, first), balance(net, capital)
    again = ask(capital, first)
    assert again["status"] == "signed" and again["proof"] == before
    asked = cycles - balance(net, capital)
    MEASURED["a request for a signed day"] = asked
    assert asked < FEE // 100  # no call to the management canister: only the instructions of finding the proof
    cycles = balance(net, capital)
    wait(net, 3 * HOUR)  # three sweeps, and no day has closed
    assert proof_of(capital, first) == before == issue(capital, first)["proof"]
    idle = cycles - balance(net, capital)
    assert idle < FEE // 10  # a sweep that makes nothing costs its instructions, and storage is paid for as ever
    BURN["per_second"] = idle / (3 * HOUR)
    print(f"a request for a signed day: {asked:,} cycles; an idle realm burns {BURN['per_second'] * DAY:,.0f} cycles a day, three sweeps included")


def test_c_each_day_is_made_as_it_closes_for_no_signature_and_links_to_the_one_before(net, capital):
    first = issue(capital)["first_day"]
    spent = [spent_on_signing(net, capital, DAY) for _ in range(2)]
    days = closed_days(capital)
    proofs = [proof_of(capital, day) for day in days]
    assert days[0] == first and len(proofs) >= 3
    docs = [document(p) for p in proofs]
    assert [d["body"]["number"] for d in docs] == list(range(1, len(docs) + 1))
    assert [d["prev"] for d in docs] == [None] + [p["digest"] for p in proofs[:-1]]
    assert [bool(p["signature"]) for p in proofs] == [True] + [False] * (len(proofs) - 1)  # only the one asked for
    print(f"cycles a closing day costs beyond an idle realm's, made and not signed: {spent}")
    MEASURED["a day made, no signature"] = max(spent)
    assert all(abs(s) < SLACK for s in spent), spent  # SHA-256 and one free call for the key, and no signature


def test_d_a_fact_with_characters_beyond_ascii_is_signed_as_the_same_ascii_text_cpython_writes(net, capital):
    fact = "sha256:é中😀"
    shell(capital, f"from core import journal\njournal.record('codex_installed', codex={fact!r})")
    day = issue(capital)["day"]
    wait(net, DAY)
    made = proof_of(capital, day)
    proof = ask(capital, day)["proof"]
    text = proof["canonical"]
    assert text == made["canonical"] and text.isascii() and "\\u00e9" in text and "\\u4e2d" in text and "\\ud83d\\ude00" in text
    assert json.dumps(json.loads(text), sort_keys=True, separators=(",", ":")) == text  # CPython's text, byte for byte
    assert document(proof)["body"]["entries"][0]["details"]["codex"] == fact == issue(capital, day)["entries"][0]["details"]["codex"]
    assert reference_valid(proof)
    if NODE:
        assert page("verifyProof", proof, {"canister": capital.cid, "requested": {"kind": "journal", "subject": day}})["valid"] is True


def test_e_days_that_closed_while_the_realm_was_stopped_are_made_in_batches_of_a_month_and_one_signature_covers_them_all(net, capital):
    last = closed_days(capital)[-1]
    spent = stopped_for(net, capital, 40)
    new = [d for d in closed_days(capital) if d > last]
    proofs = [proof_of(capital, d) for d in new]
    assert len(new) >= 40 and all(p["signature"] == "" for p in proofs)
    docs = [document(p) for p in proofs]
    assert docs[0]["prev"] == proof_of(capital, last)["digest"]
    assert [d["prev"] for d in docs[1:]] == [p["digest"] for p in proofs[:-1]]
    assert docs[0]["made_at"] > net.pic.get_time()["nanos_since_epoch"] // 10**9 - 2 * HOUR  # made late, and says so
    # Forty days stopped and made: storage, SHA-256 and two calls for the key. A signature (26B) would not fit in the bound.
    MEASURED["forty days stopped and made, no signature"] = spent
    print(f"forty days stopped and made: {spent:,} cycles lost (an idle realm burns {40 * DAY * BURN['per_second']:,.0f} in that time)")
    assert spent < 1.5 * 40 * DAY * BURN["per_second"], spent
    # One signature, on the last day, is all a reader needs: every day before it is linked to it by digest, back to the
    # first, which was signed on its own.
    if NODE:
        signed = ask(capital, new[-1])["proof"]
        after, walked = document(signed), 0
        while after["prev"] is not None:
            earlier_day = date(datetime.fromisoformat(after["subject"]).replace(tzinfo=timezone.utc).timestamp() - DAY)
            earlier = proof_of(capital, earlier_day)
            checked = page("checkEarlier", earlier, after, {"canister": capital.cid, "requested": {"code": earlier["code"]}})
            assert checked["ok"] is True and checked["signed"] is bool(earlier["signature"])
            after, walked = checked["doc"], walked + 1
        assert after["subject"] == issue(capital)["first_day"] and walked >= 40


def test_f_two_requests_sent_together_for_one_day_make_one_signature_and_the_second_is_pending(net, capital):
    day = unsigned_days(capital)[-1]
    before = balance(net, capital)
    replies = capital.finish(capital.submit("journal_sign", {"day": day}), capital.submit("journal_sign", {"day": day}))
    spent = before - balance(net, capital)
    assert [r["status"] for r in replies] == ["signed", "pending"] and "proof" not in replies[1]
    assert abs(spent - FEE) < SLACK, spent  # one signature, not two
    assert replies[0]["proof"] == proof_of(capital, day) and reference_valid(replies[0]["proof"])
    assert ask(capital, day) == replies[0]  # and asked again, the proof for nothing


# ---- An issue altered or dropped shows


def test_g_an_entry_altered_after_the_issue_was_signed_shows_and_so_does_a_changed_text(net, capital):
    shell(capital, RECORD.format(n=2))
    day = issue(capital)["day"]
    wait(net, 2 * DAY)  # the day closes, and so does the one after it
    following = closed_days(capital)[closed_days(capital).index(day) + 1]
    ask(capital, following)  # the day after it is signed too, and names it by its digest
    proof = ask(capital, day)["proof"]
    signed_entries = document(proof)["body"]["entries"]
    assert len(signed_entries) == 1 and issue(capital, day)["entries"] == signed_entries
    shell(capital, f"from ggg import JournalEntry\nfor e in JournalEntry.load_some(1, 500):\n    if e.day == '{day}':\n"
                   "        e.kind = 'forged'")
    assert issue(capital, day)["entries"] != signed_entries  # what the realm now shows is not what it signed
    assert reference_valid(proof_of(capital, day)) and document(proof_of(capital, day))["body"]["entries"] == signed_entries
    shell(capital, f"from ggg import SignedDocument\nd = SignedDocument['journal:{day}']\n"
                   "d.canonical = d.canonical.replace('codex_installed', 'forged')")
    changed = proof_of(capital, day)
    assert not reference_valid(changed)
    if NODE:
        assert page("verifyProof", changed, {"canister": capital.cid})["valid"] is False
        link = page("checkPrevious", document(proof_of(capital, following)), {"$fetcher": {code(document(proof_of(capital, following))["prev"]): changed}})
        assert link["status"] == "fail"  # the document it follows is no longer the one it names


def test_h_a_dropped_issue_leaves_a_gap_in_the_chain_that_a_later_issue_shows(net, capital):
    days = closed_days(capital)
    dropped, after = days[3], days[4]
    ask(capital, after)
    next_doc = document(proof_of(capital, after))
    assert next_doc["prev"] == proof_of(capital, dropped)["digest"]
    shell(capital, f"from ggg import SignedDocument\nSignedDocument['journal:{dropped}'].delete()")
    assert issue(capital, dropped)["status"] == "unsigned" and "proof" not in issue(capital, dropped)
    out = capital.call("signature_proof", {"code": code(next_doc["prev"])}, query=True)
    assert not out["ok"] and out["kind"] == "not_found"  # the document it follows is no longer served
    wait(net, 2 * HOUR)
    assert capital.call("signature_proof", {"kind": "journal", "subject": dropped}, query=True)["kind"] == "not_found"
    assert reference_valid(proof_of(capital, after))  # the later issue is still genuine: it is the gap that shows


# ---- The keys, the setting, an upgrade, and what it costs


def test_i_each_network_names_its_own_key_and_an_ic_realm_signs_with_key_1(capital):
    names = {"local": "dfx_test_key", "test": "test_key_1", "staging": "test_key_1", "demo": "test_key_1",
             "ic": "key_1", "": "key_1"}
    for network, name in names.items():
        shell(capital, f"from ggg import Realm\nRealm['1'].network = '{network}'")
        assert shell(capital, "from core import ic_keys\nprint(ic_keys.key_name())").strip() == name
    shell(capital, "from ggg import Realm\nRealm['1'].network = 'ic'")  # nothing is made meanwhile: no time passed
    assert document(proof_of(capital, closed_days(capital)[-1]))["key"]["name"] == "key_1"


def test_j_a_realm_set_to_sign_daily_by_a_vote_signs_each_day_as_it_closes_for_one_signature(net, capital):
    assert capital.ok("realm_info", "alice", query=True)["realm"]["journal_signing"] == "on_request"
    before = closed_days(capital)
    ask(capital, before[-1])  # the newest day is signed on request: every day before it is covered by it, and unsigned still
    unsigned = unsigned_days(capital)
    capital.govern("realm_configure", {"journal_signing": "daily"})  # and a daily realm's sweep goes on from it
    assert capital.ok("realm_info", "alice", query=True)["realm"]["journal_signing"] == "daily"
    cycles = balance(net, capital)
    for _ in range(2):
        wait(net, DAY)  # a day at a time: the clock jumps, and the sweep that follows makes the days that closed in the jump together
    wait(net, 120)  # a little more for the last signature to be back
    spent = cycles - balance(net, capital) - round(BURN["per_second"] * (2 * DAY + 120))
    new = [d for d in closed_days(capital) if d not in before]
    proofs = [proof_of(capital, d) for d in new]
    # Two midnights pass; a day that closed within the hour before the vote is made, and signed, after it: three at most.
    assert 2 <= len(new) <= 3, new
    assert all(reference_valid(p) for p in proofs) and len({p["signature"] for p in proofs}) == len(new)
    assert [document(p)["prev"] for p in proofs[1:]] == [p["digest"] for p in proofs[:-1]]
    print(f"cycles spent in {len(new)} days, one signature each, daily: {spent:,}")
    MEASURED["a day signed by a daily realm"] = spent // len(new)
    assert abs(spent - len(new) * FEE) < 3 * SLACK, spent  # one threshold signature a day, and nothing else of cost
    assert unsigned_days(capital) == unsigned  # the days before the last signed one stay as they were


def test_k_days_that_closed_while_a_daily_realm_was_stopped_are_signed_in_batches_of_a_month(net, capital):
    last = closed_days(capital)[-1]
    spent = stopped_for(net, capital, 40)
    new = [d for d in closed_days(capital) if d > last]
    proofs = [proof_of(capital, d) for d in new]
    assert len(new) >= 40
    roots = [p["root"] for p in proofs]
    assert len(set(roots)) == 2 and roots[:31] == [roots[0]] * 31  # a month in one batch, the rest in another
    assert max(len(p["path"]) for p in proofs) == 5 and all(reference_valid(p) for p in proofs)
    # Two signatures for forty days, on top of what being stopped costs (the roots say it was two signatures, not forty).
    MEASURED["forty days stopped, made and signed in two batches"] = spent
    print(f"forty days stopped, made and signed: {spent:,} cycles lost (two fees are {2 * FEE:,})")
    assert 2 * FEE <= spent <= 2 * FEE + 1.5 * 40 * DAY * BURN["per_second"], spent
    assert document(proofs[0])["prev"] == proof_of(capital, last)["digest"]
    if NODE:
        key = document(proofs[0])["key"]["public_key"]
        for proof in (proofs[0], proofs[15], proofs[30], proofs[31], proofs[-1]):
            assert page("verifyProof", proof, {"canister": capital.cid, "expectedKey": key})["valid"] is True


def test_l_a_realm_set_back_to_on_request_signs_nothing_by_itself_again(net, capital):
    capital.govern("realm_configure", {"journal_signing": "on_request"})
    spent = spent_on_signing(net, capital, 2 * DAY + 120)
    newest = proof_of(capital, closed_days(capital)[-1])
    assert newest["signature"] == "" and spent < SLACK, spent


def test_m_an_upgrade_keeps_the_documents_signed_or_not_and_the_setting_and_the_sweep_goes_on(net, capital):
    days = closed_days(capital)
    kept = [proof_of(capital, d) for d in days[-3:]]
    assert any(p["signature"] for p in [proof_of(capital, d) for d in days]) and kept[-1]["signature"] == ""
    net.wait(600)
    capital.upgrade()
    assert [proof_of(capital, d) for d in days[-3:]] == kept
    assert capital.ok("realm_info", "alice", query=True)["realm"]["journal_signing"] == "on_request"
    wait(net, DAY)  # the sweep is set again by post_upgrade
    newest = proof_of(capital, closed_days(capital)[-1])
    assert document(newest)["prev"] == kept[-1]["digest"] and newest["signature"] == ""


@pytest.mark.skipif(not PREVIOUS, reason="REALM_PREVIOUS_WASM names no wasm")
def test_n_a_realm_upgraded_from_the_release_that_signed_every_day_keeps_its_signed_days_and_stops_signing_by_itself(wasm_of, people):
    old = Network(PREVIOUS, people, SubnetConfig(application=1, fiduciary=True))
    realm = old.realm("signed-daily", members=("alice",))
    shell(realm, "from ggg import Realm\nRealm['1'].network = 'ic'")
    shell(realm, RULES)
    shell(realm, RECORD.format(n=1))
    wait(old, 3 * DAY + 6 * HOUR)  # the release before this one signs each day as it closes
    days = closed_days(realm)
    before = [proof_of(realm, d) for d in days]
    assert len(days) >= 3 and all(reference_valid(p) for p in before)
    assert all("signed_at" in document(p) and "made_at" not in document(p) for p in before)
    old.wait(600)
    realm.upgrade(wasm_of("production"))
    assert [proof_of(realm, d) for d in days] == before  # the text, digest and proof, byte for byte
    assert all(reference_valid(proof_of(realm, d)) for d in days)
    assert realm.ok("realm_info", "alice", query=True)["realm"]["journal_signing"] == "on_request"  # a row with no setting
    cycles = balance(old, realm)
    wait(old, 2 * DAY)  # two more days close, and nothing signs them
    assert cycles - balance(old, realm) < FEE // 10
    new = [d for d in closed_days(realm) if d > days[-1]]
    made = [proof_of(realm, d) for d in new]
    assert len(new) >= 2 and all(p["signature"] == "" for p in made)
    assert document(made[0])["prev"] == before[-1]["digest"] and "made_at" in document(made[0])  # linked to the last signed
    assert [document(p)["prev"] for p in made[1:]] == [p["digest"] for p in made[:-1]]
    proof = ask(realm, new[-1])["proof"]
    assert reference_valid(proof) and realm.ok("journal_issue", "alice", query=True, day=days[0])["issue"]["proof"] == before[0]
    if NODE:
        after = document(proof)
        for p in reversed(made[:-1]):
            checked = page("checkEarlier", p, after, {"canister": realm.cid, "requested": {"code": p["code"]}})
            assert checked["ok"] is True and checked["signed"] is False
            after = checked["doc"]
        checked = page("checkEarlier", before[-1], after, {"canister": realm.cid, "requested": {"code": before[-1]["code"]}})
        assert checked["ok"] is True and checked["signed"] is True  # the last the old release signed, in full


def test_o_what_making_and_signing_cost_in_instructions(capital, costs):
    day = closed_days(capital)[-1]
    ask(capital, day)  # the newest day is signed, so that a daily sweep has nothing to sign either
    nothing = ("from core import chain, journal_signing, op\nkeep = chain.set_timer\nchain.set_timer = lambda *a, **k: None\n"
               "try:\n    op.run(journal_signing.sweep())\nfinally:\n    chain.set_timer = keep")
    costs.measure(capital, "journal sweep that makes nothing: on request", nothing)
    shell(capital, "from ggg import Realm\nRealm['1'].journal_signing = 'daily'")
    costs.measure(capital, "journal sweep that makes nothing: daily", nothing)
    shell(capital, "from ggg import Realm\nRealm['1'].journal_signing = 'on_request'")
    costs.measure(capital, "journal: read an issue, signed or not", f"from core import journal, journal_signing\n"
                  f"journal_signing.shown(journal.issue('{day}'))")
    costs.measure(capital, "journal: make one day into a document (and drop it again)",
                  f"from core import journal, journal_signing, op, signing\nfrom ggg import SignedDocument\n"
                  "def key(kind, name):\n    return bytes(32)\n    yield\nasked, signing.public_key = signing.public_key, key\n"
                  f"try:\n    op.run(signing.make('measure', False, lambda: ['{day}'], lambda d: journal_signing.body(journal.issue(d))))\n"
                  "finally:\n    signing.public_key = asked\nSignedDocument['measure:" + day + "'].delete()")
    costs.measure(capital, "journal signing: the tree and paths of a month of issues",
                  "import hashlib\nfrom core import signing\n"
                  "signing.merkle([hashlib.sha256(bytes([n])).digest() for n in range(31)])")
    costs.measure(capital, "signature_proof: find a proof by its code",
                  f"from core import signing\nsigning.find(signing.proof('journal', '{day}')['code'])")
    for what, cycles in MEASURED.items():
        print(f"{what}: {cycles:,} cycles")
