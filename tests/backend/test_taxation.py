"""Tax policy: the parameter document, its lifecycle, and the reference calculation.

The document is stored once and read by codices that do the invoicing, so the
validation has to be strict (a typo must be an error, not a tax that ignores
the parameter), and adopting has to leave exactly one policy in force.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src" / "realm_backend"))

from _cdk_stub import ensure_cdk_stub  # noqa: E402

ensure_cdk_stub()

from core import taxation  # noqa: E402
from ggg import TaxPolicy, TaxPolicyStatus  # noqa: E402
from ggg_sdk import tax_breakdown, tax_period  # noqa: E402
from ic_python_db import Database  # noqa: E402

PROPERTY = {
    "code": "property",
    "label": "Property tax",
    "base": "land_area",
    "unit_value": 100,
    "method": "progressive",
    "brackets": [
        {"up_to": 10_000, "rate_bp": 0},
        {"up_to": 50_000, "rate_bp": 50},
        {"up_to": None, "rate_bp": 100},
    ],
    "period": "annual",
}
MEMBERSHIP = {"code": "membership", "base": "flat", "method": "fixed", "amount": 10,
              "period": "monthly"}


@pytest.fixture(autouse=True)
def clean_db():
    Database.get_instance().clear()
    yield


def doc(*taxes):
    return {"taxes": [dict(t) for t in taxes]}


# ---------------------------------------------------------------------------
# Document validation
# ---------------------------------------------------------------------------


class TestDocument:
    def test_canonical_form_fills_defaults(self):
        out = taxation.normalize_document(doc(MEMBERSHIP))
        tax = out["taxes"][0]
        assert out["schema_version"] == taxation.SCHEMA_VERSION
        assert tax["label"] == "membership"
        assert tax["enabled"] is True
        assert tax["applies_to"] == ["member"]
        assert tax["exempt_profiles"] == []
        assert tax["due_days"] == 30
        assert tax["amount"] == 10.0

    def test_accepts_a_json_string(self):
        import json

        out = taxation.normalize_document(json.dumps(doc(PROPERTY)))
        assert out["taxes"][0]["brackets"][-1] == {"up_to": None, "rate_bp": 100}

    def test_unknown_fields_are_refused_not_dropped(self):
        with pytest.raises(ValueError, match="rate_percent"):
            taxation.normalize_document(doc({**MEMBERSHIP, "rate_percent": 5}))

    def test_fields_of_another_method_are_left_out(self):
        tax = taxation.normalize_document(doc({**MEMBERSHIP, "rate_bp": 500}))["taxes"][0]
        assert "rate_bp" not in tax

    @pytest.mark.parametrize("code", ["", "Property", "1tax", "tax-1", "x" * 33])
    def test_codes_are_lowercase_identifiers(self, code):
        with pytest.raises(ValueError, match="code"):
            taxation.normalize_document(doc({**MEMBERSHIP, "code": code}))

    def test_codes_are_unique(self):
        with pytest.raises(ValueError, match="twice"):
            taxation.normalize_document(doc(MEMBERSHIP, MEMBERSHIP))

    def test_a_flat_tax_is_a_fixed_amount(self):
        with pytest.raises(ValueError, match="flat"):
            taxation.normalize_document(doc({**MEMBERSHIP, "method": "proportional",
                                             "rate_bp": 100}))

    def test_fixed_amount_must_be_positive(self):
        for bad in (0, -1, "10", True, None):
            with pytest.raises(ValueError, match="amount"):
                taxation.normalize_document(doc({**MEMBERSHIP, "amount": bad}))

    def test_rates_are_whole_basis_points_up_to_100_percent(self):
        base = {"code": "land", "base": "land_area", "method": "proportional"}
        for bad in (-1, 10_001, 1.5, "100"):
            with pytest.raises(ValueError, match="basis points"):
                taxation.normalize_document(doc({**base, "rate_bp": bad}))

    def test_brackets_must_rise_and_end_open(self):
        brackets = PROPERTY["brackets"]
        with pytest.raises(ValueError, match="open-ended"):
            taxation.normalize_document(doc({**PROPERTY, "brackets": brackets[:2]}))
        with pytest.raises(ValueError, match="only the last"):
            taxation.normalize_document(doc({**PROPERTY, "brackets": [
                {"up_to": None, "rate_bp": 0}, {"up_to": None, "rate_bp": 10}]}))
        with pytest.raises(ValueError, match="greater than"):
            taxation.normalize_document(doc({**PROPERTY, "brackets": [
                {"up_to": 500, "rate_bp": 0}, {"up_to": 100, "rate_bp": 10},
                {"up_to": None, "rate_bp": 20}]}))

    def test_land_unit_value_must_be_positive(self):
        with pytest.raises(ValueError, match="unit_value"):
            taxation.normalize_document(doc({**PROPERTY, "unit_value": 0}))

    def test_a_tax_must_apply_to_someone(self):
        with pytest.raises(ValueError, match="applies_to"):
            taxation.normalize_document(doc({**MEMBERSHIP, "applies_to": []}))

    def test_empty_policy_is_valid(self):
        assert taxation.normalize_document({"taxes": []})["taxes"] == []


# ---------------------------------------------------------------------------
# Reference calculation (ggg_sdk, shared by codices and the extension preview)
# ---------------------------------------------------------------------------


class TestBreakdown:
    def tax(self, raw):
        return taxation.normalize_document(doc(raw))["taxes"][0]

    def test_flat_is_the_amount_for_any_member(self):
        assert tax_breakdown(self.tax(MEMBERSHIP), 1)["amount"] == 10.0
        assert tax_breakdown(self.tax(MEMBERSHIP), 0)["amount"] == 0.0

    def test_proportional_applies_the_rate_to_the_value(self):
        tax = self.tax({"code": "land", "base": "land_area", "unit_value": 20,
                        "method": "proportional", "rate_bp": 250})
        out = tax_breakdown(tax, 100)
        assert out["value"] == 2000.0
        assert out["amount"] == 50.0

    def test_progressive_brackets_are_marginal(self):
        out = tax_breakdown(self.tax(PROPERTY), 600)  # value 60 000
        assert out["value"] == 60_000.0
        # 0 on the first 10k, 0.5% of the next 40k, 1% of the last 10k
        assert out["amount"] == pytest.approx(200.0 + 100.0)
        assert [line["rate_bp"] for line in out["lines"]] == [0, 50, 100]

    def test_progressive_stops_in_the_bracket_the_value_reaches(self):
        out = tax_breakdown(self.tax(PROPERTY), 300)  # value 30 000
        assert out["amount"] == pytest.approx(100.0)
        assert len(out["lines"]) == 2

    def test_exempt_below_exempts_entirely(self):
        tax = self.tax({**PROPERTY, "exempt_below": 20_000})
        assert tax_breakdown(tax, 150) == {
            **tax_breakdown(tax, 150), "amount": 0.0, "exempt": True,
        }
        # Above the threshold the whole value is taxed, not just the excess.
        assert tax_breakdown(tax, 300)["amount"] == pytest.approx(100.0)

    @pytest.mark.parametrize("period,expected", [
        ("monthly", "2026-10"), ("quarterly", "2026-Q4"), ("annual", "2026"),
    ])
    def test_period_keys(self, period, expected):
        assert tax_period(period, 1_792_000_000) == expected  # 2026-10-14


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_saving_twice_edits_the_same_draft(self):
        first = taxation.save_draft(doc(MEMBERSHIP), "v1", triggered_by="alice")
        second = taxation.save_draft(doc(MEMBERSHIP, PROPERTY), "v2", triggered_by="bob")
        assert first["id"] == second["id"]
        assert second["description"] == "v2"
        assert len(second["taxes"]) == 2
        assert second["created_by"] == "alice"

    def test_invalid_documents_are_not_saved(self):
        with pytest.raises(ValueError):
            taxation.save_draft({"taxes": [{"code": "x"}]})
        assert taxation.current_draft() is None

    def test_adopting_leaves_one_policy_in_force(self):
        first = taxation.save_draft(doc(MEMBERSHIP))
        assert taxation.adopt_policy({"policy_id": first["id"], "triggered_by": "a"})["success"]
        second = taxation.save_draft(doc(PROPERTY))
        assert second["id"] != first["id"], "the adopted row is no longer the draft"

        out = taxation.adopt_policy({"policy_id": second["id"], "triggered_by": "b"})
        assert out["superseded"] == first["id"]
        assert TaxPolicy.adopted().id == second["id"]
        assert TaxPolicy[first["id"]].status == TaxPolicyStatus.SUPERSEDED
        assert TaxPolicy.adopted().adopted_by == "b"

    def test_superseded_and_unknown_policies_cannot_be_adopted(self):
        first = taxation.save_draft(doc(MEMBERSHIP))
        taxation.adopt_policy({"policy_id": first["id"]})
        second = taxation.save_draft(doc(PROPERTY))
        taxation.adopt_policy({"policy_id": second["id"]})

        assert taxation.adopt_policy({"policy_id": first["id"]})["success"] is False
        assert taxation.adopt_policy({"policy_id": "TAX-POLICY-99"})["success"] is False

    def test_a_proposed_draft_is_frozen(self):
        """What passes the vote must be what was voted on."""
        draft = taxation.save_draft(doc(MEMBERSHIP))
        taxation.mark_proposed(draft["id"], "prop_007")
        edited = taxation.save_draft(doc(PROPERTY))
        assert edited["id"] != draft["id"]

        frozen = TaxPolicy[draft["id"]]
        assert frozen.status == TaxPolicyStatus.PROPOSED
        assert frozen.proposal_id == "prop_007"
        assert [t["code"] for t in frozen.taxes()] == ["membership"]

        assert taxation.adopt_policy({"policy_id": draft["id"]})["success"]
        assert TaxPolicy.adopted().id == draft["id"]
        assert taxation.current_draft().id == edited["id"]

    def test_discarding_removes_only_the_draft(self):
        adopted = taxation.save_draft(doc(MEMBERSHIP))
        taxation.adopt_policy({"policy_id": adopted["id"]})
        draft = taxation.save_draft(doc(PROPERTY))
        assert taxation.discard_draft() == {"discarded": draft["id"]}
        assert taxation.current_draft() is None
        assert TaxPolicy.adopted().id == adopted["id"]
        assert taxation.discard_draft() == {"discarded": None}

    def test_history_is_paginated(self):
        for i in range(3):
            row = taxation.save_draft(doc(MEMBERSHIP), f"v{i}")
            taxation.adopt_policy({"policy_id": row["id"]})
        page = taxation.history(cursor=1, count=2)
        assert [r["description"] for r in page["rows"]] == ["v0", "v1"]
        assert page["next"] == 3
        rest = taxation.history(cursor=page["next"], count=2)
        assert [r["status"] for r in rest["rows"]] == ["adopted"]
        assert rest["next"] is None


class TestEffectivePolicy:
    def test_adopted_policy_wins(self, monkeypatch):
        import core.codex_hooks as hooks

        monkeypatch.setattr(hooks, "get_config", lambda: {"taxes": [MEMBERSHIP]})
        row = taxation.save_draft(doc(PROPERTY))
        taxation.adopt_policy({"policy_id": row["id"]})
        out = taxation.effective_policy()
        assert out["source"] == "policy"
        assert out["policy_id"] == row["id"]
        assert [t["code"] for t in out["taxes"]] == ["property"]

    def test_codex_defaults_apply_until_a_policy_is_adopted(self, monkeypatch):
        import core.codex_hooks as hooks

        monkeypatch.setattr(hooks, "get_config", lambda: {"taxes": [MEMBERSHIP]})
        out = taxation.effective_policy()
        assert out["source"] == "codex"
        assert out["taxes"][0]["amount"] == 10.0

    def test_invalid_codex_defaults_mean_no_taxes(self, monkeypatch):
        import core.codex_hooks as hooks

        monkeypatch.setattr(hooks, "get_config", lambda: {"taxes": [{"code": "bad"}]})
        assert taxation.effective_policy() == {"source": "none", "policy_id": "", "taxes": []}

    def test_assessment_needs_a_codex_that_assesses(self, monkeypatch):
        import core.codex_hooks as hooks

        monkeypatch.setattr(hooks, "get_hook", lambda name: None)
        with pytest.raises(ValueError, match="does not assess"):
            taxation.run_assessment()

    def test_assessment_needs_a_tax_in_force(self, monkeypatch):
        import core.codex_hooks as hooks

        monkeypatch.setattr(hooks, "get_hook", lambda name: (lambda args: "{}"))
        monkeypatch.setattr(hooks, "get_config", lambda: {})
        with pytest.raises(ValueError, match="adopt a tax policy"):
            taxation.run_assessment()

    def test_assessment_hands_the_policy_to_the_codex(self, monkeypatch):
        import core.codex_hooks as hooks

        seen = {}

        def call_hook(name, args=None, default=None):
            seen.update(name=name, args=args)
            return {"success": True, "invoices_created": 2, "next_cursor": ""}

        monkeypatch.setattr(hooks, "get_hook", lambda name: (lambda args: "{}"))
        monkeypatch.setattr(hooks, "call_hook", call_hook)
        row = taxation.save_draft(doc(MEMBERSHIP))
        taxation.adopt_policy({"policy_id": row["id"]})

        out = taxation.run_assessment(cursor="members:26", triggered_by="alice")
        assert out["invoices_created"] == 2
        assert seen["name"] == taxation.ASSESS_HOOK
        assert seen["args"]["cursor"] == "members:26"
        assert seen["args"]["policy"]["policy_id"] == row["id"]
        assert seen["args"]["count"] == taxation.ASSESS_BATCH

    def test_codex_failure_surfaces_as_an_error(self, monkeypatch):
        import core.codex_hooks as hooks

        monkeypatch.setattr(hooks, "get_hook", lambda name: (lambda args: "{}"))
        monkeypatch.setattr(hooks, "call_hook",
                            lambda *a, **k: {"success": False, "error": "no currency"})
        row = taxation.save_draft(doc(MEMBERSHIP))
        taxation.adopt_policy({"policy_id": row["id"]})
        with pytest.raises(ValueError, match="no currency"):
            taxation.run_assessment()
