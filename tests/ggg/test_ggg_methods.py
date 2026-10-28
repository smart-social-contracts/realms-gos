"""GGG methods the acceptance tests cannot reach through the mirror, called with Candid values: bytes for blobs,
principals, lists of records where Realms GOS takes maps."""

import fake_chain
import ggg_api
import ggg_methods as g
import world
from core import op
from core.candid import Reader
from ggg import Department, KeyEnvelope, Mandate, Proposal, Quarter, Realm, User, UserProfile
from kit import ok
from mirror import FakePrincipal as P
from test_assets import RX, agora, license_clinic, prescribe
from test_crypto import REPORT, health


def caller(principal):
    fake_chain.state["caller"] = principal


def do(update):
    """Run an update method to its reply, as the canister drives its generator."""
    return op.run(update)


def test_private_data_and_wrapped_keys_are_blobs():
    health()
    caller("alice")
    assert do(g.ggg_private_data_update(b"\x01\xff"))["Ok"]["private_data"] == b"\x01\xff"
    assert User["alice"].private_data == "01ff"
    caller("bob")
    assert g.ggg_user_get(P("alice"))["Ok"]["user"]["private_data"] is None  # only its owner reads it
    caller("alice")
    keys = [{"principal": P("alice"), "wrapped_dek": b"\xaa"}, {"principal": P("claude-conn"), "wrapped_dek": b"\xbb"}]
    assert do(g.ggg_crypto_grant({"scope": "user:alice:private", "keys": keys})) == {"Ok": 2}
    assert KeyEnvelope["user:alice:private|claude-conn"].wrapped_dek == "bb"
    env = g.ggg_crypto_envelope("user:alice:private")["Ok"]
    assert (env["scope"], env["principal"], env["wrapped_dek"]) == ("user:alice:private", "alice", b"\xaa")
    caller("carla")
    stored = do(g.ggg_document_store({"document": None, "department": "Health", "keys": [], **REPORT}))["Ok"]
    doc = stored["document"]["id"]
    assert stored["scope"] == f"dept:Health:doc:{doc}"
    shared = do(g.ggg_document_share({"document": doc, "keys": [{"principal": P("dr-ana"), "wrapped_dek": b"\x0a"}]}))
    assert shared == {"Ok": 1}
    caller("dr-ana")
    got = g.ggg_document_get(doc)["Ok"]
    assert got["document"]["hash"] == "sha256:ab12" and got["envelope"]["wrapped_dek"] == b"\x0a"
    caller("bob")
    assert g.ggg_document_get(doc) == {"Ok": None} or "Err" in g.ggg_document_get(doc)


def test_a_credential_header_is_a_list_of_text_pairs():
    agora()
    license_clinic()
    header = prescribe()["header"]
    pairs = [(k, str(v)) for k, v in header.items()]
    caller(fake_chain.ANONYMOUS)
    assert g.ggg_credential_verify({"header": pairs, "frozen": False}) == {
        "Ok": {"valid": True, "ours": True, "licensed": True, "expired": False, "revoked": False}}
    assert not g.ggg_credential_verify({"header": pairs, "frozen": True})["Ok"]["valid"]
    assert dict(pairs)["ggg:schema"] == RX


def test_mandates_are_revoked_and_reassigned_and_drafts_opened():
    world.build()
    caller("ctl")
    made = do(g.ggg_mandate_create({"title": "Road works", "scope": {"Map": []}, "max_value": None,
                                    "symbol": None, "executor": "Infrastructure", "expires_at": None}))
    assert made == {"Ok": {"Done": "M-1"}}
    assert do(g.ggg_mandate_assign_executor({"mandate": "M-1", "executor": "Finance"})) == {"Ok": {"Done": None}}
    assert Mandate["M-1"].executor.id == "Finance"
    assert do(g.ggg_mandate_revoke({"mandate": "M-1"})) == {"Ok": {"Done": None}}
    assert Mandate["M-1"].status == "revoked"
    Proposal(id="P-1", title="Draft", proposer="alice", department=Department["Administration"], status="draft")
    opened = do(g.ggg_proposal_open("P-1"))["Ok"]
    assert opened["status"] == {"Voting": None} and opened["deadline"] > opened["opens_at"] > 0


def test_tokens_are_configured_in_setup_and_governed_after():
    world.build()
    ok("setup_step", "ctl", step="install", network="ic", founder="frank")
    caller("frank")
    out = do(g.ggg_realm_configure_tokens({"accounting_currency": "ckUSDC", "accounting_currency_decimals": 6}))
    assert out == {"Ok": {"Done": None}} and Realm["1"].accounting_currency_decimals == 6
    info = g.ggg_realm_info()
    assert (info["accounting_currency"], info["accounting_currency_decimals"]) == ("ckUSDC", 6)


def motion(action, title="Road works"):
    return {"title": title, "description": "", "department": None, "action": action, "on_behalf_of": None}


