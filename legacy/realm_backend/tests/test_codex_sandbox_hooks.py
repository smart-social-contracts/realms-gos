"""End-to-end (logic) tests for the migrated agora/syntropia sandbox hooks
(issue #265), effects model.

For each codex we reproduce the exact sandbox path off-canister:

  1. build the combined source (SDK loader + the codex's real
     ``sandbox_hooks.py``) and ``exec`` it, as the subinterpreter would;
  2. build the plain-data ``context`` the host would inject from the read-fakes
     (config/currency/time/realm/user);
  3. run the hook to get its effects envelope; and
  4. feed those effects through the *real* host ``apply_effects`` — granted
     exactly the capabilities declared in the codex manifest, and backed by
     write-fakes — so a hook that emits an effect it did not declare fails here,
     catching capability drift.

The real subinterpreter (``_basilisk_sandbox``) is WASM-only; this validates the
hook logic + effect/capability wiring, which is what the codices depend on.
"""

import json
import os
import sys

import pytest

from core import codex_bridge, runtime_sandbox

_REALM_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CODICES = os.path.join(_REALM_BACKEND, "..", "..", "codices", "codices")

# Verbs that supply *reads* (gathered into the host context) vs *writes*
# (applied as effects). Mirrors runtime_sandbox._gather_hook_context.
_READ_VERBS = {"config.get", "currency.get", "time.now", "user.get", "realm.get"}


def _manifest(codex):
    with open(os.path.join(_CODICES, codex, "manifest.json")) as f:
        return json.load(f)


def _sandbox_source(codex):
    manifest = _manifest(codex)
    module = manifest["sandbox_module"]
    with open(os.path.join(_CODICES, codex, "backend", module)) as f:
        return f.read()


def _build_context(args, fakes):
    """Reproduce runtime_sandbox._gather_hook_context from the read-fakes."""
    context = {
        "config": fakes["config.get"](),
        "currency": fakes["currency.get"](),
        "now": fakes["time.now"](),
        "realm": fakes.get("realm.get", lambda **k: None)(),
        "users": {},
    }
    uid = args.get("user_id")
    if uid:
        context["users"][uid] = fakes["user.get"](user_id=uid)
    return context


def _run_hook(codex, hook_name, args, fake_verbs):
    """Run ``codex``'s sandboxed ``hook_name`` exactly as the host would: build
    the injected context from the read-fakes, execute the hook to collect its
    effects, then apply them via the real ``apply_effects`` with the manifest's
    declared capabilities and the write-fakes backing the registry.

    Returns (resolved_result, applied_write_calls)."""
    capabilities = _manifest(codex)["capabilities"]
    source = runtime_sandbox._build_codex_sandbox_source(_sandbox_source(codex))
    context = _build_context(args, fake_verbs)

    # Install write-fakes into the verb registry (reads are served from context).
    calls = []
    saved = {}
    for name, fn in fake_verbs.items():
        if name in _READ_VERBS:
            continue
        saved[name] = codex_bridge.VERBS.get(name)

        def _wrap(fn, action):
            def _v(**kwargs):
                calls.append((action, kwargs))
                return fn(**kwargs)
            return _v

        codex_bridge.VERBS[name] = _wrap(fn, name)

    saved_sdk = sys.modules.get("ggg_sdk")
    try:
        g = {}
        exec(compile(source, f"{codex}/sandbox_hooks.py", "exec"), g)
        envelope = g[hook_name](args=json.dumps(args), context=context)
        if not envelope.get("ok"):
            return {"success": False, "error": envelope.get("error")}, calls
        results = codex_bridge.apply_effects(
            codex, capabilities, envelope.get("effects") or []
        )
        result = codex_bridge.resolve_result(envelope.get("result"), results)
        return (result if result is not None else {"success": True}), calls
    finally:
        for name, prev in saved.items():
            if prev is None:
                codex_bridge.VERBS.pop(name, None)
            else:
                codex_bridge.VERBS[name] = prev
        # The SDK loader clobbers sys.modules['ggg_sdk'] with the sandbox copy;
        # restore the host module so other tests import the real one.
        if saved_sdk is not None:
            sys.modules["ggg_sdk"] = saved_sdk
        else:
            sys.modules.pop("ggg_sdk", None)


def _verbs_by_action(calls):
    seen = {}
    for action, kwargs in calls:
        seen.setdefault(action, []).append(kwargs)
    return seen


# ---------------------------------------------------------------------------
# Syntropia — greenfield deposit invoice
# ---------------------------------------------------------------------------


