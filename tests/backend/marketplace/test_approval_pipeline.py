"""Tests for the submission → review → approval pipeline (issue #267).

Three properties the marketplace has to hold up:

  1. Publishing is gated on an active developer license, so every submission
     has an accountable owner behind it.
  2. Nothing is approved on arrival, and republishing withdraws whatever the
     last review concluded.
  3. Reviewing is a job separate from holding the canister's upgrade key, and
     an approval only counts once the file registry has accepted it.
"""

from marketplace_backend.api import approval as approval_api
from marketplace_backend.api import codices as codices_api
from marketplace_backend.api import config as config_api
from marketplace_backend.api import extensions as ext_api
from marketplace_backend.api import licenses as licenses_api
from marketplace_backend.api import verification as verification_api

DEV = "dev-principal"
REVIEWER = "reviewer-principal"
OUTSIDER = "outsider-principal"
REGISTRY = "registry-cai"
YEAR_SECONDS = 365 * 24 * 3600


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def license_for(principal, as_caller):
    as_caller("controller", controller=True)
    r = licenses_api.grant_manual_license(
        principal=principal, duration_seconds=YEAR_SECONDS, note="test"
    )
    assert r["success"], r
    as_caller(principal)


_DEFAULT_NAMESPACE = object()


def publish_extension(
    developer, ext_id="voting", version="1.0.0", namespace=_DEFAULT_NAMESPACE
):
    if namespace is _DEFAULT_NAMESPACE:
        namespace = f"ext/{ext_id}/{version}"
    return ext_api.create_extension(
        developer=developer,
        extension_id=ext_id,
        name="Voting",
        description="",
        version=version,
        price_e8s=0,
        icon="",
        categories="",
        screenshots="",
        file_registry_canister_id=REGISTRY,
        file_registry_namespace=namespace,
        download_url="",
    )


def status_of(ext_id):
    details = ext_api.get_extension_details(ext_id)
    assert details["success"], details
    return details["extension"]


class FakeRegistry:
    """Stands in for the file registry canister on the other side of the call."""

    def __init__(self, response='{"ok": true, "file_count": 3}'):
        self.response = response
        self.calls = []

    def set_namespace_approval(self, args):
        self.calls.append(args)
        return self.response


def run_review(monkeypatch, registry, **kwargs):
    """Drive api.approval.review_listing, intercepting its outbound call."""
    monkeypatch.setattr(
        approval_api, "FileRegistryService", lambda principal: registry
    )
    monkeypatch.setattr(
        approval_api.Principal, "from_str", staticmethod(lambda v: v), raising=False
    )

    generator = approval_api.review_listing(**kwargs)
    to_send = None
    while True:
        try:
            generator.send(to_send)
        except StopIteration as stop:
            return stop.value
        to_send = registry.response


# ---------------------------------------------------------------------------
# 1. Publishing requires a license
# ---------------------------------------------------------------------------


def test_publishing_without_a_license_is_refused(as_caller):
    as_caller(DEV)
    r = publish_extension(DEV)
    assert r["success"] is False
    assert "developer license" in r["error"]


def test_publishing_a_codex_without_a_license_is_refused(as_caller):
    as_caller(DEV)
    r = codices_api.create_codex(
        developer=DEV,
        codex_id="syntropia",
        realm_type="city",
        name="Syntropia",
        description="",
        version="1.0.0",
        price_e8s=0,
        icon="",
        categories="",
        file_registry_canister_id=REGISTRY,
        file_registry_namespace="codex/syntropia/1.0.0",
    )
    assert r["success"] is False
    assert "developer license" in r["error"]


def test_an_expired_license_does_not_count(as_caller, advance_time):
    license_for(DEV, as_caller)
    advance_time(YEAR_SECONDS + 60)
    r = publish_extension(DEV)
    assert r["success"] is False
    assert "developer license" in r["error"]


def test_a_licensed_developer_can_publish(as_caller):
    license_for(DEV, as_caller)
    r = publish_extension(DEV)
    assert r["success"] is True, r


def test_a_controller_can_publish_without_a_license(as_caller):
    # Seeding first-party packages must not require issuing a license to the
    # deploy identity.
    as_caller("controller", controller=True)
    r = publish_extension("controller")
    assert r["success"] is True, r


# ---------------------------------------------------------------------------
# 2. Nothing arrives approved
# ---------------------------------------------------------------------------


def test_a_new_listing_starts_in_review(as_caller):
    license_for(DEV, as_caller)
    r = publish_extension(DEV)
    assert r["verification_status"] == "pending_review"
    assert status_of("voting")["verification_status"] == "pending_review"


def test_republishing_withdraws_a_previous_approval(as_caller, monkeypatch):
    license_for(DEV, as_caller)
    publish_extension(DEV)

    as_caller(REVIEWER, controller=True)
    run_review(
        monkeypatch,
        FakeRegistry(),
        caller=REVIEWER,
        item_kind="ext",
        item_id="voting",
        approve=True,
        notes="looks fine",
    )
    assert status_of("voting")["verification_status"] == "verified"

    as_caller(DEV)
    r = publish_extension(DEV, version="1.1.0")
    assert r["verification_status"] == "pending_review"
    assert status_of("voting")["verification_status"] == "pending_review"


