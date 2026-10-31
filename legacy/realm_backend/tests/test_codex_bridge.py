"""Unit + exploit-regression tests for the codex effect bridge (issue #265).

These exercise the host-side trust checkpoint without a real subinterpreter
(unavailable off-canister) or ``ggg`` (needs a DB): the strict serializer,
capability authorization, effect application + ``$eff`` reference resolution, and
the two exploit regressions the bridge must block (undeclared-capability
escalation, live-object leakage).
"""

import pytest

from core import codex_bridge
from core.codex_bridge import BridgeSerializationError, apply_effects, to_plain


# ---------------------------------------------------------------------------
# Strict plain-data serializer
# ---------------------------------------------------------------------------


def test_serializer_accepts_plain_data():
    value = {
        "a": 1,
        "b": True,
        "c": None,
        "d": 1.5,
        "e": "x",
        "f": [1, "two", {"g": [3]}],
    }
    assert to_plain(value) == value


def test_serializer_converts_tuple_to_list():
    assert to_plain((1, 2, 3)) == [1, 2, 3]


def test_serializer_rejects_custom_object():
    class Entity:
        pass

    with pytest.raises(BridgeSerializationError):
        to_plain(Entity())


def test_serializer_rejects_callable():
    with pytest.raises(BridgeSerializationError):
        to_plain(lambda: 1)


def test_serializer_rejects_exception():
    with pytest.raises(BridgeSerializationError):
        to_plain(ValueError("boom"))


def test_serializer_rejects_set_and_bytes():
    with pytest.raises(BridgeSerializationError):
        to_plain({1, 2, 3})
    with pytest.raises(BridgeSerializationError):
        to_plain(b"bytes")


def test_serializer_rejects_non_string_dict_key():
    with pytest.raises(BridgeSerializationError):
        to_plain({1: "one"})


def test_serializer_rejects_object_nested_in_dict():
    class Entity:
        pass

    with pytest.raises(BridgeSerializationError):
        to_plain({"ok": 1, "bad": Entity()})


def test_serializer_rejects_excessive_depth():
    value = current = {}
    for _ in range(40):
        current["next"] = {}
        current = current["next"]
    with pytest.raises(BridgeSerializationError):
        to_plain(value)


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def test_authorize_allows_declared_registered_verb():
    assert codex_bridge.authorize("user.get", ["user.get"]) is None


def test_authorize_denies_undeclared_verb():
    assert codex_bridge.authorize("user.get", []) is not None


def test_authorize_denies_unknown_verb_even_if_declared():
    # A codex cannot conjure a verb that does not exist by declaring it.
    assert codex_bridge.authorize("treasury.drain", ["treasury.drain"]) is not None


def test_known_verbs_are_registered():
    verbs = set(codex_bridge.known_verbs())
    assert {"config.get", "user.get", "invoice.create", "notification.create"} <= verbs


# ---------------------------------------------------------------------------
# Effect application (with a fake verb registry to avoid ggg/DB)
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_verb(monkeypatch):
    """Register a plain-data verb 'test.echo' for the duration of a test."""
    calls = []

    def _echo(**kwargs):
        calls.append(kwargs)
        return {"echoed": kwargs}

    monkeypatch.setitem(codex_bridge.VERBS, "test.echo", _echo)
    return calls


def _effect(verb, **kwargs):
    return {"verb": verb, "kwargs": kwargs}


def test_apply_effects_dispatches_declared_verb(fake_verb):
    results = apply_effects("codex-x", ["test.echo"], [_effect("test.echo", v=1)])
    assert results == [{"echoed": {"v": 1}}]
    assert fake_verb == [{"v": 1}]


def test_apply_effects_denies_undeclared_verb(fake_verb):
    # Exploit-1 regression: a codex without the capability cannot invoke it.
    with pytest.raises(PermissionError):
        apply_effects("codex-x", [], [_effect("test.echo", v=1)])
    assert fake_verb == []  # verb never ran


