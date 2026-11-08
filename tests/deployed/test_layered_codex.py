"""The storage of a layered codex on the real wasm (docs/codices/layered-codex.md §9.1, realms-gos#486): a legacy codex
keeps working across the upgrade that adds the new entities, and a layered codex with its chapter texts survives an
upgrade. Nothing installs a layered codex yet, so the rows are written through the controller's ``__shell__``.
"""

import hashlib
import json
import os
import pathlib

import pytest
from pic import Canister

CODICES = pathlib.Path(__file__).resolve().parents[2] / "codices" / "codices"
# The realm wasm of feat/layered-codex before this change, packed from that commit: the test is skipped without it.
PREVIOUS_WASM = os.environ.get("REALM_PREVIOUS_WASM")

LAYERS = ["eu/es", "eu/es/vc"]
PATHS = {"tax": ["eu/es", "eu/es/vc"]}
SMALL = "class Tax:\n    holiday = 'Año Nuevo'\n"  # not ASCII: the checksum is of the UTF-8 bytes
RATE_LIMIT = 600  # seconds the replica wants between install_code messages as dear as installing this wasm
BIG = "x" * (256 * 1024)  # the most a chapter may be, and more than the database's declared value size


def ok(realm, op, who, query=False, **args):
    out = realm.call(op, args, sender=who, query=query)
    assert out["ok"], (op, out)
    return out


def sha256(text):
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def codex_of(name, version=None):
    folder = CODICES / name
    manifest = json.loads((folder / "manifest.json").read_text())
    return {"manifest": {**manifest, "version": version or manifest["version"]},
            "source": (folder / "backend" / "hooks.py").read_text()}


def new_realm(wasm, people):
    """A realm in setup, whose founder can install a codex directly."""
    realm = Canister(wasm, installer=people["installer"])
    founder = people["founder"]
    ok(realm, "setup_step", people["installer"], step="install", network="local", founder=founder)
    ok(realm, "setup_step", founder, step="realm", name="Agora", open_registration=True)
    ok(realm, "setup_step", founder, step="trust_policy", require_marketplace_approval=False)
    ok(realm, "setup_step", founder, step="tokens", accounting_currency="DOM", accounting_currency_decimals=8)
    return realm


def run(realm, code):
    out = realm.shell(code)
    assert "Traceback" not in out, out
    return out


@pytest.mark.skipif(not PREVIOUS_WASM, reason="REALM_PREVIOUS_WASM names no wasm")
def test_a_legacy_codex_stays_active_and_installable_across_the_upgrade(wasm_of, people):
    founder = people["founder"]
    realm = new_realm(PREVIOUS_WASM, people)
    installed = ok(realm, "codex_install", founder, **codex_of("dominion"))["codex"]
    before = ok(realm, "codex_status", founder, query=True)

    realm.wait(RATE_LIMIT)
    realm.upgrade(wasm_of("production"))

    after = ok(realm, "codex_status", founder, query=True)
    assert after["codex"] == before["codex"]
    assert (after["codex"]["id"], after["codex"]["status"]) == (installed, "active")
    assert after["hooks"] == before["hooks"] and after["history"] == before["history"]
    assert (after["pending"], after["job"]) == (None, None)
    newer = ok(realm, "codex_install", founder, **codex_of("dominion", "99.0.0"))["codex"]
    status = ok(realm, "codex_status", founder, query=True)["codex"]
    assert (status["id"], status["previous"]) == (newer, installed)
    assert ok(realm, "codex_revert", people["ctl"])["codex"] == installed
    assert ok(realm, "codex_status", founder, query=True)["codex"]["id"] == installed


