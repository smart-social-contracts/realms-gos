"""``tax.*`` verbs: who may change the tax policy, and how adoption is governed.

Reads are open to members. Writes need treasury-manager rights. Adopting goes
through the root org's policy: applied directly under 1/1, otherwise a vote
whose proposal adopts the draft frozen at submission time.
"""

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src" / "realm_backend"))

from _cdk_stub import ensure_cdk_stub  # noqa: E402

ensure_cdk_stub()

from core import tax_bridge as tb  # noqa: E402
from core import taxation  # noqa: E402
from ggg import TaxPolicy, TaxPolicyStatus  # noqa: E402
from ic_python_db import Database  # noqa: E402

MEMBERSHIP = {"code": "membership", "base": "flat", "method": "fixed", "amount": 10}
MANAGER = "manager-principal"
MEMBER = "member-principal"


@pytest.fixture
def realm(monkeypatch):
    Database.get_instance().clear()
    import core.access as access
    import core.codex_hooks as hooks
    import core.governed_action as ga

    dept = types.SimpleNamespace(
        name="ROOT",
        policy_threshold_m=1,
        policy_threshold_n=1,
        policy_quorum_percent=0,
        policy_veto_principals="",
    )
    users = {uid: types.SimpleNamespace(id=uid) for uid in (MANAGER, MEMBER)}
    proposals = []

    def submit(governing, summary, code_inline, proposer, metadata_extra=None, **kw):
        proposals.append({"summary": summary, "code": code_inline,
                          "metadata": metadata_extra, "proposer": proposer})
        return {"success": True, "applied": "proposal",
                "proposal_id": f"prop_{len(proposals):03d}", "summary": summary}

    def caller_user(caller):
        if caller not in users:
            raise PermissionError(f"User {caller} not found")
        return users[caller]

    monkeypatch.setattr(tb, "_caller_user", caller_user)
    monkeypatch.setattr(tb, "_governing", lambda: dept)
    monkeypatch.setattr(tb, "_can_manage", lambda user: user.id == MANAGER)
    monkeypatch.setattr(ga, "governing_org", lambda name=None: dept)
    monkeypatch.setattr(ga, "submit_replay_proposal", submit)
    monkeypatch.setattr(access, "_is_controller_or_trusted", lambda c: False)
    monkeypatch.setattr(hooks, "get_config", lambda: {})
    monkeypatch.setattr(hooks, "get_hook", lambda name: None)
    monkeypatch.setattr(hooks, "get_active_codex", lambda: "agora")
    return {"dept": dept, "proposals": proposals}


def save(caller=MANAGER, taxes=(MEMBERSHIP,)):
    return tb.v_save_draft(caller=caller, document={"taxes": list(taxes)}, description="d")


class TestRights:
    def test_reads_need_a_registered_user(self, realm):
        with pytest.raises(PermissionError, match="not found"):
            tb.v_overview(caller="ghost")
        with pytest.raises(PermissionError, match="not found"):
            tb.v_history(caller="ghost")

    def test_any_member_can_read(self, realm):
        save()
        out = tb.v_overview(caller=MEMBER)
        assert out["draft"]["taxes"][0]["code"] == "membership"
        assert out["can_manage"] is False
        assert out["codex"] == "agora"
        assert out["codex_assesses"] is False
        assert out["options"]["bases"] == list(taxation.BASES)

    @pytest.mark.parametrize("verb,kwargs", [
        (tb.v_save_draft, {"document": {"taxes": [MEMBERSHIP]}}),
        (tb.v_discard_draft, {}),
        (tb.v_adopt, {}),
        (tb.v_assess, {}),
    ])
    def test_members_cannot_change_anything(self, realm, verb, kwargs):
        with pytest.raises(PermissionError, match="admin/head"):
            verb(caller=MEMBER, **kwargs)

    def test_attribution_is_the_host_caller(self, realm):
        assert save()["created_by"] == MANAGER


