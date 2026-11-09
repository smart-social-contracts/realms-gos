"""This release's entities against the schemas of the realms in the field (tests/fixtures/schema/*.json).

Every upgrade of a realm stores the entity schema it ran with, and the next upgrade is rejected in ``post_upgrade`` if it
adds a field without a default to an entity that has no ``migrate()``, changes a field's type, or changes a
relationship's. A realm installed fresh stores none, so the upgrade tests that start from a previous wasm never meet the
check; a realm that has been upgraded before does. This makes the same comparison, with ic-python-db's own check and no
replica: it loads a stored schema into a database that has the entities of ``ggg`` registered, and asks the database
whether they may replace it.

It runs in a process of its own. The registry of entity types in this process also holds the stand-ins other tests define
under the names of the realm's entities (a ``Codex`` and a ``Token``, among them), so it is not what a realm registers.

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
SNAPSHOTS = sorted((pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "schema").glob("*.json"))
CHECK = """
import json, sys
sys.path[:0] = [sys.argv[1]]
from ic_python_db import Database

Database.init()
import ggg  # registers the entities

db = Database.get_instance()
db.save("_system", "_schema", json.load(open(sys.argv[2])))
db.check_upgrade_compatibility()  # raises SchemaIncompatibleError, naming every breaking change without a migrate()
"""


def test_there_is_a_schema_to_check_against():
    assert SNAPSHOTS, "tests/fixtures/schema holds no schema"


@pytest.mark.parametrize("snapshot", SNAPSHOTS, ids=lambda path: path.stem)
def test_this_release_may_replace_the_schema_a_realm_stores(snapshot):
    json.loads(snapshot.read_text())  # a schema, not a truncated copy
    done = subprocess.run([sys.executable, "-c", CHECK, str(SRC), str(snapshot)], capture_output=True, text=True)
    assert done.returncode == 0, "the upgrade would be rejected:\n" + done.stderr[-1500:]
