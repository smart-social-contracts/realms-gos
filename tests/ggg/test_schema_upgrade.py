"""This release's entities against the schemas of the realms in the field (tests/fixtures/schema/*.json).

Every upgrade of a realm stores the entity schema it ran with, and the next upgrade is rejected in ``post_upgrade`` if it
adds a field without a default to an entity that has no ``migrate()``, changes a field's type, or changes a
relationship's. A realm installed fresh stores none, so the upgrade tests that start from a previous wasm never meet the
check; a realm that has been upgraded before does. This makes the same comparison, with ic-python-db's own check and no
replica: it loads a stored schema into a database that has the realm's entities registered, and asks the database
whether they may replace it.

It runs in a process of its own, and registers what the canister registers, not what CPython does. The registry of entity
types in the test process also holds the stand-ins other tests define under the names of the realm's entities (a ``Codex``
and a ``Token``, among them). In a process of its own, importing ``ggg`` also imports ``ic_basilisk_toolkit.crypto``, which
the canister never does: it registers the toolkit's ``KeyEnvelope`` (3 fields) over the realm's (6), and a ``CryptoGroup``
and a ``CryptoGroupMember`` the canister does not have. A default-less field added to the realm's ``KeyEnvelope`` would
pass a check made against the toolkit's and trap a real upgrade. ``test_the_registry_is_what_the_canister_registers``
holds the registry to the schema of the current wasm (tests/fixtures/current_schema.json, which
tests/deployed/test_schema_snapshot.py holds to the wasm in turn), so any other such difference fails there.

A failure names each change and its entity. Give the new field a default, or the entity a ``migrate()`` (it may return
the row as it is when a missing value already means what the field is for).

The snapshots are what the realms store now; tests/fixtures/schema/README.md says how to take one.
"""

import json
import pathlib
import subprocess
import sys

import pytest

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "realm_backend"
FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures"
SNAPSHOTS = sorted((FIXTURES / "schema").glob("*.json"))
CURRENT = FIXTURES / "current_schema.json"
REGISTRY = """
import json, sys
sys.path[:0] = [sys.argv[1]]
from ic_python_db import Database
from ic_python_db.schema import build_schema

Database.init()
import ggg  # registers the entities

db = Database.get_instance()
# What the canister registers: the realm's KeyEnvelope, not the toolkit's of the same name, and none of the toolkit's
# crypto groups (ic_basilisk_toolkit.crypto is imported here by ggg, and never in the canister).
db.register_entity_type(ggg.KeyEnvelope, ggg.KeyEnvelope.get_full_type_name())
for name in ("CryptoGroup", "CryptoGroupMember"):
    db._entity_types.pop(name, None)

if len(sys.argv) > 2:
    db.save("_system", "_schema", json.load(open(sys.argv[2])))
    db.check_upgrade_compatibility()  # raises SchemaIncompatibleError, naming every breaking change without a migrate()
else:
    print(json.dumps(build_schema(db._entity_types), sort_keys=True))
"""


def in_a_process_of_its_own(*args):
    return subprocess.run([sys.executable, "-c", REGISTRY, str(SRC), *args], capture_output=True, text=True)


def comparable(schema):
    """The schema with a Json field's default as a value: it is written as text, with spaces under CPython and without in
    the canister (basilisk's json), which is no difference between the entities."""
    schema = json.loads(json.dumps(schema))
    for entity in schema.values():
        for field in entity["fields"].values():
            if field.get("type") == "Json" and isinstance(field.get("default"), str):
                field["default"] = json.loads(field["default"])
    return schema


def test_there_is_a_schema_to_check_against():
    assert SNAPSHOTS, "tests/fixtures/schema holds no schema"


@pytest.mark.parametrize("snapshot", SNAPSHOTS, ids=lambda path: path.stem)
def test_this_release_may_replace_the_schema_a_realm_stores(snapshot):
    json.loads(snapshot.read_text())  # a schema, not a truncated copy
    done = in_a_process_of_its_own(str(snapshot))
    assert done.returncode == 0, "the upgrade would be rejected:\n" + done.stderr[-1500:]


def test_the_registry_is_what_the_canister_registers():
    done = in_a_process_of_its_own()
    assert done.returncode == 0, done.stderr[-1500:]
    registered, canister = comparable(json.loads(done.stdout)), comparable(json.loads(CURRENT.read_text()))
    differing = {name: sorted(registered[name]["fields"] if name in registered else []) for name in registered
                 if name not in canister or registered[name] != canister[name]}
    missing = sorted(set(canister) - set(registered))
    assert not differing and not missing, (
        "the entities registered here are not the schema of the current wasm (tests/fixtures/current_schema.json). "
        f"Registered here and different or absent there: {sorted(differing)}; there and not here: {missing}. "
        "An entity was changed: take the schema again (tests/fixtures/schema/README.md). Otherwise the canister registers "
        "something this process does not: say so in REGISTRY above.")
