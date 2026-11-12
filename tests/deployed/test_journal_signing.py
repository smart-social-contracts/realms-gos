"""The signature on each closed issue of the official journal on the real wasm, with the IC's own threshold Schnorr keys
(docs/codices/journal-signature.md): an issue is signed once its day has closed, the signature verifies as Ed25519 under
the key the IC derives for the canister, and an altered or dropped issue shows. tests/ggg/test_journal_signing.py runs the
same rules in process with a fake key.

The instance has a fiduciary subnet, which holds the keys: only ``key_1`` on the pocket-ic that CI runs (dfx 0.31's also
has ``dfx_test_key`` and ``test_key_1``, dfx 0.32's does not), so the realms are ``ic`` realms, which sign with ``key_1``,
and the names the other networks use are checked in the wasm without signing. Proofs are checked three ways: in Python
with ``cryptography`` (verify_page.reference_valid), by the verify page's own script under Node (docs/verify/index.html),
and, for the key, by asking the IC through another canister, which may ask for any canister's key.

The tests of this module run in order on one network: the clock only goes forward."""

import json
from datetime import datetime, timezone

import pytest
from network import Network
from verify_page import NODE, code, page, reference_valid

DAY = 86400
HOUR = 3600
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


@pytest.fixture(scope="module")
def net(wasm_of, people):
    return Network(wasm_of("production"), people, keys=True)


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
    out = realm.ok("signature_proof", "alice", query=True, kind="journal", subject=day)
    assert (out["kind"], out["subject"]) == ("journal", day)
    return out["proof"]


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


def closed_days(realm):
    """Every day of the journal that has closed, oldest first."""
    first = issue(realm)["first_day"]
    days, day = [], datetime.fromisoformat(first)
    while day.strftime("%Y-%m-%d") < issue(realm)["today"]:
        days.append(day.strftime("%Y-%m-%d"))
        day = datetime.fromordinal(day.toordinal() + 1)
    return days


# ---- An issue is signed once its day has closed, and the signature verifies


def test_a_an_issue_closed_is_signed_and_its_signature_verifies_under_the_key_the_ic_derives(net, capital, other):
    shell(capital, RULES)
    shell(capital, RECORD.format(n=1))
    first = issue(capital)["first_day"]
    assert issue(capital)["status"] == "provisional" and "proof" not in issue(capital)
    wait(net, 6 * HOUR)  # past the first midnight
    closed = issue(capital, first)
    assert closed["status"] == "signed" and closed["today"] != first
    proof = closed["proof"]
    assert proof == proof_of(capital, first)
    doc = document(proof)
    assert (doc["kind"], doc["realm"], doc["subject"], doc["prev"]) == ("journal", capital.cid, first, None)
    assert doc["body"]["number"] == 1 and doc["body"]["entries"] == closed["entries"] and len(closed["entries"]) == 1
    assert doc["key"]["algorithm"] == "ed25519" and doc["key"]["path"] == "realms:journal"
    assert doc["key"]["name"] == "key_1"  # an ic realm's key
    assert proof["root"] == proof["digest"] and proof["path"] == []  # a batch of one
    assert reference_valid(proof)
    key = derived_key(net, other, capital.cid, "key_1")
    assert doc["key"]["public_key"] == key  # the IC's own answer for this canister, not the realm's word
    assert derived_key(net, other, other.cid, "key_1") != key  # another canister, another key
    if NODE:
        result = page("verifyProof", proof, {"canister": capital.cid, "expectedKey": key})
        assert result["valid"] is True and all(c["status"] == "pass" for c in result["checks"]), result["checks"]


def test_b_a_second_read_and_the_sweeps_after_it_give_the_same_signature_and_cost_nothing(net, capital):
    first = issue(capital)["first_day"]
    before, cycles = proof_of(capital, first), balance(net, capital)
    wait(net, 3 * HOUR)  # three sweeps, and no day has closed
    assert proof_of(capital, first) == before == issue(capital, first)["proof"]
    idle = cycles - balance(net, capital)
    assert idle < FEE // 10  # a sweep that signs nothing costs its instructions, and storage is paid for as ever
    BURN["per_second"] = idle / (3 * HOUR)
    print(f"an idle realm burns {BURN['per_second'] * DAY:,.0f} cycles a day, three sweeps included")


def test_c_each_day_is_signed_as_it_closes_for_one_signature_and_links_to_the_one_before(net, capital):
    first = issue(capital)["first_day"]
    spent = [spent_on_signing(net, capital, DAY) for _ in range(2)]
    days = closed_days(capital)
    proofs = [proof_of(capital, day) for day in days]
    assert days[0] == first and len(proofs) >= 3
    docs = [document(p) for p in proofs]
    assert [d["body"]["number"] for d in docs] == list(range(1, len(docs) + 1))
    assert [d["prev"] for d in docs] == [None] + [p["digest"] for p in proofs[:-1]]
    assert all(reference_valid(p) for p in proofs) and len({p["signature"] for p in proofs}) == len(proofs)
    print(f"cycles spent in a day, one signature: {spent}")
    assert all(abs(s - FEE) < SLACK for s in spent), spent  # one threshold signature a day, and nothing else of cost


