import importlib.util
import json
from pathlib import Path

import pytest

import fake_chain
import world
from api import setup
from ggg import Department, Realm, Registry
from kit import call, denied, ok

APPROVED = json.dumps({"approved": True, "approver": "market"})


def test_story_the_founder_sets_up_and_launches_agora():
    world.build()
    ok("setup_step", "ctl", step="install", network="ic", founder="frank")
    ok("setup_step", "frank", step="realm", name="Agora", open_registration=True)
    ok("setup_step", "frank", step="branding", primary_color="#1E6B52")
    ok("setup_step", "frank", step="tokens", accounting_currency="ckUSDC")
    state = ok("setup_state", "frank")
    assert state["founder"] == "frank" and state["done"] == ["branding", "install", "realm", "tokens"]
    assert state["todo"] == ["infrastructure", "trust_policy"] and state["realm"]["primary_color"] == "#1E6B52"
    assert ok("setup_launch", "frank")["status"] == "alpha"
    assert Realm["1"].status == "alpha" and ok("status", fake_chain.ANONYMOUS)["name"] == "Agora"
    denied("realm_configure", "frank", open_registration=False)
    assert "governed" in call("setup_step", "ctl", step="branding", logo_url="x")["error"]
    assert "already launched" in call("setup_launch", "ctl")["error"]


def test_the_installer_named_at_init_keeps_admin_rights_without_being_a_controller():
    world.build()
    setup.start("gaas")
    setup.start("other")
    assert Realm["1"].installer_canister_id == "gaas"
    ok("setup_step", "gaas", step="install", network="ic", founder="frank")
    ok("setup_step", "gaas", step="realm", name="Agora")
    denied("setup_step", "other", step="branding", primary_color="#1E6B52")


@pytest.fixture
def test_build(monkeypatch):
    """The realm as built by ``pack_realm_backend.py --variant test``; the checked-in variant is production."""
    source = Path(__file__).parents[2] / "scripts" / "build_variants" / "test.py"
    spec = importlib.util.spec_from_file_location("test_build_variant", source)
    variant = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(variant)
    monkeypatch.setattr(setup, "build_variant", variant)


@pytest.mark.parametrize("network", ["", "prod", "production", "ic", "ic-1", "staging"])
def test_I_ADM_1_an_unset_or_unknown_network_is_production(test_build, network):
    world.build()
    Realm["1"].network = network
    assert "test flags" in call("realm_configure", "ctl", test_flags={"fast_clock": True})["error"]


def test_I_ADM_1_a_production_build_refuses_test_flags_on_any_network():
    world.build()
    for network, can_test_mode in (("local", False), ("test", True), ("", True), ("ic", True)):
        Realm["1"].network, Realm["1"].can_test_mode = network, can_test_mode
        assert "test flags" in call("realm_configure", "ctl", test_mode=True)["error"]
        assert "test flags" in call("realm_configure", "ctl", test_flags={"skip_terms": True})["error"]
    assert Realm["1"].test_flags == {} and not Realm["1"].test_mode


def test_I_ADM_1_test_flags_only_off_production_or_when_allowed_at_install(test_build):
    world.build()
    ok("setup_step", "ctl", step="install", network="ic")
    assert "test flags" in call("realm_configure", "ctl", test_mode=True)["error"]
    assert "test flags" in call("realm_configure", "ctl", test_flags={"fast_clock": True})["error"]
    ok("realm_configure", "ctl", test_flags={"fast_clock": False})
    Realm["1"].can_test_mode = True
    ok("realm_configure", "ctl", test_mode=True)
    Realm["1"].can_test_mode, Realm["1"].network = False, "local"
    ok("realm_configure", "ctl", test_flags={"fast_clock": True})
    assert Realm["1"].test_flags == {"fast_clock": True}


def test_I_ADM_2_founder_configures_during_setup_then_it_is_governed():
    world.build()
    world.policy(Department["Administration"], 2, 3)
    denied("setup_step", "alice", step="realm", name="Mine")
    ok("setup_step", "ctl", step="install", network="local", founder="frank")
    assert "set once" in call("setup_step", "frank", step="install", can_test_mode=True)["error"]
    assert ok("realm_configure_branding", "frank", logo_url="/logo.png").get("configured") == ["logo_url"]
    assert "unknown setup step" in call("setup_step", "frank", step="codex")["error"]
    assert "cannot configure" in call("setup_step", "frank", step="branding", name="x")["error"]
    assert "name the realm" in call("setup_launch", "frank")["error"]
    ok("setup_step", "frank", step="realm", name="Agora")
    ok("governance_update", "frank", voting_window=0)
    assert Realm["1"].calendar.voting_window == 0
    ok("setup_launch", "frank")
    denied("governance_update", "frank", voting_window=3600)
    out = ok("realm_configure", "ctl", open_registration=True)
    assert out["requires_vote"] and not Realm["1"].open_registration
    assert ok("governance_update", "ctl", voting_window=3600)["requires_vote"]
    denied("setup_state", "alice")


def release(sha256="aa", approval=APPROVED):
    """A realm with a file registry whose release wasm hashes to ``sha256``, approved as ``approval`` says."""
    world.build()
    ok("realm_configure_infrastructure", "ctl", file_registry_canister_id="registry", marketplace_canister_id="market")
    fake_chain.state["replies"][("registry", "get_file_size")] = json.dumps({"sha256": sha256})
    fake_chain.state["replies"][("registry", "get_namespace_approval_icc")] = approval


