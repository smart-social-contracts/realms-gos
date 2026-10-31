"""The shipped role-management codices, driven through the real SDK (issue #265).

These three files are the governance gates deciding who may hold ``admin`` or
``treasurer``. They used to be ``exec()``d in-process with full builtins; they
now run sandboxed and return a plain verdict. Unit-testing the dispatcher with a
fake sandbox proves the host half, but not that the codex sources are actually
correct against the SDK contract — so here we execute the real files the way
``_build_codex_sandbox_source`` does, with ``rpc`` served by the real host
handler, and assert the verdicts each governance model promises.
"""

from pathlib import Path

import pytest

import ggg_sdk
from core import codex_bridge

CODEX_DIR = (
    Path(__file__).resolve().parents[3]
    / "extensions"
    / "extensions"
    / "role_manager"
    / "data"
    / "codexes"
)

MODELS = ("agora", "syntropia", "dominion")

# What core/codex_hooks.py grants these hooks.
ROLE_HOOK_CAPABILITIES = [
    "config.get",
    "realm.get",
    "time.now",
    "user.get",
    "proposal.find_executed",
]


def _load(model, approvals=()):
    """Execute a shipped codex against the real SDK, with *approvals* the set of
    ``(user_id, profile_name, action)`` tuples an executed proposal authorizes.

    Mirrors the subinterpreter: the SDK is the module the codex imports, and
    ``rpc`` is the real host handler, so capability denials are real denials.
    """
    source = (CODEX_DIR / ("role_management_hook_" + model + ".py")).read_text()
    approved = {tuple(a) for a in approvals}

    def _find_executed(target_principal="", profile_name="", change="assign", **kw):
        if (target_principal, profile_name, change) in approved:
            return {"id": "p1", "proposal_type": "role_" + change}
        return None

    handler = codex_bridge.make_rpc_handler(model, ROLE_HOOK_CAPABILITIES)

    def _rpc(action, **kwargs):
        return handler(model, action, kwargs)

    namespace = {"__name__": "codex_" + model}
    exec(compile(source, model + ".py", "exec"), namespace)
    return namespace, _find_executed, _rpc


@pytest.fixture
def codex(monkeypatch):
    def _make(model, approvals=()):
        namespace, find_executed, rpc = _load(model, approvals)
        monkeypatch.setitem(
            codex_bridge.VERBS, "proposal.find_executed", find_executed
        )
        monkeypatch.setattr(ggg_sdk, "rpc", rpc, raising=False)
        return namespace

    return _make


def _call(namespace, hook_name, user_id="u1", profile_name="admin", actor="alice"):
    envelope = namespace[hook_name](
        args={
            "user_id": user_id,
            "profile_name": profile_name,
            "actor_principal": actor,
        },
        context={},
    )
    assert envelope["ok"] is True, envelope.get("error")
    # A role hook decides; it must never propose a write.
    assert envelope["effects"] == []
    return envelope["result"]


# ---------------------------------------------------------------------------
# Contract shared by all three models
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", MODELS)
def test_every_model_implements_the_full_hook_set(codex, model):
    namespace = codex(model)
    for name in (
        "role_assign_prehook",
        "role_revoke_prehook",
        "role_assign_posthook",
        "role_revoke_posthook",
        "get_governance_params",
    ):
        assert callable(namespace.get(name)), name


@pytest.mark.parametrize("model", MODELS)
def test_prehooks_return_a_verdict_rather_than_raising(codex, model):
    namespace = codex(model)
    for name in ("role_assign_prehook", "role_revoke_prehook"):
        verdict = _call(namespace, name)
        assert isinstance(verdict, dict)
        assert isinstance(verdict["allowed"], bool)
        if not verdict["allowed"]:
            assert verdict["reason"]


@pytest.mark.parametrize("model", MODELS)
def test_governance_params_are_plain_numbers(codex, model):
    namespace = codex(model)
    envelope = namespace["get_governance_params"](
        args={"proposal_type": "role_assignment", "requested_permissions": []},
        context={},
    )
    params = envelope["result"]
    assert 0 < params["quorum"] <= 100
    assert 0 < params["threshold"] <= 1
    assert params["notice_hours"] > 0


# ---------------------------------------------------------------------------
# The governance models themselves
# ---------------------------------------------------------------------------


def test_dominion_allows_everything(codex):
    namespace = codex("dominion")
    assert _call(namespace, "role_assign_prehook")["allowed"] is True
    assert _call(namespace, "role_revoke_prehook")["allowed"] is True


def test_agora_lets_routine_roles_through(codex):
    namespace = codex("agora")
    verdict = _call(namespace, "role_assign_prehook", profile_name="member")
    assert verdict["allowed"] is True


def test_agora_blocks_a_sensitive_role_without_a_proposal(codex):
    namespace = codex("agora")
    verdict = _call(namespace, "role_assign_prehook", profile_name="treasurer")
    assert verdict["allowed"] is False
    assert "treasurer" in verdict["reason"]


def test_agora_admits_a_sensitive_role_with_a_proposal(codex):
    namespace = codex("agora", approvals=[("u1", "treasurer", "assign")])
    assert _call(
        namespace, "role_assign_prehook", profile_name="treasurer"
    )["allowed"] is True


def test_agora_does_not_let_an_assignment_proposal_authorize_a_revocation(codex):
    namespace = codex("agora", approvals=[("u1", "admin", "assign")])
    verdict = _call(namespace, "role_revoke_prehook", profile_name="admin")
    assert verdict["allowed"] is False


def test_agora_scopes_approval_to_the_named_user(codex):
    namespace = codex("agora", approvals=[("u2", "admin", "assign")])
    verdict = _call(namespace, "role_assign_prehook", user_id="u1")
    assert verdict["allowed"] is False


def test_syntropia_blocks_even_a_routine_role(codex):
    namespace = codex("syntropia")
    verdict = _call(namespace, "role_assign_prehook", profile_name="member")
    assert verdict["allowed"] is False


def test_syntropia_admits_any_role_that_was_voted_for(codex):
    namespace = codex("syntropia", approvals=[("u1", "member", "assign")])
    assert _call(
        namespace, "role_assign_prehook", profile_name="member"
    )["allowed"] is True


# ---------------------------------------------------------------------------
# The sandbox boundary these codices are subject to
# ---------------------------------------------------------------------------


def test_a_codex_cannot_read_a_verb_it_was_not_granted(monkeypatch):
    """The grant is read-only, so an attempt to write fails inside the hook
    rather than reaching the host."""
    namespace, _, _ = _load("agora")
    handler = codex_bridge.make_rpc_handler("agora", ROLE_HOOK_CAPABILITIES)

    def _rpc(action, **kwargs):
        return handler("agora", action, kwargs)

    monkeypatch.setattr(ggg_sdk, "rpc", _rpc, raising=False)
    with pytest.raises(PermissionError):
        _rpc("user.set", user_id="u1", profile="admin")


def test_role_hook_capabilities_are_all_reads():
    assert set(ROLE_HOOK_CAPABILITIES) <= codex_bridge.READ_VERBS


def test_dispatcher_grants_exactly_these_capabilities():
    from core import codex_hooks

    assert set(codex_hooks.ROLE_HOOK_CAPABILITIES) == set(ROLE_HOOK_CAPABILITIES)
