import hashlib
import os
import sys

import pytest
from ic.principal import Principal

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..")
sys.path[:0] = [HERE, os.path.join(ROOT, "scripts")]

from pack_realm_backend import pack  # noqa: E402
from pic import CONTROLLER, Canister  # noqa: E402


def principal(name):
    """A stable self-authenticating principal per test persona."""
    return Principal.self_authenticating(hashlib.sha256(name.encode()).digest()).to_str()


@pytest.fixture(scope="session")
def realm():
    return Canister(str(pack()))


@pytest.fixture(scope="session")
def people():
    return {n: principal(n) for n in ("founder", "alice", "bob", "claude-conn", "mallory")} | {"ctl": CONTROLLER}