def test_a_listing_in_review_is_not_offered_as_verified(as_caller):
    license_for(DEV, as_caller)
    publish_extension(DEV)
    listed = ext_api.list_extensions(page=1, per_page=10, verified_only=True)
    assert listed["total_count"] == 0


# ---------------------------------------------------------------------------
# 3. Reviewing is its own role
# ---------------------------------------------------------------------------


def test_reviewers_are_separate_from_controllers(as_caller):
    as_caller("controller", controller=True)
    assert config_api.add_reviewer(REVIEWER)["success"] is True
    assert config_api.get_reviewers() == [REVIEWER]

    as_caller(REVIEWER)
    assert config_api.is_reviewer(REVIEWER) is True
    as_caller(OUTSIDER)
    assert config_api.is_reviewer(OUTSIDER) is False


def test_only_a_controller_can_appoint_reviewers(as_caller):
    as_caller(REVIEWER)
    r = config_api.add_reviewer(OUTSIDER)
    assert r["success"] is False
    assert "controller-only" in r["error"]


def test_a_removed_reviewer_loses_the_right(as_caller):
    as_caller("controller", controller=True)
    config_api.add_reviewer(REVIEWER)
    config_api.remove_reviewer(REVIEWER)
    as_caller(REVIEWER)
    assert config_api.is_reviewer(REVIEWER) is False


def test_a_non_reviewer_cannot_decide(as_caller, monkeypatch):
    license_for(DEV, as_caller)
    publish_extension(DEV)

    as_caller(OUTSIDER)
    registry = FakeRegistry()
    r = run_review(
        monkeypatch,
        registry,
        caller=OUTSIDER,
        item_kind="ext",
        item_id="voting",
        approve=True,
        notes="",
    )
    assert r["success"] is False
    assert "reviewers only" in r["error"]
    assert registry.calls == [], "the registry must not be touched by a stranger"


def test_a_non_reviewer_cannot_flip_the_status_directly(as_caller):
    license_for(DEV, as_caller)
    publish_extension(DEV)
    as_caller(OUTSIDER)
    r = verification_api.set_verification_status(
        item_kind="ext", item_id="voting", status="verified", notes=""
    )
    assert r["success"] is False
    assert "reviewers only" in r["error"]


# ---------------------------------------------------------------------------
# 4. An approval only counts once the registry accepts it
# ---------------------------------------------------------------------------


def test_approving_stamps_the_namespace_in_the_registry(as_caller, monkeypatch):
    license_for(DEV, as_caller)
    publish_extension(DEV)

    as_caller("controller", controller=True)
    config_api.add_reviewer(REVIEWER)
    as_caller(REVIEWER)

    registry = FakeRegistry()
    r = run_review(
        monkeypatch,
        registry,
        caller=REVIEWER,
        item_kind="ext",
        item_id="voting",
        approve=True,
        notes="reviewed the diff",
    )

    assert r["success"] is True, r
    assert r["namespace"] == "ext/voting/1.0.0"
    assert len(registry.calls) == 1
    sent = registry.calls[0]
    assert '"namespace": "ext/voting/1.0.0"' in sent
    assert '"status": "approved"' in sent
    assert "reviewed the diff" in sent
    assert status_of("voting")["verification_status"] == "verified"


def test_rejecting_records_a_rejection(as_caller, monkeypatch):
    license_for(DEV, as_caller)
    publish_extension(DEV)

    as_caller(REVIEWER, controller=True)
    registry = FakeRegistry()
    r = run_review(
        monkeypatch,
        registry,
        caller=REVIEWER,
        item_kind="ext",
        item_id="voting",
        approve=False,
        notes="calls out to a private API",
    )

    assert r["success"] is True, r
    assert '"status": "rejected"' in registry.calls[0]
    assert status_of("voting")["verification_status"] == "rejected"


def test_a_registry_refusal_leaves_the_listing_unapproved(as_caller, monkeypatch):
    # The failure a user can act on is "the approval did not go through". A
    # marketplace claiming verified while realms refuse the package is not.
    license_for(DEV, as_caller)
    publish_extension(DEV)

    as_caller(REVIEWER, controller=True)
    registry = FakeRegistry(response='{"error": "Unauthorized: approver rights required"}')
    r = run_review(
        monkeypatch,
        registry,
        caller=REVIEWER,
        item_kind="ext",
        item_id="voting",
        approve=True,
        notes="",
    )

    assert r["success"] is False
    assert "approver rights" in r["error"]
    assert status_of("voting")["verification_status"] == "pending_review"


def test_a_listing_without_a_namespace_cannot_be_approved(as_caller, monkeypatch):
    license_for(DEV, as_caller)
    publish_extension(DEV, namespace="")

    as_caller(REVIEWER, controller=True)
    registry = FakeRegistry()
    r = run_review(
        monkeypatch,
        registry,
        caller=REVIEWER,
        item_kind="ext",
        item_id="voting",
        approve=True,
        notes="",
    )
    assert r["success"] is False
    assert "nothing to approve" in r["error"]
    assert registry.calls == []


def test_the_review_queue_shows_submissions(as_caller):
    license_for(DEV, as_caller)
    publish_extension(DEV)

    as_caller(OUTSIDER)
    assert verification_api.list_pending_audits() == []

    as_caller(REVIEWER, controller=True)
    queue = verification_api.list_pending_audits()
    assert [row["item_id"] for row in queue] == ["voting"]