def test_syntropia_on_user_register_creates_deposit_invoice():
    config = {
        "fees": {"deposit": 0.05},
        "lifecycle": {"deposit_label": "a house in a zone"},
        "membership": {"invoice_validity_days": 30},
    }
    fakes = {
        "config.get": lambda **k: config,
        "currency.get": lambda **k: "ckBTC",
        "time.now": lambda **k: {"epoch": 0, "ns": 0},
        "user.get": lambda user_id="", **k: {"id": user_id, "name": "Citizen"},
        "invoice.create": lambda **k: {"id": "inv-1"},
        "notification.create": lambda **k: {"id": "ntf-1"},
    }
    result, calls = _run_hook(
        "syntropia", "on_user_register", {"user_id": "u1"}, fakes
    )
    assert result == {"success": True, "invoice_id": "inv-1"}

    by = _verbs_by_action(calls)
    assert by["invoice.create"][0]["metadata"] == "deposit invoice - a house in a zone"
    assert by["invoice.create"][0]["amount"] == 0.05
    assert by["invoice.create"][0]["currency"] == "ckBTC"
    assert by["invoice.create"][0]["user_id"] == "u1"
    assert by["notification.create"][0]["topic"] == "welcome"
    assert by["notification.create"][0]["metadata"] == "invoice_id:inv-1"


def test_syntropia_unknown_user_short_circuits():
    fakes = {
        "config.get": lambda **k: {},
        "currency.get": lambda **k: "ckBTC",
        "time.now": lambda **k: {"epoch": 0, "ns": 0},
        "user.get": lambda user_id="", **k: None,
        "invoice.create": lambda **k: {"id": "x"},
        "notification.create": lambda **k: {"id": "x"},
    }
    result, calls = _run_hook(
        "syntropia", "on_user_register", {"user_id": "ghost"}, fakes
    )
    assert result == {"success": False, "error": "user not found"}
    assert "invoice.create" not in _verbs_by_action(calls)


# ---------------------------------------------------------------------------
# Agora — incumbent migration (activation + phase-aware invoicing)
# ---------------------------------------------------------------------------


def _agora_fakes(stage, fee):
    config = {
        "fees": {"registration": fee},
        "membership": {"invoice_validity_days": 30},
    }
    return {
        "config.get": lambda **k: config,
        "currency.get": lambda **k: "REALMS",
        "time.now": lambda **k: {"epoch": 0, "ns": 0},
        "user.get": lambda user_id="", **k: {"id": user_id, "name": "Resident"},
        "realm.get": lambda **k: {"status": stage},
        "member.activate": lambda **k: {"accepted": True, "member_id": "m1"},
        "invoice.create": lambda **k: {"id": "inv-9"},
        "notification.create": lambda **k: {"id": "ntf-9"},
    }


def test_agora_beta_issues_registration_invoice_and_activates():
    result, calls = _run_hook(
        "agora", "on_user_register", {"user_id": "u2"},
        _agora_fakes(stage="beta", fee=2.0),
    )
    assert result == {"success": True, "invoice_id": "inv-9"}
    by = _verbs_by_action(calls)
    # Migration activation always happens...
    assert by["member.activate"][0]["user_id"] == "u2"
    assert by["member.activate"][0]["identity_verification"] == "verified"
    # ...and beta with a fee issues a registration invoice.
    assert by["invoice.create"][0]["metadata"] == "registration invoice"
    assert by["invoice.create"][0]["amount"] == 2.0
    assert by["notification.create"][0]["icon"] == "wallet"


def test_agora_alpha_activates_but_issues_no_invoice():
    result, calls = _run_hook(
        "agora", "on_user_register", {"user_id": "u3"},
        _agora_fakes(stage="alpha", fee=2.0),
    )
    assert result == {"success": True, "stage": "alpha"}
    by = _verbs_by_action(calls)
    assert "member.activate" in by
    assert "invoice.create" not in by  # no payment during alpha
    assert by["notification.create"][0]["icon"] == "information_circle"


def test_agora_capabilities_cover_every_verb_used():
    # If a hook calls a verb absent from the manifest capabilities, the handler
    # raises PermissionError; a green run above already proves coverage, but
    # assert the declared set matches the registry names too.
    declared = set(_manifest("agora")["capabilities"])
    assert declared <= set(codex_bridge.known_verbs())
    declared_s = set(_manifest("syntropia")["capabilities"])
    assert declared_s <= set(codex_bridge.known_verbs())