def test_a_proposal_calls_a_ggg_method_with_candid_arguments():
    world.build()
    world.policy(Department["Administration"], 2, 3)
    args = {"title": "Road works", "scope": {"Map": []}, "max_value": 200000, "symbol": None,
            "executor": "Infrastructure", "expires_at": None}
    arg = fake_chain.candid_encode("(" + ggg_api.to_candid(args, "MandateCreateArgs") + ")")
    caller("alice")
    filed = do(g.ggg_proposal_create(motion({"Call": {"method": "ggg_mandate_create", "arg": arg}})))
    pid = filed["Ok"]
    assert Proposal[pid].action == {"op": "mandate_create", "args": {
        "title": "Road works", "scope": None, "max_value": 200000, "executor": "Infrastructure"}}
    shown = g.ggg_proposal_get({"id": pid, "cursor": None, "limit": None})["Ok"]["proposal"]
    call = shown["action"]["Call"]
    assert call["method"] == "ggg_mandate_create" and shown["department"] is None
    back = ggg_api.from_candid(Reader(fake_chain.candid_decode(call["arg"])).args()[0], "MandateCreateArgs")
    assert (back["title"], back["max_value"], back["executor"]) == ("Road works", 200000, "Infrastructure")


def test_a_call_to_a_method_the_realm_lacks_or_with_the_wrong_arguments_is_refused():
    world.build()
    caller("alice")
    empty = fake_chain.candid_encode("()")
    out = do(g.ggg_proposal_create(motion({"Call": {"method": "ggg_no_such_method", "arg": empty}})))
    assert "InvalidArgument" in out["Err"]
    wrong = fake_chain.candid_encode('("not a ProposalVoteArgs")')
    out = do(g.ggg_proposal_create(motion({"Call": {"method": "ggg_proposal_vote", "arg": wrong}})))
    assert "InvalidArgument" in out["Err"]
    out = do(g.ggg_proposal_create(motion({"Call": {"method": "ggg_proposal_vote", "arg": b"not candid"}})))
    assert "InvalidArgument" in out["Err"]
    assert Proposal.count() == 0


def test_a_caller_is_answered_about_itself():
    world.build()
    caller(fake_chain.ANONYMOUS)
    assert g.ggg_user_get(P(fake_chain.ANONYMOUS)) == {"Ok": None}
    assert "Unauthenticated" in g.ggg_user_get(P("alice"))["Err"]
    caller("mallory")
    assert g.ggg_user_get(P("mallory"))["Ok"]["user"]["id"] == "mallory"


def install_args(founder="frank", **kw):
    return {"founder": P(founder), "network": "staging", "registry": None, "test_mode": False, **kw}


def test_the_host_installs_the_realm_once_and_names_its_canisters():
    world.build()
    caller(fake_chain.ANONYMOUS)
    assert "Unauthenticated" in do(g.ggg_host_install(install_args()))["Err"]
    caller("alice")
    assert "Forbidden" in do(g.ggg_host_install(install_args()))["Err"]
    User["dana"].profiles = [*User["dana"].profiles, UserProfile["admin"]]  # holds `all`, but is not the host
    caller("dana")
    assert "Forbidden" in do(g.ggg_host_install(install_args()))["Err"]
    caller("ctl")
    state = do(g.ggg_host_install(install_args(registry=P("registry"), test_mode=True)))["Ok"]
    assert state["founder"] == "frank" and "install" in state["done"] and "install" not in state["todo"]
    assert (Realm["1"].network, Realm["1"].can_test_mode) == ("staging", True)
    assert "Conflict" in do(g.ggg_host_install(install_args()))["Err"]
    canisters = {"frontend": P("front"), "file_registry": None, "extra": [("realms_gos:baton", P("baton"))]}
    assert "infrastructure" in do(g.ggg_host_infrastructure(canisters))["Ok"]["done"]
    assert Realm["1"].frontend_canister_id == "front"
    assert Realm["1"].manifest_data["casals"]["baton_canister_id"] == "baton"
    wrong = {"frontend": None, "file_registry": None, "extra": [("monad_gos:oracle", P("x"))]}
    assert "InvalidArgument" in do(g.ggg_host_infrastructure(wrong))["Err"]
    ok("setup_step", "frank", step="realm", name="Agora")
    ok("setup_launch", "frank")
    caller("ctl")
    assert "Conflict" in do(g.ggg_host_infrastructure(canisters))["Err"]


def test_the_installer_it_was_created_with_is_a_host_too():
    world.build()
    Realm["1"].installer_canister_id = "installer"
    caller("installer")
    assert do(g.ggg_host_install(install_args()))["Ok"]["founder"] == "frank"


def test_a_capital_counts_its_quarters_members():
    world.build()
    members = g.ggg_realm_info()["member_count"]
    assert members > 0
    Quarter(id="harbor", population=40, status="active")
    Quarter(id="hills", population=2, status="active")
    assert g.ggg_realm_info()["member_count"] == members + 42
