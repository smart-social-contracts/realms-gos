"""A ``core`` upgrade proposal succeeds only if the Baton accepted the approval."""

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src" / "realm_backend"))
sys.modules.setdefault("_cdk", MagicMock())

from core import proposal_dispatch as pd  # noqa: E402


def _candid_text(payload: dict) -> str:
    return "(" + json.dumps(json.dumps(payload)) + ")"


@pytest.mark.parametrize(
    "decoded",
    [
        _candid_text({"ok": True, "status": "pending"}),
        json.dumps({"ok": True, "status": "pending"}),
        [json.dumps({"ok": True, "status": "pending"})],
        {"ok": True, "status": "pending"},
    ],
)
def test_parse_baton_reply_shapes(decoded):
    assert pd.parse_baton_reply(decoded) == {"ok": True, "status": "pending"}


def test_parse_baton_reply_candid_escaped_quote():
    text = '("{\\"ok\\": false, \\"error\\": \\"caller isn\\\'t eligible\\"}")'
    assert pd.parse_baton_reply(text) == {"ok": False, "error": "caller isn't eligible"}


def _run_approve(monkeypatch, decoded_reply):
    calls = []
    ic = MagicMock()
    ic.call_raw.side_effect = lambda *a: calls.append(a) or "pending-call"
    ic.candid_encode.side_effect = lambda s: s.encode()
    ic.candid_decode.side_effect = lambda b: decoded_reply
    cdk = types.ModuleType("_cdk")
    cdk.ic = ic
    cdk.CallResult = object
    cdk.Principal = MagicMock()
    monkeypatch.setitem(sys.modules, "_cdk", cdk)

    realm = types.SimpleNamespace(
        manifest_data=json.dumps({"casals": {"baton_canister_id": "zzlag-qiaaa-aaaas-qhaba-cai"}})
    )
    ggg = types.ModuleType("ggg")
    ggg.Realm = types.SimpleNamespace(load=lambda _id: realm)
    monkeypatch.setitem(sys.modules, "ggg", ggg)

    gen = pd._approve_orchestration({"action_id": "act-1", "decision": "approve"})
    assert next(gen) == "pending-call"
    with pytest.raises(StopIteration) as stop:
        gen.send({"Ok": b"\x00"})
    assert calls[0][1] == "submit_approval"
    return json.loads(stop.value.value)


def test_baton_acceptance_is_success(monkeypatch):
    out = _run_approve(monkeypatch, _candid_text({"ok": True, "status": "approved"}))
    assert out["success"] is True
    assert out["reply"]["status"] == "approved"
    assert pd._json_ok(json.dumps(out))[0] is True


def test_baton_refusal_fails_the_proposal(monkeypatch):
    out = _run_approve(monkeypatch, _candid_text({"ok": False, "error": "unknown action"}))
    ok, err, code = pd._json_ok(json.dumps(out))
    assert ok is False
    assert code == "orchestration_refused"
    assert "unknown action" in err