WRITE = f'''
import json
import ggg
from core import layered_codex as store
from core.db import Conflict, Fail

big = "x" * store.MAX_CHAPTER
small = {SMALL!r}
counted = {{}}


def counting(name, fn, *args):
    before = ic.performance_counter(0)
    out = fn(*args)
    counted[name] = ic.performance_counter(0) - before
    return out


counting("store_small", store.store_chapter, store.checksum(small), small)
counting("store_256_kb", store.store_chapter, store.checksum(big), big)
chapters = {{
    "eu/es/tax": {{"source": "eu/es", "version": "2.4.0", "sha256": store.checksum(small), "vouched_by": "eu/es",
                  "route": "capital", "fetched_at": 1000, "url": "", "size": len(small.encode())}},
    "eu/es/vc/tax": {{"source": "eu/es/vc", "version": "1.1.0", "sha256": store.checksum(big), "vouched_by": "eu/es/vc",
                     "route": "https", "fetched_at": 1001, "url": "https://example.org/tax.py", "size": len(big)}},
}}
layers, paths = {LAYERS!r}, {PATHS!r}
codex_id = counting("codex_id", store.codex_id, layers, paths, chapters)
ggg.LayeredCodex(id=codex_id, layers=layers, layer_paths=paths, chapters=chapters, status="active",
                 installed_at=500, proposal="P-1")
refusals = []
for text, key in ((big + "x", store.checksum(big + "x")), (small, store.checksum(big))):
    try:
        store.store_chapter(key, text)
    except Fail as e:
        refusals.append(str(e))
job = store.start_job("P-1", {{"url": "https://example.org/change.json", "checksum": "sha256:" + "1" * 64}})
try:
    store.start_job("P-2", {{}})
except Conflict as e:
    refusals.append(str(e))
print(json.dumps({{"id": codex_id, "counted": counted, "refusals": refusals, "job": job.id}}))
'''

READ = '''
import json
import ggg
print(json.dumps({"sources": {row.sha256: len(row.source) for row in ggg.ChapterSource.load_some(1, 10)},
                  "count": ggg.ChapterSource.count(), "codices": ggg.LayeredCodex.count(),
                  "jobs": ggg.InstallJob.count()}))
'''


@pytest.fixture(scope="module")
def written(wasm_of, people):
    """A realm with a layered codex, two chapter texts (one of 256 KB) and an install job, written in the canister."""
    realm = new_realm(wasm_of("production"), people)
    realm.written = json.loads(run(realm, WRITE))
    return realm


def test_the_canister_computes_the_id_and_the_checksums_as_the_design_says(written):
    sums = {"eu/es/tax": sha256(SMALL), "eu/es/vc/tax": sha256(BIG)}
    canonical = json.dumps({"chapters": sums, "layer_paths": PATHS, "layers": LAYERS}, sort_keys=True,
                           separators=(",", ":"), ensure_ascii=False)
    assert written.written["id"] == sha256(canonical)


def test_the_canister_refuses_what_the_rules_refuse(written):
    assert written.written["refusals"][0].endswith("is above 256 KB")
    assert "not sha256:" in written.written["refusals"][1]
    assert written.written["refusals"][2] == "an install is running" and written.written["job"] == "J-1"


def test_the_checks_cost_little_next_to_a_message(written):
    # Printed for the install task (D2): hashing 256 KB is the dearest of them. An update may use 40 billion.
    counted = written.written["counted"]
    print(counted)
    assert max(counted.values()) < 2_000_000_000


def test_a_layered_codex_and_its_chapter_texts_survive_an_upgrade(written, people):
    founder = people["founder"]
    before = ok(written, "codex_status", founder, query=True)
    assert before["codex"]["id"] == written.written["id"] and before["codex"]["status"] == "active"

    written.wait(RATE_LIMIT)
    written.upgrade()

    after = ok(written, "codex_status", founder, query=True)
    assert after["codex"] == before["codex"] and after["job"] == before["job"]
    assert after["codex"]["layer_paths"] == PATHS and after["codex"]["chapters"]["eu/es/tax"]["route"] == "capital"
    assert after["job"]["id"] == "J-1" and after["job"]["status"] == "running"
    stored = json.loads(run(written, READ))
    assert stored == {"sources": {sha256(SMALL): len(SMALL), sha256(BIG): len(BIG)}, "count": 2, "codices": 1,
                      "jobs": 1}


def test_a_chapter_stored_again_after_the_upgrade_is_not_stored_twice(written):
    out = run(written, "from core import layered_codex as store\n"
                       "import ggg\n"
                       f"store.store_chapter(store.checksum({SMALL!r}), {SMALL!r})\n"
                       "print(ggg.ChapterSource.count())")
    assert out.strip() == "2"


def test_the_export_pages_a_256_kb_chapter_alone(written, people):
    """Ten of them pass a reply's 2 MB and seven a query's instruction limit: the realm returns one row a page."""
    seen, start = [], 1
    while start is not None:
        out = ok(written, "realm_export", people["ctl"], query=True, entity="ChapterSource", count=500,
                 **{"from": start})
        assert len(out["rows"]) == 1
        seen += [row["sha256"] for row in out["rows"]]
        start = out["next"]
    assert seen == [sha256(SMALL), sha256(BIG)]