def test_I_ADM_3_an_upgrade_needs_a_release_the_trust_policy_accepts():
    release(approval=json.dumps({"approved": False}))
    assert "not approved" in call("realm_upgrade", "ctl", version="0.9.1", module_hash="sha256:aa")["error"]
    fake_chain.state["replies"][("registry", "get_namespace_approval_icc")] = APPROVED
    out = ok("realm_upgrade", "ctl", version="0.9.1", module_hash="sha256:aa")
    assert (out["approved_by"], Realm["1"].manifest_data["upgrade"]["version"]) == ("market", "0.9.1")
    assert fake_chain.state["calls"][-2:] == [
        ("registry", "get_file_size", json.dumps({"namespace": "wasm/realm-backend/0.9.1",
                                                  "path": "realm_backend.wasm.gz"})),
        ("registry", "get_namespace_approval_icc", "wasm/realm-backend/0.9.1")]
    ok("realm_configure_trust_policy", "ctl", trusted_approvers=["reviewers"])
    assert "does not trust" in call("realm_upgrade", "ctl", version="0.9.2", module_hash="aa")["error"]
    denied("realm_upgrade", "alice", version="0.9.1", module_hash="sha256:aa")


def test_I_ADM_3_the_approval_covers_only_the_release_wasm_hash():
    release(sha256="aa")
    out = call("realm_upgrade", "ctl", version="0.9.1", module_hash="sha256:bb")
    assert "is sha256:aa, not sha256:bb" in out["error"] and "upgrade" not in Realm["1"].manifest_data
    assert ok("realm_upgrade", "ctl", version="0.9.1", module_hash="aa")["approved_by"] == "market"


def test_I_ADM_3_a_pinned_module_needs_no_approval():
    release(sha256="aa")
    ok("realm_configure_trust_policy", "ctl", trusted_approvers=["sha256:cc"])
    fake_chain.state["calls"].clear()
    assert ok("realm_upgrade", "ctl", version="0.9.3", module_hash="sha256:cc")["approved_by"] == "sha256:cc"
    assert fake_chain.state["calls"] == []
    assert ok("realm_upgrade", "ctl", version="0.9.1", module_hash="aa")["approved_by"] == "market"
    assert "not sha256:dd" in call("realm_upgrade", "ctl", version="0.9.1", module_hash="dd")["error"]


def test_register_in_the_realm_registry():
    world.build()
    ok("setup_step", "ctl", step="realm", name='Agora "the first"')
    fake_chain.state["replies"][("reg", "register_realm")] = '(variant { Ok = "registered" })'
    ok("realm_register", "ctl", registry="reg", url="https://agora.realms")
    assert Registry["reg"].description == 'Agora "the first"'
    assert fake_chain.state["calls"][-1][2].startswith('("Agora \\"the first\\"", "https://agora.realms", ""')
    fake_chain.state["replies"][("reg2", "register_realm")] = '(variant { Err = "unpaid" })'
    assert "refused" in call("realm_register", "ctl", registry="reg2")["error"] and Registry["reg2"] is None
    denied("realm_register", "alice", registry="reg")


def test_orchestration_decide_answers_the_casals_baton():
    world.build()
    assert "no Casals baton" in call("orchestration_decide", "ctl", action_id=7, decision="approve")["error"]
    ok("realm_configure_infrastructure", "ctl", baton_canister_id="baton")
    fake_chain.state["replies"][("baton", "submit_approval")] = json.dumps({"ok": True, "status": "APPROVED"})
    assert ok("orchestration_decide", "ctl", action_id=7, decision="approve")["reply"]["status"] == "APPROVED"
    fake_chain.state["replies"][("baton", "reject_action")] = json.dumps({"ok": False, "error": "already executed"})
    assert "already executed" in call("orchestration_decide", "ctl", action_id=7, decision="reject")["error"]
    assert "approve or reject" in call("orchestration_decide", "ctl", action_id=7, decision="maybe")["error"]
    denied("orchestration_decide", "alice", action_id=7, decision="approve")


def test_the_realm_keeps_its_casals_conductor_and_stand():
    world.build()
    ok("realm_configure_infrastructure", "ctl", conductor_canister_id="conductor", casals_stand="agora")
    assert Realm["1"].manifest_data["casals"] == {"conductor_canister_id": "conductor", "casals_stand": "agora"}
    assert "casals_stand" not in ok("setup_state", "ctl")["realm"]


def test_monitor_and_export():
    world.build()
    mon = ok("realm_monitor", "ctl")
    assert mon["rows"]["Member"] == len(world.PEOPLE) and mon["status"] == "setup"
    page = ok("realm_export", "ctl", entity="Member", count=3)
    assert [r["id"] for r in page["rows"]] == ["alice", "bob", "dana"] and page["next"] == 4
    assert "unknown entity" in call("realm_export", "ctl", entity="json")["error"]
    denied("realm_monitor", "alice")
    denied("realm_export", "alice", entity="Member")


def test_shell_calls_operations_as_the_caller():
    world.build()
    p = ok("proposal_create", "alice", title="Bike lane")["proposal"]
    out = ok("shell", "ctl", expr='ops.proposal_list({"status": "voting"})')["result"]
    assert [x["id"] for x in out["proposals"]] == [p]
    world.profile("console", "shell.execute")
    world.person("bob", "console")
    weak = ok("shell", "bob", expr=f'ops.proposal_finalize(proposal="{p}")')["result"]
    assert not weak["ok"] and "proposal.manage" in weak["error"]
    denied("shell", "alice", expr="1")
