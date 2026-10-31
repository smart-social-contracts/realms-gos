"""Minimal tests for ``call_extension_function`` error handling."""

import pytest

from core import extensions, runtime_sandbox


def test_sandbox_infrastructure_attribute_error_is_not_swallowed(monkeypatch):
    monkeypatch.setattr(runtime_sandbox, "should_sandbox", lambda ext_id: True)
    monkeypatch.setattr(runtime_sandbox, "is_sandbox_available", lambda: True)
    monkeypatch.setattr(
        runtime_sandbox, "is_async_extension_function", lambda *a, **k: False
    )
    monkeypatch.setattr(extensions, "_has_backend", lambda ext_id: True)

    def _boom(*args, **kwargs):
        raise AttributeError(
            "module '_basilisk_sandbox' has no attribute 'sha256'"
        )

    monkeypatch.setattr(runtime_sandbox, "call_extension_in_sandbox", _boom)
    monkeypatch.setattr(
        "core.runtime_extensions.resolve_extension_id", lambda name: name
    )

    with pytest.raises(AttributeError, match="sha256"):
        extensions.call_extension_function("my_ext", "is_admin", "{}")


def test_missing_extension_hook_still_returns_none(monkeypatch):
    monkeypatch.setattr(runtime_sandbox, "should_sandbox", lambda ext_id: False)

    def _missing(name, function_name):
        raise AttributeError(
            f"Extension '{name}' has no function '{function_name}'"
        )

    monkeypatch.setattr("core.runtime_extensions.get_func", _missing)
    monkeypatch.setattr(
        "core.runtime_extensions.resolve_extension_id", lambda name: name
    )

    assert extensions.call_extension_function("my_ext", "optional_hook", "{}") is None