class TestAdoption:
    def test_nothing_to_adopt_without_a_draft(self, realm):
        with pytest.raises(ValueError, match="no draft"):
            tb.v_adopt(caller=MANAGER)

    def test_one_of_one_policy_adopts_directly(self, realm):
        draft = save()
        out = tb.v_adopt(caller=MANAGER)
        assert out["applied"] == "direct"
        assert TaxPolicy.adopted().id == draft["id"]
        assert TaxPolicy.adopted().adopted_by == MANAGER
        assert realm["proposals"] == []

    def test_other_policies_ask_before_opening_a_vote(self, realm):
        realm["dept"].policy_threshold_m = 2
        realm["dept"].policy_threshold_n = 3
        draft = save()

        out = tb.v_adopt(caller=MANAGER)
        assert out["requires_confirmation"] is True
        assert realm["proposals"] == []
        assert TaxPolicy[draft["id"]].status == TaxPolicyStatus.DRAFT
        assert TaxPolicy.adopted() is None

    def test_confirming_opens_a_vote_on_the_frozen_draft(self, realm):
        realm["dept"].policy_threshold_m = 2
        realm["dept"].policy_threshold_n = 3
        draft = save()

        out = tb.v_adopt(caller=MANAGER, confirm=True)
        assert out["applied"] == "proposal"
        frozen = TaxPolicy[draft["id"]]
        assert frozen.status == TaxPolicyStatus.PROPOSED
        assert frozen.proposal_id == out["proposal_id"]
        assert TaxPolicy.adopted() is None, "nothing applies before the vote"

        proposal = realm["proposals"][0]
        assert proposal["metadata"] == {"tax_policy_id": draft["id"]}
        assert "core.taxation" in proposal["code"] and "adopt_policy" in proposal["code"]
        assert draft["id"] in proposal["code"]

        # Editing during the vote starts a new draft instead of changing the
        # one being voted on.
        assert save(taxes=({**MEMBERSHIP, "amount": 99},))["id"] != draft["id"]

    def test_replay_adopts_what_was_voted(self, realm):
        realm["dept"].policy_threshold_m = 2
        realm["dept"].policy_threshold_n = 3
        draft = save()
        tb.v_adopt(caller=MANAGER, confirm=True)
        save(taxes=({**MEMBERSHIP, "amount": 99},))

        scope = {}
        exec(realm["proposals"][0]["code"], scope)
        assert scope["main"]()["success"]
        adopted = TaxPolicy.adopted()
        assert adopted.id == draft["id"]
        assert adopted.taxes()[0]["amount"] == 10.0


def test_verbs_are_registered_and_classified():
    from core import extension_bridge as eb

    for verb in tb.VERBS:
        assert verb in eb.VERBS
    assert tb.READ_VERBS <= eb.READ_VERBS
    writes = set(tb.VERBS) - tb.READ_VERBS
    assert writes == {"tax.save_draft", "tax.discard_draft", "tax.adopt", "tax.assess"}
    assert not writes & eb.READ_VERBS


def test_codices_can_read_the_policy_but_not_write_it():
    from core import codex_bridge as cb

    assert "tax.policy" in cb.READ_VERBS
    assert not any(v.startswith("tax.") and v != "tax.policy" for v in cb.VERBS)


EXTENSION = (Path(__file__).parent.parent.parent / "extensions" / "extensions"
             / "tax_manager")


@pytest.fixture
def extension(realm, monkeypatch):
    """The tax_manager backend on the sandbox SDK, its rpc wired to the verbs."""
    import json

    import ggg_sdk

    if not EXTENSION.is_dir():
        pytest.skip("extensions submodule not checked out")
    caller = {"id": MANAGER}

    def rpc(action, **kwargs):
        return tb.VERBS[action](caller=caller["id"], **kwargs)

    sdk = types.ModuleType("ggg_sdk")
    sdk.rpc = rpc
    exec(compile(ggg_sdk.GGG_SDK_SOURCE, "ggg_sdk.py", "exec"), sdk.__dict__)
    monkeypatch.setitem(sys.modules, "ggg_sdk", sdk)
    entry = types.ModuleType("tax_manager_entry")
    source = (EXTENSION / "backend" / "entry.py").read_text()
    exec(compile(source, "entry.py", "exec"), entry.__dict__)

    def call(fn, caller_id=MANAGER, **args):
        caller["id"] = caller_id
        return json.loads(entry.EXTENSION_FUNCTIONS[fn](json.dumps(args)))

    return call


class TestExtension:
    def test_functions_match_the_manifest(self):
        import json

        if not EXTENSION.is_dir():
            pytest.skip("extensions submodule not checked out")
        manifest = json.loads((EXTENSION / "manifest.json").read_text())
        source = (EXTENSION / "backend" / "entry.py").read_text()
        for fn in manifest["functions"]:
            assert f'"{fn}": {fn},' in source
        assert set(manifest["capabilities"]) == set(tb.VERBS)

    def test_edit_and_adopt_through_the_extension(self, extension):
        assert extension("save_tax_draft", document={"taxes": [MEMBERSHIP]})["success"]
        out = extension("adopt_tax_policy")
        assert out["success"] and out["data"]["applied"] == "direct"
        overview = extension("get_tax_overview", caller_id=MEMBER)["data"]
        assert overview["adopted"]["taxes"][0]["code"] == "membership"
        assert extension("get_tax_history")["data"]["rows"][0]["status"] == "adopted"

    def test_members_get_an_error_not_an_exception(self, extension):
        out = extension("save_tax_draft", caller_id=MEMBER, document={"taxes": [MEMBERSHIP]})
        assert out == {"success": False, "error": out["error"]}
        assert "admin/head" in out["error"]

    def test_invalid_documents_come_back_as_errors(self, extension):
        out = extension("save_tax_draft", document={"taxes": [{"code": "Bad Code"}]})
        assert out["success"] is False and "code" in out["error"]

    def test_preview_uses_the_reference_calculation(self, extension):
        tax = {"code": "property", "base": "land_area", "unit_value": 100,
               "method": "progressive",
               "brackets": [{"up_to": 50000, "rate_bp": 40},
                            {"up_to": None, "rate_bp": 80}]}
        out = extension("preview_tax", tax=tax, quantity=600)["data"]
        assert out["value"] == 60000
        assert out["amount"] == pytest.approx(280.0)
        assert [line["rate_bp"] for line in out["lines"]] == [40, 80]
