"""Host-side typed proposal dispatcher (issue #305)."""

import json

import pytest

from core.proposal_dispatch import (
    DispatchError,
    freeze_action,
    reject_forbidden_submit_keys,
    submit_gate,
    uses_timelock,
)
from core.proposal_execution import compute_code_checksum


class FakeProposal:
    def __init__(self, **kwargs):
        self.proposal_id = kwargs.get("proposal_id", "prop_001")
        self.status = kwargs.get("status", "executing")
        self.code_checksum = kwargs.get("code_checksum", "")
        self.metadata = kwargs.get("metadata", "{}")


def test_reject_old_submit_fields():
    err = reject_forbidden_submit_keys({"code_inline": "x = 1"})
    assert err["error_code"] == "forbidden_field"
    assert reject_forbidden_submit_keys({"title": "x"}) is None


def test_transaction_amount_must_be_decimal_string():
    _, _, err = freeze_action(
        "transaction",
        {"token": "ICP", "to_principal": "aaaaa-aa", "amount": 100},
    )
    assert err["error_code"] == "invalid_amount"
    action, perms, err = freeze_action(
        "transaction",
        {"token": "ICP", "to_principal": "aaaaa-aa", "amount": "9007199254740993"},
    )
    assert err is None
    assert perms == []
    assert action["amount"] == "9007199254740993"


def test_transaction_rejects_zero_and_leading_zeros():
    _, _, err = freeze_action(
        "transaction",
        {"token": "ICP", "to_principal": "aaaaa-aa", "amount": "0"},
    )
    assert err["error_code"] == "invalid_amount"
    _, _, err = freeze_action(
        "transaction",
        {"token": "ICP", "to_principal": "aaaaa-aa", "amount": "01"},
    )
    assert err["error_code"] == "invalid_amount"


def test_upgrade_voting_refused():
    _, _, err = freeze_action(
        "upgrade",
        {"target": "extension", "package_id": "voting", "version": "1.4.0",
         "registry_canister_id": "aaaaa-aa"},
    )
    assert err["error_code"] == "self_upgrade_unsupported"


def test_upgrade_latest_refused():
    _, _, err = freeze_action(
        "upgrade",
        {"target": "codex", "package_id": "agora", "version": "latest",
         "registry_canister_id": "aaaaa-aa"},
    )
    assert err["error_code"] == "version_not_pinned"


def test_permissions_only_on_code_execution():
    _, _, err = freeze_action(
        "poll",
        {},
        requested_permissions=["treasury.transfer"],
    )
    assert err["error_code"] == "permissions_not_allowed"


def test_code_execution_name_is_derived(monkeypatch):
    action, perms, err = freeze_action(
        "code_execution",
        {},
        source="def main():\n    return {'success': True}\n",
        requested_permissions=["treasury.transfer", "role.assign", "not-a-verb"],
        proposal_id="prop_007",
    )
    assert err is None
    assert action["codex_name"] == "proposal_prop_007"
    assert perms == ["treasury.transfer"]


def test_submit_gates():
    assert submit_gate("poll", {}) == "proposal.create"
    assert submit_gate("code_execution", {}) == "proposal.create"
    assert submit_gate("transaction", {}) == "transfer.create"
    assert submit_gate("upgrade", {"target": "codex"}) == "codex.install"
    assert submit_gate("upgrade", {"target": "codex_revert"}) == "codex.revert"
    assert submit_gate("upgrade", {"target": "extension"}) == "extension.install"
    assert submit_gate("upgrade", {"target": "core"}) == "orchestration.approve"


def test_upgrade_codex_revert_needs_no_package():
    action, perms, err = freeze_action("upgrade", {"target": "codex_revert"})
    assert err is None
    assert perms == []
    assert action == {"target": "codex_revert"}


def test_timelock_only_transaction_and_core():
    assert uses_timelock("transaction", {}) is True
    assert uses_timelock("upgrade", {"target": "core"}) is True
    assert uses_timelock("upgrade", {"target": "extension"}) is False
    assert uses_timelock("poll", {}) is False
    assert uses_timelock("code_execution", {}) is False


def test_dispatch_refuses_non_executing():
    from core.proposal_dispatch import dispatch_proposal

    p = FakeProposal(status="accepted", metadata=json.dumps({
        "proposal_type": "poll", "action": {},
    }))
    with pytest.raises(DispatchError) as ei:
        gen = dispatch_proposal(p)
        if hasattr(gen, "__next__"):
            list(gen)
    assert ei.value.error_code == "not_executing"


