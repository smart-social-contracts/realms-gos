"""Power of attorney: grant windows, non-member delegates, scope limits, paged lookups."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src" / "realm_backend"))

from _cdk_stub import ensure_cdk_stub  # noqa: E402

ensure_cdk_stub()

import core.delegation as dl  # noqa: E402
from ggg import Delegation, User, UserProfile  # noqa: E402
from ggg.system.user_profile import Operations  # noqa: E402
from ic_python_db import Database  # noqa: E402

NOW = 1_800_000_000
ALICE = "alice-principal"
BOB = "bob-principal"
AI = "ai-connection-principal"
VOTE = {"operations": [Operations.PROPOSAL_VOTE]}


@pytest.fixture(autouse=True)
def realm(monkeypatch):
    Database.get_instance().clear()
    monkeypatch.setattr(sys.modules["_cdk"], "Principal", type("P", (), {"from_str": staticmethod(str)}))
    monkeypatch.setattr(dl, "_now_ts", lambda: NOW)
    monkeypatch.setattr(dl, "_notify_delegate_pending", lambda d: None)
    caller = {"id": ALICE}
    monkeypatch.setattr(dl.ic, "caller", lambda: type("P", (), {"to_str": lambda s: caller["id"]})())
    member = UserProfile(name="member", allowed_to=",".join([Operations.PROPOSAL_VOTE, Operations.PROPOSAL_CREATE]))
    User(id=ALICE, profiles=[member])
    User(id=BOB, profiles=[member])
    yield caller


def grant(delegate=AI, scope=VOTE, **kw):
    kw.setdefault("expires_at", NOW + 3600)
    return dl.grant_delegation(ALICE, delegate, scope, **kw)


def test_non_member_delegate_is_active_without_acceptance():
    res = grant(label="My Claude", description="Votes while I travel")
    assert res["success"], res
    d = res["data"]
    assert d["status"] == "active" and d["label"] == "My Claude"
    assert d["description"] == "Votes while I travel"
    assert dl.find_active_delegation(ALICE, AI) is not None


def test_member_delegate_must_accept():
    assert grant(delegate=BOB)["data"]["status"] == "pending"
    assert dl.find_active_delegation(ALICE, BOB) is None


def test_future_start_is_not_active_until_it_begins(monkeypatch):
    assert grant(starts_at=NOW + 600, expires_at=NOW + 3600)["success"]
    assert dl.find_active_delegation(ALICE, AI) is None
    monkeypatch.setattr(dl, "_now_ts", lambda: NOW + 601)
    assert dl.find_active_delegation(ALICE, AI) is not None


def test_expired_grant_stops_working(monkeypatch):
    assert grant()["success"]
    monkeypatch.setattr(dl, "_now_ts", lambda: NOW + 3601)
    assert dl.find_active_delegation(ALICE, AI) is None


@pytest.mark.parametrize(
    "kw, error",
    [
        ({"expires_at": NOW - 1}, "after the start"),
        ({"starts_at": NOW + 100, "expires_at": NOW + 50}, "after the start"),
        ({"expires_at": NOW + 400 * 24 * 3600}, "one year"),
    ],
)
def test_rejects_bad_windows(kw, error):
    res = grant(**kw)
    assert not res["success"] and error in res["error"]


def test_only_delegable_operations_the_grantor_holds():
    res = grant(scope={"operations": [Operations.REALM_ADMIN]})
    assert not res["success"] and "cannot be delegated" in res["error"]
    res = grant(scope={"operations": [Operations.TRANSFER_CREATE]})
    assert not res["success"] and "lacks permission" in res["error"]


def test_one_open_grant_per_pair_until_revoked(realm):
    first = grant()["data"]
    assert "revoke it first" in grant()["error"]
    assert dl.revoke_delegation(first["id"])["success"]
    assert grant()["success"]


def test_listing_is_paged(realm):
    for i in range(5):
        assert grant(delegate=f"ai-{i}")["success"]
    page = dl.list_delegations_for_caller(count=2)["data"]
    assert len(page["as_grantor"]) == 2 and page["next_grantor_cursor"]
    seen = [r["delegate"] for r in page["as_grantor"]]
    cursor = page["next_grantor_cursor"]
    while cursor:
        page = dl.list_delegations_for_caller(grantor_cursor=cursor, delegate_cursor=0, count=2)["data"]
        seen += [r["delegate"] for r in page["as_grantor"]]
        cursor = page["next_grantor_cursor"]
    assert seen == [f"ai-{i}" for i in range(5)]


def test_delegable_operations_lists_what_the_caller_holds():
    ops = {o["operation"] for o in dl.delegable_operations_for(ALICE)}
    assert ops == {Operations.PROPOSAL_VOTE, Operations.PROPOSAL_CREATE}
    assert dl.delegable_operations_for("stranger") == []


def test_extension_gate_admits_delegate_through_grant(realm):
    import json

    from core.extension_access import _delegation_allows

    assert grant()["success"]
    realm["id"] = AI
    args = json.dumps({"proposal_id": "p1", "vote": "yes", "on_behalf_of": ALICE})
    assert _delegation_allows(args, AI, Operations.PROPOSAL_VOTE)
    assert not _delegation_allows(args, AI, Operations.PROPOSAL_CREATE)
    assert not _delegation_allows(json.dumps({"on_behalf_of": BOB}), AI, Operations.PROPOSAL_VOTE)
    assert not _delegation_allows(json.dumps({"vote": "yes"}), AI, Operations.PROPOSAL_VOTE)


def test_delegate_may_call_extensions_for_grantor(realm, monkeypatch):
    import json

    import core.access
    from core.extension_access import delegate_may_call

    monkeypatch.setattr(core.access, "_check_access", lambda who, op: who == ALICE)
    assert grant()["success"]
    op = Operations.EXTENSION_SYNC_CALL
    assert delegate_may_call(json.dumps({"on_behalf_of": ALICE}), AI, op)
    assert not delegate_may_call(json.dumps({"on_behalf_of": BOB}), AI, op)
    assert not delegate_may_call(json.dumps({"vote": "yes"}), AI, op)
    assert not delegate_may_call(json.dumps({"on_behalf_of": ALICE}), BOB, op)


def test_delegate_may_call_extensions_for_grantor(realm):
    import json

    from core.extension_access import delegate_may_call

    on_behalf = json.dumps({"on_behalf_of": ALICE})
    assert not delegate_may_call(on_behalf, AI, Operations.EXTENSION_SYNC_CALL)
    assert grant()["success"]
    assert delegate_may_call(on_behalf, AI, Operations.EXTENSION_SYNC_CALL)
    assert not delegate_may_call(on_behalf, BOB, Operations.EXTENSION_SYNC_CALL)
    assert not delegate_may_call(json.dumps({}), AI, Operations.EXTENSION_SYNC_CALL)


def test_acting_context_resolves_grantor(realm):
    assert grant()["success"]
    realm["id"] = AI
    ctx = dl.resolve_acting_context({"on_behalf_of": ALICE}, Operations.PROPOSAL_VOTE)
    assert ctx.is_delegated and ctx.subject == ALICE and ctx.actor == AI
    with pytest.raises(dl.AccessDenied):
        dl.resolve_acting_context({"on_behalf_of": ALICE}, Operations.PROPOSAL_CREATE)


def ask(realm, scope=None, **kw):
    realm["id"] = AI
    kw.setdefault("expires_at", NOW + 7 * 24 * 3600)
    scope = scope or {"operations": [Operations.PROPOSAL_VOTE, Operations.PROPOSAL_CREATE]}
    res = dl.request_delegation(ALICE, scope, label="Claude", **kw)
    realm["id"] = ALICE
    return res


def test_request_is_signed_by_the_delegate_and_visible_to_the_grantor(realm):
    res = ask(realm)
    assert res["success"], res
    req = res["data"]
    assert req["delegate"] == AI and req["grantor"] == ALICE and req["label"] == "Claude"
    seen = dl.get_delegation_request(req["id"])["data"]
    assert [(p["operation"], p["grantor_holds"]) for p in seen["permissions"]] == [
        (Operations.PROPOSAL_VOTE, True),
        (Operations.PROPOSAL_CREATE, True),
    ]
    assert seen["permissions"][0]["label"] == "Vote on proposals"
    realm["id"] = BOB
    assert not dl.get_delegation_request(req["id"])["success"]


def test_approve_can_narrow_but_not_widen(realm):
    rid = ask(realm)["data"]["id"]
    assert "Not asked for" in dl.approve_delegation_request(rid, [Operations.EXTENSION_SYNC_CALL])["error"]
    assert "earlier" in dl.approve_delegation_request(rid, None, NOW + 8 * 24 * 3600)["error"]
    res = dl.approve_delegation_request(rid, [Operations.PROPOSAL_VOTE], NOW + 3600)
    assert res["success"], res
    d = dl.find_active_delegation(ALICE, AI)
    assert d is not None and int(d.expires_at) == NOW + 3600
    assert dl.scope_allows(dl._parse_scope(d.scope_json), Operations.PROPOSAL_VOTE)
    assert not dl.scope_allows(dl._parse_scope(d.scope_json), Operations.PROPOSAL_CREATE)
    assert not dl.get_delegation_request(rid)["success"]


def test_only_the_grantor_approves(realm):
    rid = ask(realm)["data"]["id"]
    realm["id"] = AI
    assert not dl.approve_delegation_request(rid)["success"]
    assert dl.find_active_delegation(ALICE, AI) is None


def test_approve_replaces_an_existing_grant(realm):
    assert grant()["success"]
    rid = ask(realm)["data"]["id"]
    assert dl.approve_delegation_request(rid)["success"]
    d = dl.find_active_delegation(ALICE, AI)
    assert dl.scope_allows(dl._parse_scope(d.scope_json), Operations.PROPOSAL_CREATE)


def test_decline_and_expiry_remove_the_request(realm, monkeypatch):
    rid = ask(realm)["data"]["id"]
    assert dl.decline_delegation_request(rid)["success"]
    assert not dl.get_delegation_request(rid)["success"]
    rid = ask(realm)["data"]["id"]
    monkeypatch.setattr(dl, "_now_ts", lambda: NOW + dl.REQUEST_TTL_SECONDS + 1)
    assert not dl.approve_delegation_request(rid)["success"]
    assert dl.DelegationRequest.count_by("grantor", ALICE) == 0


def test_request_rules(realm, monkeypatch):
    assert "not a member" in dl.request_delegation("nobody", VOTE)["error"]
    assert "cannot be delegated" in ask(realm, {"operations": ["realm.admin"]})["error"]
    assert "list the permissions" in ask(realm, {"all": True})["error"]
    realm["id"] = dl.ANONYMOUS_PRINCIPAL
    assert not dl.request_delegation(ALICE, VOTE, expires_at=NOW + 60)["success"]


def test_new_request_supersedes_and_grantor_cap_holds(realm, monkeypatch):
    first = ask(realm)["data"]["id"]
    second = ask(realm)["data"]["id"]
    assert dl.DelegationRequest.count_by("grantor", ALICE) == 1
    assert dl.DelegationRequest[first] is None and dl.DelegationRequest[second] is not None
    for i in range(dl.MAX_OPEN_REQUESTS_PER_GRANTOR - 1):
        realm["id"] = f"ai-{i}"
        assert dl.request_delegation(ALICE, VOTE, expires_at=NOW + 60)["success"]
    realm["id"] = "ai-late"
    assert "too many" in dl.request_delegation(ALICE, VOTE, expires_at=NOW + 60)["error"]
