import glob
import importlib
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "..", "src", "realm_backend")
sys.path[:0] = [HERE, SRC]
sys.path.append(os.path.join(HERE, ".."))  # tests/fixtures, last: its names must not shadow the realm's own

import core  # noqa: E402
import fake_chain  # noqa: E402

sys.modules["core.chain"] = core.chain = fake_chain

import cedarpy  # noqa: E402
from ic_python_db import Database  # noqa: E402

Database.init()

from core import auth  # noqa: E402
from core.db import Fail  # noqa: E402


def load(schema, policies):
    """The canister's Cedar load: strict validation of the policies against the schema."""
    check = cedarpy.validate_policies(policies, schema)
    if not check.validation_passed:
        raise Fail(f"the Cedar policies do not validate: {check.errors}")


def decide(principal, action, resource, entities, context):
    """The canister's decision, over the policies and schema in force (``auth.live``), the request validated against it."""
    request = {"principal": principal, "action": action, "resource": resource, "context": context}
    result = cedarpy.is_authorized(request, auth.live["policies"], entities, auth.live["schema"])
    assert not result.diagnostics.errors, result.diagnostics.errors
    return result.decision == cedarpy.Decision.Allow


load(auth.live["schema"], auth.live["policies"])
auth.load, auth.decide = load, decide
for path in sorted(glob.glob(os.path.join(SRC, "*", "*.py"))):
    package, name = path.split(os.sep)[-2], os.path.basename(path)[:-3]
    if package in ("core", "api") and name not in ("__init__", "chain"):
        importlib.import_module(f"{package}.{name}")


import ggg_api  # noqa: E402
import kit  # noqa: E402
from api import assets, quarters  # noqa: E402
import mirror  # noqa: E402

ggg_api.Principal = mirror.FakePrincipal
MIRROR = os.environ.get("GGG_MIRROR")
if MIRROR:
    kit.MIRROR = mirror.call


@pytest.fixture(autouse=True)
def fresh():
    Database.get_instance().clear()
    ggg_api.STAFF.clear()
    fake_chain.reset()
    quarters._synced.update(since_start=False, at=0)  # what a heap of a canister knows of its syncs, which a test starts without
    quarters._scale["check"] = True
    assets.census.update(held=None, generation=1, taken=0)
    fake_chain.state["mirror"] = MIRROR
    yield


def pytest_sessionfinish(session):
    if MIRROR:
        mirror.dump(os.path.join(HERE, "..", "..", ".ggg-mirror.txt"))
