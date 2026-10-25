"""Host-side sandbox plumbing tests: spawn arguments and context gathering
(issue #265).

The real ``_basilisk_sandbox`` is WASM-only, so these install a fake module in
``sys.modules`` and assert what the host passes to it: the instruction budget
(so codices are metered rather than running unbounded), the capability list and
rpc handler, and the fallback for images whose ``spawn_subinterpreter`` predates
those arguments. The second half covers the per-hook context spec.
"""

import sys

import pytest

from core import runtime_sandbox


class FakeSandbox:
    """Stand-in for ``_basilisk_sandbox``.

    ``extended`` mirrors an image that accepts the capability/budget arguments;
    when False the spawn raises ``TypeError`` for any call passing them, exactly
    as an older two-argument C function would.
    """

    def __init__(self, extended=True, result=None):
        self.extended = extended
        self.result = result if result is not None else {"ok": True}
        self.spawns = []
        self.closed = []
        self.approved = []

    def sha256(self, text):
        return "hash-of-%d-bytes" % len(text)

    def approve_hash(self, h):
        self.approved.append(h)

    def revoke_hash(self, h):
        pass

    def spawn_subinterpreter(self, source, content_hash, *args):
        if args and not self.extended:
            raise TypeError(
                "spawn_subinterpreter() takes 2 positional arguments "
                "but %d were given" % (2 + len(args))
            )
        self.spawns.append(args)
        return 1

    def call_in_subinterpreter(self, handle, fn, kwargs=None):
        return self.result

    def close_subinterpreter(self, handle):
        self.closed.append(handle)


@pytest.fixture
def fake_sandbox(monkeypatch):
    """Install a fake primitive and reset the cached arity probe."""

    def _install(extended=True, result=None):
        sandbox = FakeSandbox(extended=extended, result=result)
        monkeypatch.setitem(sys.modules, "_basilisk_sandbox", sandbox)
        monkeypatch.setattr(runtime_sandbox, "_extended_spawn", None, raising=False)
        return sandbox

    return _install


@pytest.fixture(autouse=True)
def clear_config_cache(monkeypatch):
    """Keep the policy cache from leaking between tests."""
    monkeypatch.setattr(runtime_sandbox, "_config_cache", None, raising=False)


# ---------------------------------------------------------------------------
# Availability probe (is_sandbox_available)
# ---------------------------------------------------------------------------


def test_is_sandbox_available_false_when_module_missing(monkeypatch):
    import builtins

    monkeypatch.delitem(sys.modules, "_basilisk_sandbox", raising=False)
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "_basilisk_sandbox":
            raise ImportError("no sandbox primitive")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert runtime_sandbox.is_sandbox_available() is False


def test_is_sandbox_available_false_without_sha256(monkeypatch):
    class NoSha256:
        pass

    monkeypatch.setitem(sys.modules, "_basilisk_sandbox", NoSha256())
    assert runtime_sandbox.is_sandbox_available() is False


def test_is_sandbox_available_true_with_sha256(fake_sandbox):
    fake_sandbox()
    assert runtime_sandbox.is_sandbox_available() is True


def _budget(patch):
    """Force a specific instruction budget into the policy cache."""
    config = dict(runtime_sandbox.DEFAULT_CONFIG)
    config["budget"] = patch
    runtime_sandbox._config_cache = config


def test_spawn_passes_budget_and_capabilities(fake_sandbox):
    sandbox = fake_sandbox()
    _budget(5_000_000)

    runtime_sandbox._run_in_subinterpreter(
        "src",
        "hook",
        {"args": {}},
        context_id="agora",
        allowed_actions=["user.get"],
        rpc_handler="handler-sentinel",
    )

    context_id, allowed, handler, budget = sandbox.spawns[0]
    assert context_id == "agora"
    assert allowed == ("user.get",)
    assert handler == "handler-sentinel"
    assert budget == 5_000_000


def test_budget_zero_is_passed_through_to_disable_metering(fake_sandbox):
    sandbox = fake_sandbox()
    _budget(0)

    runtime_sandbox._run_in_subinterpreter("src", "hook", {})

    assert sandbox.spawns[0][3] == 0


def test_default_budget_meters_by_default(fake_sandbox):
    sandbox = fake_sandbox()

    runtime_sandbox._run_in_subinterpreter("src", "hook", {})

    assert sandbox.spawns[0][3] == runtime_sandbox.DEFAULT_CONFIG["budget"] > 0


