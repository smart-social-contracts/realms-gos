"""Tests for sandboxed proposal execution (issue #265)."""

import pytest

from core import codex_bridge
from core.proposal_execution import (
    compute_code_checksum,
    normalize_proposal_permissions,
    verify_code_checksum,
    wrap_proposal_code,
)


def test_compute_and_verify_checksum():
    code = "def main():\n    return {'ok': True}\n"
    checksum = compute_code_checksum(code)
    assert checksum.startswith("sha256:")
    assert verify_code_checksum(code, checksum) is None


def test_verify_checksum_fail_closed_when_missing():
    assert verify_code_checksum("code", "") == "checksum required but missing"


def test_verify_checksum_mismatch():
    err = verify_code_checksum("a", compute_code_checksum("b"))
    assert err and "mismatch" in err


def test_normalize_proposal_permissions_filters_unknown():
    perms = normalize_proposal_permissions(
        ["member.assign_profile", "treasury.drain", "not-a-verb", 3]
    )
    assert perms == ["member.assign_profile"]


def test_wrap_proposal_code_adds_main_and_adapter():
    wrapped = wrap_proposal_code("x = 1")
    assert "def main():" in wrapped
    assert "__proposal_main__" in wrapped


def test_apply_effects_defers_treasury_transfer():
    effects = [{"verb": "treasury.transfer", "kwargs": {
        "to_principal": "abc", "amount": 5,
    }}]
    results, deferred = codex_bridge.apply_effects(
        "proposal:1", ["treasury.transfer"], effects, defer_async=True
    )
    assert results == [None]
    assert deferred == [{
        "verb": "treasury.transfer",
        "kwargs": {"to_principal": "abc", "amount": 5},
    }]


def test_apply_effects_rejects_async_without_defer():
    effects = [{"verb": "treasury.transfer", "kwargs": {"amount": 1}}]
    with pytest.raises(PermissionError, match="asynchronously"):
        codex_bridge.apply_effects(
            "codex-x", ["treasury.transfer"], effects, defer_async=False
        )


def test_known_verbs_includes_async_effects():
    assert "treasury.transfer" in codex_bridge.known_verbs()
