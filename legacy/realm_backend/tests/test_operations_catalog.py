"""OPERATIONS_CATALOG must cover every Operations constant exactly."""

from __future__ import annotations

import inspect

from ggg.system.user_profile import OPERATIONS_CATALOG, Operations


def _operation_values() -> set[str]:
    values = set()
    for _name, value in inspect.getmembers(Operations):
        if _name.startswith("_"):
            continue
        if isinstance(value, str):
            values.add(value)
    return values


def test_operations_catalog_covers_every_operation():
    ops = _operation_values()
    assert ops, "expected at least one operation constant"

    missing = sorted(ops - set(OPERATIONS_CATALOG))
    assert not missing, f"missing catalog entries: {missing}"

    for op in ops:
        entry = OPERATIONS_CATALOG[op]
        assert entry.get("category"), f"{op}: category must be non-empty"
        assert entry.get("description"), f"{op}: description must be non-empty"


def test_operations_catalog_has_no_unknown_keys():
    ops = _operation_values()
    extra = sorted(set(OPERATIONS_CATALOG) - ops)
    assert not extra, f"unknown catalog keys: {extra}"