def test_falls_back_to_legacy_two_arg_spawn(fake_sandbox):
    sandbox = fake_sandbox(extended=False)

    result = runtime_sandbox._run_in_subinterpreter(
        "src", "hook", {}, context_id="agora", allowed_actions=["user.get"]
    )

    assert result == {"ok": True}
    assert sandbox.spawns == [()]  # retried without the extended arguments
    assert runtime_sandbox.supports_capabilities() is False


def test_arity_probe_is_cached_across_calls(fake_sandbox):
    sandbox = fake_sandbox(extended=False)

    runtime_sandbox._run_in_subinterpreter("src", "hook", {})
    runtime_sandbox._run_in_subinterpreter("src", "hook", {})

    # Two spawns, and neither retried the extended form after the first probe.
    assert sandbox.spawns == [(), ()]


def test_extended_support_is_recorded(fake_sandbox):
    fake_sandbox(extended=True)

    runtime_sandbox._run_in_subinterpreter("src", "hook", {})

    assert runtime_sandbox.supports_capabilities() is True


def test_type_error_surfaces_once_extended_form_is_known_good(fake_sandbox):
    """A genuine bad-argument TypeError must not be swallowed by the retry."""
    sandbox = fake_sandbox(extended=True)
    runtime_sandbox._run_in_subinterpreter("src", "hook", {})
    assert runtime_sandbox.supports_capabilities() is True

    def _boom(source, content_hash, *args):
        raise TypeError("rpc_handler must be callable")

    sandbox.spawn_subinterpreter = _boom
    with pytest.raises(TypeError):
        runtime_sandbox._run_in_subinterpreter("src", "hook", {})


def test_subinterpreter_is_always_closed(fake_sandbox):
    sandbox = fake_sandbox()

    runtime_sandbox._run_in_subinterpreter("src", "hook", {})

    assert sandbox.closed == [1]


def test_content_hash_uses_sandbox_sha256_when_present(fake_sandbox):
    fake_sandbox()

    assert runtime_sandbox._content_hash("hello") == "hash-of-5-bytes"


# ---------------------------------------------------------------------------
# Per-hook context gathering
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_reads(monkeypatch):
    """Replace the context-producing verbs with cheap stubs."""
    from core import codex_bridge

    monkeypatch.setattr(codex_bridge, "_v_config_get", lambda **k: {"c": 1})
    monkeypatch.setattr(codex_bridge, "_v_currency_get", lambda **k: "DOM")
    monkeypatch.setattr(codex_bridge, "_v_time_now", lambda **k: {"epoch": 7, "ns": 0})
    monkeypatch.setattr(codex_bridge, "_v_realm_get", lambda **k: {"status": "beta"})
    monkeypatch.setattr(
        codex_bridge, "_v_user_get", lambda user_id="", **k: {"id": user_id}
    )
    return codex_bridge


def test_context_gathers_the_default_keys(stub_reads):
    context = runtime_sandbox._gather_hook_context("on_user_register", "")
    assert set(context) == {"config", "currency", "now", "realm", "users"}


def test_get_config_context_omits_config(stub_reads):
    """Gathering ``config`` would re-enter the hook being dispatched."""
    context = runtime_sandbox._gather_hook_context("get_config", "")
    assert "config" not in context
    assert context["currency"] == "DOM"


def test_context_prefetches_the_triggering_user(stub_reads):
    context = runtime_sandbox._gather_hook_context(
        "on_user_register", '{"user_id": "u1"}'
    )
    assert context["users"] == {"u1": {"id": "u1"}}


def test_context_survives_a_failing_read(stub_reads, monkeypatch):
    """A read that cannot be gathered is absent, not fatal; the hook can still
    request it over rpc and get a real error there."""

    def _boom(**kwargs):
        raise RuntimeError("no realm yet")

    monkeypatch.setattr(stub_reads, "_v_realm_get", _boom)

    context = runtime_sandbox._gather_hook_context("on_user_register", "")
    assert "realm" not in context
    assert context["currency"] == "DOM"


def test_malformed_args_do_not_break_gathering(stub_reads):
    context = runtime_sandbox._gather_hook_context("on_user_register", "not-json")
    assert context["users"] == {}
