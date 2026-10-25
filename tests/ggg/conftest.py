import glob
import importlib
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "..", "src", "realm_backend")
sys.path[:0] = [HERE, SRC]

import fake_chain  # noqa: E402

sys.modules["chain"] = fake_chain

import cedarpy  # noqa: E402
from ic_python_db import Database  # noqa: E402

Database.init()

import auth  # noqa: E402

POLICIES = auth.GUARDRAILS + auth.POLICIES
_check = cedarpy.validate_policies(POLICIES, auth.SCHEMA)
assert _check.validation_passed, _check.errors


def decide(principal, action, resource, entities, context):
    request = {"principal": principal, "action": action, "resource": resource, "context": context}
    return cedarpy.is_authorized(request, POLICIES, entities).decision == cedarpy.Decision.Allow


auth.decide = decide
for path in sorted(glob.glob(os.path.join(SRC, "*.py"))):
    name = os.path.basename(path)[:-3]
    if name not in ("main", "chain"):
        importlib.import_module(name)


@pytest.fixture(autouse=True)
def fresh():
    Database.get_instance().clear()
    fake_chain.reset()
    yield