def test_apply_effects_denies_unknown_verb():
    with pytest.raises(PermissionError):
        apply_effects("codex-x", ["treasury.drain"], [_effect("treasury.drain")])


def test_apply_effects_rejects_leaked_live_object(monkeypatch):
    # Exploit-3 regression: a verb that returns a live object is refused by the
    # serializer before anything crosses back into the sandbox.
    class Entity:
        pass

    monkeypatch.setitem(codex_bridge.VERBS, "leak.obj", lambda **kw: Entity())
    with pytest.raises(BridgeSerializationError):
        apply_effects("codex-x", ["leak.obj"], [_effect("leak.obj")])


def test_apply_effects_rejects_non_dict_effect():
    with pytest.raises(PermissionError):
        apply_effects("codex-x", ["test.echo"], ["not-an-object"])


def test_apply_effects_rejects_non_list_batch():
    with pytest.raises(PermissionError):
        apply_effects("codex-x", ["test.echo"], {"verb": "test.echo"})


def test_apply_effects_resolves_ref_between_effects(monkeypatch):
    # An earlier effect's result id feeds a later effect (invoice -> notification).
    created = []

    def _make(**kwargs):
        created.append(kwargs)
        return {"id": "inv-42"}

    seen = []

    def _use(**kwargs):
        seen.append(kwargs)
        return {"ok": True}

    monkeypatch.setitem(codex_bridge.VERBS, "make.thing", _make)
    monkeypatch.setitem(codex_bridge.VERBS, "use.thing", _use)

    effects = [
        _effect("make.thing", amount=1),
        # Whole-string ref and embedded ref both resolve against effect #0.
        _effect("use.thing", ref="$eff:0:id", note="linked:$eff:0:id"),
    ]
    results = apply_effects("codex-x", ["make.thing", "use.thing"], effects)
    assert results[0] == {"id": "inv-42"}
    assert seen == [{"ref": "inv-42", "note": "linked:inv-42"}]


def test_resolve_result_substitutes_refs():
    results = [{"id": "inv-7"}]
    resolved = codex_bridge.resolve_result(
        {"success": True, "invoice_id": "$eff:0:id"}, results
    )
    assert resolved == {"success": True, "invoice_id": "inv-7"}


# ---------------------------------------------------------------------------
# Live reads over rpc
# ---------------------------------------------------------------------------


def test_readable_capabilities_keeps_only_reads():
    caps = ["config.get", "user.get", "invoice.create", "member.activate"]
    assert codex_bridge.readable_capabilities(caps) == ["config.get", "user.get"]


def test_rpc_serves_a_declared_read(monkeypatch):
    monkeypatch.setitem(codex_bridge.VERBS, "user.get", lambda **kw: {"id": kw["user_id"]})
    handler = codex_bridge.make_rpc_handler("codex-x", ["user.get"])
    assert handler("codex-x", "user.get", {"user_id": "u1"}) == {"id": "u1"}


def test_rpc_denies_undeclared_read(monkeypatch):
    monkeypatch.setitem(codex_bridge.VERBS, "user.get", lambda **kw: {"id": "u1"})
    handler = codex_bridge.make_rpc_handler("codex-x", [])
    with pytest.raises(PermissionError):
        handler("codex-x", "user.get", {"user_id": "u1"})


def test_rpc_refuses_write_verbs_even_when_declared(fake_verb):
    # Writes must arrive as effects so the host applies them as one authorized
    # batch; a codex cannot mutate mid-computation by routing through rpc.
    handler = codex_bridge.make_rpc_handler("codex-x", ["invoice.create"])
    with pytest.raises(PermissionError):
        handler("codex-x", "invoice.create", {"amount": 1})


def test_rpc_refuses_unknown_verb():
    handler = codex_bridge.make_rpc_handler("codex-x", ["treasury.drain"])
    with pytest.raises(PermissionError):
        handler("codex-x", "treasury.drain", {})


