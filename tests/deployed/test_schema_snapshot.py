"""The schema the current wasm stores is tests/fixtures/current_schema.json.

tests/ggg/test_schema_upgrade.py checks this release's entities against the schemas of the realms in the field, in
CPython. A realm registers its entities in a canister, which differs from CPython (the toolkit's crypto entities are
imported in one and not in the other), so that test holds its registry to this file; this test holds the file to the wasm.
An upgrade stores the schema it ran with, so the canister is upgraded over itself before the schema is read.

When an entity changes, take the schema again: tests/fixtures/schema/README.md.
"""

import json
import pathlib

from pic import Canister

CURRENT = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "current_schema.json"
RATE_LIMIT = 600  # seconds the replica wants between install_code messages as dear as installing this wasm
STORED = "import json\nfrom ic_python_db import Database\nprint(json.dumps(Database.get_instance().load('_system', '_schema')))"


def test_the_wasm_stores_the_schema_the_fast_test_trusts(wasm_of, people):
    realm = Canister(wasm_of("production"), installer=people["installer"])
    realm.wait(RATE_LIMIT)
    realm.upgrade()  # an installed canister has stored no schema yet
    stored, expected = json.loads(realm.shell(STORED)), json.loads(CURRENT.read_text())
    differing = sorted(name for name in set(stored) | set(expected) if stored.get(name) != expected.get(name))
    assert not differing, (
        f"tests/fixtures/current_schema.json is not the schema this wasm stores; it differs in {differing}. "
        "Take it again (tests/fixtures/schema/README.md).")
