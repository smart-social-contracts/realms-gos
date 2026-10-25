import json

from ic_python_db import Entity, Float, ManyToOne, OneToMany, String, TimestampedMixin
from ic_python_logging import get_logger

logger = get_logger("entity.proposal")


class Proposal(Entity, TimestampedMixin):
    __owner_field__ = "proposer"  # realms#282 — SecureORM ownership stamp/protect
    """Governance proposal entity for voting system.

    v2: ``org_scope`` promoted from the metadata JSON blob to an indexed
    field, and ``status`` indexed, so extensions can filter proposals by
    organization/status via ``Proposal.find_by`` instead of scanning the
    full ID range (ic-python-db#11). Entities written before the indexes
    existed are backfilled by a timer chain kicked off in initialize().
    """

    __version__ = 2
    __alias__ = "proposal_id"
    proposal_id = String(max_length=64)
    title = String(max_length=256)
    description = String(max_length=2048)
    code_url = String(max_length=512)
    code_checksum = String(max_length=128)
    proposer = ManyToOne("User", "proposals")
    # voting, pending_vote, approved, rejected, etc.
    status = String(max_length=32, indexed=True)
    voting_deadline = String(max_length=64)  # ISO format timestamp or None
    votes_yes = Float()
    votes_no = Float()
    votes_abstain = Float()
    total_voters = Float()
    required_threshold = Float()
    metadata = String(max_length=4096)
    # Governing organization for org-scoped ballots. None/"" = realm-wide
    # (kept unset so realm-wide proposals stay out of the index; filter
    # them by omitting the org filter instead).
    org_scope = String(max_length=128, indexed=True)
    votes = OneToMany("Vote", "proposal")
    budgets = OneToMany("Budget", "proposal")

    @classmethod
    def migrate(cls, obj: dict, from_version: int, to_version: int) -> dict:
        if from_version == 1 and to_version >= 2:
            try:
                meta = json.loads(obj.get("metadata") or "{}")
            except Exception:
                meta = {}
            scope = ""
            if isinstance(meta, dict):
                scope = (meta.get("org_scope") or "").strip()
            obj["org_scope"] = scope or None
        return obj

    def tally(self) -> dict:
        """Count votes from linked Vote entities and update tally fields.

        Returns dict with yes, no, abstain counts and total.
        """
        yes = no = abstain = 0
        for vote in self.votes:
            choice = (vote.vote_choice or "").lower()
            if choice == "yes":
                yes += 1
            elif choice == "no":
                no += 1
            elif choice == "abstain":
                abstain += 1
        self.votes_yes = float(yes)
        self.votes_no = float(no)
        self.votes_abstain = float(abstain)
        self.total_voters = float(yes + no + abstain)
        logger.info(f"Proposal {self.proposal_id} tallied: yes={yes} no={no} abstain={abstain}")
        return {"yes": yes, "no": no, "abstain": abstain, "total": yes + no + abstain}

    def is_quorum_met(self, active_member_count: int, quorum_percent: float) -> bool:
        """Check if enough members voted to meet quorum.

        Args:
            active_member_count: Total number of active members in the realm.
            quorum_percent: Minimum percentage of active members that must vote (0-100).
        """
        if active_member_count <= 0:
            return False
        total = self.total_voters or 0
        return (total / active_member_count) * 100 >= quorum_percent

    def is_approved(self) -> bool:
        """Check if yes votes exceed the required threshold.

        Threshold is compared against votes cast (yes + no), abstains excluded.
        """
        threshold = self.required_threshold or 0.5
        yes = self.votes_yes or 0
        no = self.votes_no or 0
        votes_cast = yes + no
        if votes_cast == 0:
            return False
        return (yes / votes_cast) > threshold

    def resolve(self, active_member_count: int, quorum_percent: float) -> str:
        """Tally votes and resolve proposal status.

        Args:
            active_member_count: Total active members for quorum check.
            quorum_percent: Required quorum percentage (0-100).

        Returns:
            New status string: 'approved', 'rejected', or 'no_quorum'.
        """
        self.tally()
        if not self.is_quorum_met(active_member_count, quorum_percent):
            self.status = "no_quorum"
        elif self.is_approved():
            self.status = "approved"
        else:
            self.status = "rejected"
        logger.info(f"Proposal {self.proposal_id} resolved: {self.status}")
        return self.status