def test_dispatch_poll_sets_executed():
    from core.proposal_dispatch import dispatch_proposal

    p = FakeProposal(status="executing", metadata=json.dumps({
        "proposal_type": "poll", "action": {},
    }))
    gen = dispatch_proposal(p)
    try:
        next(gen)
    except StopIteration:
        pass
    assert p.status == "executed"


def test_dispatch_unknown_type_fails():
    from core.proposal_dispatch import dispatch_proposal

    p = FakeProposal(status="executing", metadata=json.dumps({
        "proposal_type": "treasury_action", "action": {},
    }))
    gen = dispatch_proposal(p)
    try:
        next(gen)
    except StopIteration:
        pass
    assert p.status == "failed"
    assert json.loads(p.metadata)["error_code"] == "unknown_proposal_type"


def test_dispatch_transaction_calls_vault(monkeypatch):
    from core import proposal_dispatch as pd

    called = {}

    def fake_call(ext, fn, args):
        called["ext"] = ext
        called["fn"] = fn
        called["args"] = json.loads(args)
        return {"success": True, "ok": 1}

    monkeypatch.setattr(pd, "extension_async_call", fake_call, raising=False)

    # Patch the import inside dispatch
    import core.extensions as ext_mod
    monkeypatch.setattr(ext_mod, "extension_async_call", fake_call)

    p = FakeProposal(status="executing", metadata=json.dumps({
        "proposal_type": "transaction",
        "action": {
            "token": "ICP",
            "to_principal": "aaaaa-aa",
            "amount": "9007199254740993",
        },
    }))
    gen = pd.dispatch_proposal(p)
    yielded = next(gen)
    assert yielded == {"success": True, "ok": 1}
    try:
        gen.send(yielded)
    except StopIteration:
        pass
    assert p.status == "executed"
    assert called["args"]["amount"] == 9007199254740993
    assert called["args"]["token"] == "ICP"


def test_dispatch_transaction_vault_error(monkeypatch):
    from core import proposal_dispatch as pd
    import core.extensions as ext_mod

    def fake_call(ext, fn, args):
        return {"success": False, "error": "Insufficient funds", "error_code": "insufficient_funds"}

    monkeypatch.setattr(ext_mod, "extension_async_call", fake_call)

    p = FakeProposal(status="executing", metadata=json.dumps({
        "proposal_type": "transaction",
        "action": {"token": "ICP", "to_principal": "aaaaa-aa", "amount": "1"},
    }))
    gen = pd.dispatch_proposal(p)
    yielded = next(gen)
    try:
        gen.send(yielded)
    except StopIteration:
        pass
    assert p.status == "failed"
    meta = json.loads(p.metadata)
    assert meta["error_code"] == "insufficient_funds"


def test_dispatch_code_execution_requires_success(monkeypatch):
    from core import proposal_dispatch as pd
    from core import proposal_execution as pe

    class Row:
        code = "def main():\n    return {'success': False, 'error': 'nope'}\n"

    monkeypatch.setattr(pd, "Codex", {"proposal_prop_001": Row()}, raising=False)

    def fake_codex_lookup(name):
        return Row() if name == "proposal_prop_001" else None

    # ggg.Codex[name] style
    class CodexMap:
        def __getitem__(self, name):
            return fake_codex_lookup(name)

    import ggg
    monkeypatch.setattr(ggg, "Codex", CodexMap(), raising=False)

    def fake_exec(pid, code, perms):
        if False:
            yield None
        return {"success": False, "error": "nope"}

    monkeypatch.setattr(pe, "execute_proposal_code", fake_exec)

    checksum = compute_code_checksum(Row.code)
    p = FakeProposal(
        status="executing",
        code_checksum=checksum,
        metadata=json.dumps({
            "proposal_type": "code_execution",
            "action": {"codex_name": "proposal_prop_001"},
            "requested_permissions": [],
        }),
    )
    gen = pd.dispatch_proposal(p)
    try:
        next(gen)
    except StopIteration:
        pass
    assert p.status == "failed"
    assert json.loads(p.metadata)["error_code"] == "sandbox_unsuccessful"