def test_rpc_rejects_leaked_live_object(monkeypatch):
    class Entity:
        pass

    monkeypatch.setitem(codex_bridge.VERBS, "realm.get", lambda **kw: Entity())
    handler = codex_bridge.make_rpc_handler("codex-x", ["realm.get"])
    with pytest.raises(BridgeSerializationError):
        handler("codex-x", "realm.get", {})


def test_rpc_rejects_non_string_action():
    handler = codex_bridge.make_rpc_handler("codex-x", ["user.get"])
    with pytest.raises(PermissionError):
        handler("codex-x", 42, {})


def test_rpc_rejects_non_dict_kwargs():
    handler = codex_bridge.make_rpc_handler("codex-x", ["user.get"])
    with pytest.raises(PermissionError):
        handler("codex-x", "user.get", ["not", "an", "object"])


# ---------------------------------------------------------------------------
# proposal.find_executed
# ---------------------------------------------------------------------------
#
# The verb the role-management hooks depend on. It exists so a codex can ask
# "is this specific role change approved?" instead of being handed every
# proposal in the realm to filter itself.


class _FakeProposal:
    def __init__(self, id, status, metadata):
        self.id = id
        self.status = status
        self.metadata = metadata


def _fake_proposals(monkeypatch, proposals):
    import sys
    import types

    module = types.ModuleType("ggg")
    module.Proposal = type(
        "Proposal", (), {"instances": staticmethod(lambda: list(proposals))}
    )
    monkeypatch.setitem(sys.modules, "ggg", module)


def _meta(**kw):
    import json

    return json.dumps(kw)


def test_find_executed_matches_an_approved_assignment(monkeypatch):
    _fake_proposals(monkeypatch, [
        _FakeProposal("p1", "executed", _meta(
            proposal_type="role_assignment",
            target_principal="u1",
            profile_name="admin",
        )),
    ])
    found = codex_bridge.VERBS["proposal.find_executed"](
        target_principal="u1", profile_name="admin", change="assign"
    )
    assert found["id"] == "p1"


def test_find_executed_ignores_proposals_that_did_not_execute(monkeypatch):
    _fake_proposals(monkeypatch, [
        _FakeProposal("p1", "open", _meta(
            proposal_type="role_assignment",
            target_principal="u1",
            profile_name="admin",
        )),
    ])
    assert codex_bridge.VERBS["proposal.find_executed"](
        target_principal="u1", profile_name="admin"
    ) is None


def test_find_executed_does_not_match_another_user(monkeypatch):
    _fake_proposals(monkeypatch, [
        _FakeProposal("p1", "executed", _meta(
            proposal_type="role_assignment",
            target_principal="u2",
            profile_name="admin",
        )),
    ])
    assert codex_bridge.VERBS["proposal.find_executed"](
        target_principal="u1", profile_name="admin"
    ) is None


def test_find_executed_does_not_match_another_profile(monkeypatch):
    _fake_proposals(monkeypatch, [
        _FakeProposal("p1", "executed", _meta(
            proposal_type="role_assignment",
            target_principal="u1",
            profile_name="observer",
        )),
    ])
    assert codex_bridge.VERBS["proposal.find_executed"](
        target_principal="u1", profile_name="admin"
    ) is None


def test_an_assignment_does_not_authorize_a_revocation(monkeypatch):
    _fake_proposals(monkeypatch, [
        _FakeProposal("p1", "executed", _meta(
            proposal_type="role_assignment",
            target_principal="u1",
            profile_name="admin",
        )),
    ])
    assert codex_bridge.VERBS["proposal.find_executed"](
        target_principal="u1", profile_name="admin", change="revoke"
    ) is None


