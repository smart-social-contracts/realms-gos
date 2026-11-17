import hashlib
import os
import shutil
import sys

import pytest
from ic.principal import Principal

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..")
sys.path[:0] = [HERE, os.path.join(ROOT, "scripts")]
sys.path.append(os.path.join(ROOT, "tests"))  # tests/fixtures

from costs import Costs  # noqa: E402
from pack_realm_backend import pack  # noqa: E402
from pic import CONTROLLER, Canister  # noqa: E402

COSTS = Costs()


def principal(name):
    """A stable self-authenticating principal per test persona."""
    return Principal.self_authenticating(hashlib.sha256(name.encode()).digest()).to_str()


@pytest.fixture(scope="session")
def wasm_of(tmp_path_factory):
    """The realm wasm of a build variant, packed once. Packing rewrites .basilisk in place, so each is copied aside."""
    built = {}

    def get(variant):
        if variant not in built:
            built[variant] = shutil.copy(pack(variant=variant), tmp_path_factory.mktemp(variant) / "realm_backend.wasm")
        return built[variant]

    return get


@pytest.fixture(scope="session")
def realm(wasm_of):
    return Canister(wasm_of("production"), installer=principal("installer"))


@pytest.fixture(scope="session")
def people():
    return {n: principal(n) for n in ("founder", "alice", "bob", "claude-conn", "mallory", "installer")} | {"ctl": CONTROLLER}


@pytest.fixture(scope="session")
def costs():
    """Where tests report the instructions something cost; the session ends with the table of them."""
    return COSTS


def pytest_terminal_summary(terminalreporter):
    if COSTS.samples:
        terminalreporter.section("instruction costs reported by tests")
        for line in COSTS.table():
            terminalreporter.write_line(line)
