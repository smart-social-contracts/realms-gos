from ic_python_db import Entity, OneToOne, String, TimestampedMixin
from ic_python_logging import get_logger

logger = get_logger("entity.member")


class Member(Entity, TimestampedMixin):
    __alias__ = "id"
    id = String()
    user = OneToOne("User", "member")
    judge = OneToOne("Judge", "member")

    residence_permit: str
    tax_compliance: str
    identity_verification: str
    voting_eligibility: str
    public_benefits_eligibility: str
    criminal_record: str

    def is_active(self) -> bool:
        """Check if this member is active (identity verified)."""
        return self.identity_verification == "verified"

    def activate(self) -> None:
        """Activate this member — set identity as verified and enable voting/benefits."""
        self.identity_verification = "verified"
        self.voting_eligibility = "eligible"
        self.public_benefits_eligibility = "eligible"
        logger.info(f"Member {self.id} activated")

    def deactivate(self, reason: str = "suspended") -> None:
        """Deactivate this member — suspend identity and revoke voting/benefits."""
        self.identity_verification = reason
        self.voting_eligibility = "ineligible"
        self.public_benefits_eligibility = "ineligible"
        logger.info(f"Member {self.id} deactivated: {reason}")

    def reactivate(self) -> None:
        """Reactivate a previously deactivated member."""
        self.activate()
        logger.info(f"Member {self.id} reactivated")

    @classmethod
    def for_user(cls, user_id: str) -> "Member":
        """Find the Member record linked to a user ID. Returns Member or None."""
        for member in cls.instances():
            if member.user and member.user.id == user_id:
                return member
        return None

    @classmethod
    def count_active(cls) -> int:
        """Count all active members."""
        return sum(1 for m in cls.instances() if m.identity_verification == "verified")