def test_d_days_that_closed_while_the_realm_was_stopped_are_signed_in_batches_of_a_month(net, capital):
    last = closed_days(capital)[-1]
    capital.stop()
    wait(net, 40 * DAY)
    capital.start()
    spent = spent_on_signing(net, capital, HOUR)
    new = [d for d in closed_days(capital) if d > last]
    proofs = [proof_of(capital, d) for d in new]
    assert len(new) >= 40
    roots = [p["root"] for p in proofs]
    assert len(set(roots)) == 2 and roots[:31] == [roots[0]] * 31  # a month in one batch, the rest in another
    assert max(len(p["path"]) for p in proofs) == 5 and all(reference_valid(p) for p in proofs)
    # Two signatures for forty days. A stopped realm is charged its storage when it starts again, so the sum is the two
    # fees and, at most, forty days of storage (the roots above say it was two, not forty, signatures).
    assert 2 * FEE - SLACK <= spent <= 2 * FEE + 40 * DAY * BURN["per_second"] + 2 * SLACK, spent
    assert document(proofs[0])["prev"] == proof_of(capital, last)["digest"]
    assert document(proofs[0])["signed_at"] > net.pic.get_time()["nanos_since_epoch"] // 10**9 - 2 * HOUR  # signed late, and says so
    if NODE:
        key = document(proofs[0])["key"]["public_key"]
        for proof in (proofs[0], proofs[15], proofs[30], proofs[31], proofs[-1]):
            assert page("verifyProof", proof, {"canister": capital.cid, "expectedKey": key})["valid"] is True


# ---- An issue altered or dropped shows


def test_e_an_entry_altered_after_the_issue_was_signed_shows_and_so_does_a_changed_text(net, capital):
    shell(capital, RECORD.format(n=2))
    day = issue(capital)["day"]
    wait(net, 2 * DAY)  # the day closes, and so does the one after it
    proof = proof_of(capital, day)
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
        following = proof_of(capital, closed_days(capital)[closed_days(capital).index(day) + 1])
        link = page("checkPrevious", document(following), {"$fetcher": {code(document(following)["prev"]): changed}})
        assert link["status"] == "fail"  # the document it follows is no longer the one it names


def test_f_a_dropped_issue_leaves_a_gap_in_the_chain_that_a_later_issue_shows(net, capital):
    days = closed_days(capital)
    dropped, after = days[3], days[4]
    next_doc = document(proof_of(capital, after))
    assert next_doc["prev"] == proof_of(capital, dropped)["digest"]
    shell(capital, f"from ggg import SignedDocument\nSignedDocument['journal:{dropped}'].delete()")
    assert issue(capital, dropped)["status"] == "unsigned" and "proof" not in issue(capital, dropped)
    out = capital.call("signature_proof", {"code": code(next_doc["prev"])}, query=True)
    assert not out["ok"] and out["kind"] == "not_found"  # the document it follows is no longer served
    wait(net, 2 * HOUR)
    assert issue(capital, dropped)["status"] == "unsigned"  # and the sweeps do not make it again
    assert reference_valid(proof_of(capital, after))  # the later issue is still genuine: it is the gap that shows


# ---- The keys, an upgrade, and what it costs


def test_g_each_network_names_its_own_key_and_an_ic_realm_signs_with_key_1(capital):
    names = {"local": "dfx_test_key", "test": "test_key_1", "staging": "test_key_1", "demo": "test_key_1",
             "ic": "key_1", "": "key_1"}
    for network, name in names.items():
        shell(capital, f"from ggg import Realm\nRealm['1'].network = '{network}'")
        assert shell(capital, "from core import signing\nprint(signing.key_name())").strip() == name
    shell(capital, "from ggg import Realm\nRealm['1'].network = 'ic'")  # nothing is signed meanwhile: no time passed
    assert document(proof_of(capital, closed_days(capital)[-1]))["key"]["name"] == "key_1"


def test_h_an_upgrade_keeps_the_documents_and_the_sweep_goes_on(net, capital):
    days = closed_days(capital)
    kept = [proof_of(capital, d) for d in days[-3:]]
    net.wait(600)
    capital.upgrade()
    assert [proof_of(capital, d) for d in days[-3:]] == kept
    wait(net, DAY)  # the sweep is set again by post_upgrade
    newest = proof_of(capital, closed_days(capital)[-1])
    assert document(newest)["prev"] == kept[-1]["digest"] and reference_valid(newest)


def test_i_what_signing_costs_in_instructions(capital, costs):
    day = closed_days(capital)[-1]
    costs.measure(capital, "journal signing: read a signed issue", f"from core import journal, journal_signing\n"
                  f"journal_signing.shown(journal.issue('{day}'))")
    costs.measure(capital, "journal signing: write one issue as signed text and digest",
                  f"from core import journal, journal_signing, signing\nbody = journal_signing.body(journal.issue('{day}'))\n"
                  "signing.digest_of(signing.canonical({'v': 1, 'kind': 'journal', 'body': body}))")
    costs.measure(capital, "journal signing: the tree and paths of a month of issues",
                  "import hashlib\nfrom core import signing\n"
                  "signing.merkle([hashlib.sha256(bytes([n])).digest() for n in range(31)])")
    costs.measure(capital, "signature_proof: find a proof by its code",
                  f"from core import signing\nsigning.find(signing.proof('journal', '{day}')['code'])")