def test_find_executed_honors_the_legacy_revocation_encoding(monkeypatch):
    # Proposals voted before role_revocation existed encoded revocation as a
    # role_assignment for "revoke_<profile>"; they must keep working.
    _fake_proposals(monkeypatch, [
        _FakeProposal("p1", "executed", _meta(
            proposal_type="role_assignment",
            target_principal="u1",
            profile_name="revoke_admin",
        )),
    ])
    found = codex_bridge.VERBS["proposal.find_executed"](
        target_principal="u1", profile_name="admin", change="revoke"
    )
    assert found["id"] == "p1"


def test_find_executed_skips_unparseable_metadata(monkeypatch):
    _fake_proposals(monkeypatch, [
        _FakeProposal("p1", "executed", "{not json"),
        _FakeProposal("p2", "executed", _meta(
            proposal_type="role_assignment",
            target_principal="u1",
            profile_name="admin",
        )),
    ])
    found = codex_bridge.VERBS["proposal.find_executed"](
        target_principal="u1", profile_name="admin"
    )
    assert found["id"] == "p2"


def test_find_executed_requires_both_a_target_and_a_profile(monkeypatch):
    _fake_proposals(monkeypatch, [])
    verb = codex_bridge.VERBS["proposal.find_executed"]
    assert verb(target_principal="", profile_name="admin") is None
    assert verb(target_principal="u1", profile_name="") is None


def test_find_executed_returns_plain_data(monkeypatch):
    _fake_proposals(monkeypatch, [
        _FakeProposal("p1", "executed", _meta(
            proposal_type="role_assignment",
            target_principal="u1",
            profile_name="admin",
        )),
    ])
    handler = codex_bridge.make_rpc_handler("codex-x", ["proposal.find_executed"])
    result = handler("codex-x", "proposal.find_executed", {
        "target_principal": "u1", "profile_name": "admin",
    })
    assert result == {
        "id": "p1", "proposal_type": "role_assignment", "profile_name": "admin",
    }


def test_find_executed_is_reachable_over_rpc():
    # Role hooks read approvals mid-decision, so this has to be served live
    # rather than collected as a post-hoc effect.
    assert "proposal.find_executed" in codex_bridge.READ_VERBS
    assert codex_bridge.readable_capabilities(["proposal.find_executed"]) == [
        "proposal.find_executed"
    ]


def test_no_read_verb_shadows_the_rpc_action_parameter():
    """``rpc(action, **kwargs)`` spends the name ``action`` on the verb, so a
    verb kwarg of the same name arrives as a duplicate argument — a collision
    that only shows up at call time, in the sandbox."""
    import inspect

    for name in codex_bridge.READ_VERBS:
        params = inspect.signature(codex_bridge.VERBS[name]).parameters
        assert "action" not in params, name


def test_find_executed_is_denied_without_the_capability():
    handler = codex_bridge.make_rpc_handler("codex-x", ["user.get"])
    with pytest.raises(PermissionError):
        handler("codex-x", "proposal.find_executed", {
            "target_principal": "u1", "profile_name": "admin",
        })


# ---------------------------------------------------------------------------
# currency.get
# ---------------------------------------------------------------------------


def test_currency_get_prefers_config_pin(monkeypatch):
    monkeypatch.setattr(
        "core.codex_hooks.get_config",
        lambda: {"currency": {"symbol": "DOM"}},
    )
    monkeypatch.setattr("core.realm_currency.realm_currency", lambda: "ckUSDC")
    assert codex_bridge._v_currency_get() == "DOM"


def test_currency_get_uses_realm_when_config_unpinned(monkeypatch):
    monkeypatch.setattr("core.codex_hooks.get_config", lambda: {})
    monkeypatch.setattr("core.realm_currency.realm_currency", lambda: "ckUSDC")
    assert codex_bridge._v_currency_get() == "ckUSDC"


def test_currency_get_returns_empty_when_unresolved(monkeypatch):
    monkeypatch.setattr("core.codex_hooks.get_config", lambda: {})
    monkeypatch.setattr("core.realm_currency.realm_currency", lambda: "")
    assert codex_bridge._v_currency_get() == ""
