"""Registration code entity and helpers for invite-based realm signup."""

import hashlib
import string
import time
from datetime import datetime, timedelta


def _random_code(length: int = 16) -> str:
    """Generate a plaintext invite code (IC-safe — secrets.choice unavailable in WASM)."""
    from core.random import generate_unique_id

    alphabet = string.ascii_letters + string.digits
    seed = generate_unique_id(length=32)
    return "".join(alphabet[int(seed[i : i + 2], 16) % len(alphabet)] for i in range(0, length * 2, 2))


def _now_ts() -> int:
    """Current Unix timestamp in whole seconds, canister-safe.

    ``time.time()`` always returns 0.0 in the Kybra/WASM runtime.  The IC
    exposes a nanosecond-resolution clock via ``ic.time()`` (from kybra).
    We convert nanoseconds → seconds here.  Falls back to ``time.time()``
    (for local/test runs where kybra is unavailable) and ultimately to a
    hard-coded far-future sentinel so codes are not immediately expired in
    non-IC environments.
    """
    try:
        from kybra import ic as _ic  # noqa: PLC0415
        t = _ic.time()
        if t and t > 0:
            return int(t) // 1_000_000_000
    except Exception:
        pass
    t = time.time()
    if t and t > 1_000_000:  # sanity-check: must be after year 1970+11days
        return int(t)
    return 9_999_999_999  # far-future sentinel → never expired in tests


def _now_dt() -> datetime:
    """Current time as a datetime, canister-safe (for ISO-string storage only).

    Do NOT use this for Unix-timestamp arithmetic; use _now_ts() instead.
    """
    return datetime.utcfromtimestamp(_now_ts())

from ic_python_db import Entity, TimestampedMixin
from ic_python_db.properties import Integer, String


class RegistrationCode(Entity, TimestampedMixin):
    """Invite code that grants a profile/role when redeemed during signup.

    Attributes:
        code_hash: SHA-256 hex digest of the plaintext code (primary key).
        code: Plaintext code — empty when the browser pre-hashes.
        user_id: Identifier of the user this code targets.
        email: Email address of the intended recipient (if known).
        expires_at: Unix timestamp after which the code is invalid.
        used: 1 when all uses are exhausted (backward compat flag).
        used_at: Unix timestamp when fully consumed.
        created_by: Principal of the admin who created the code.
        frontend_url: Base URL used to build the registration link.
        profile: Profile/role granted upon redemption.
        department: Organization (Department.name) the redeemer is added to.
            Empty for plain member/citizen codes. Enables per-department
            staff invite URLs (issue #241).
        position: Position key (``<department>/<title>``) the redeemer is
            appointed to on redemption. Empty when the invite is not bound
            to a specific seat.
        metadata: JSON blob attached to the code (issue #241). For citizen
            bulk imports (``{"kind": "citizen_import", ...}``) the single-use
            code doubles as the provisional citizen record: name, quarter,
            and imported fields are bound to the redeeming principal.
        max_uses: Maximum allowed redemptions.
        uses_count: Current redemption count.
        principals_redeemed: Comma-separated principals that redeemed.
        revoked: 0 = active, 1 = revoked.
    """

    __alias__ = "code_hash"
    __version__ = 3

    code_hash = String(max_length=128)
    code = String(max_length=64)
    user_id = String(max_length=64)
    email = String(max_length=255)
    expires_at = Integer()
    used = Integer(default=0)
    used_at = Integer()
    created_by = String(max_length=64)
    frontend_url = String(max_length=512)
    profile = String(max_length=64, default="member")
    department = String(max_length=256, default="")
    position = String(max_length=512, default="")
    metadata = String(max_length=1024, default="")
    max_uses = Integer(default=1)
    uses_count = Integer(default=0)
    principals_redeemed = String(max_length=4096, default="")
    revoked = Integer(default=0)

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        if from_version < 2:
            obj.setdefault("department", "")
            obj.setdefault("metadata", "")
        if from_version < 3:
            obj.setdefault("position", "")
        return obj

    @classmethod
    def create(
        cls,
        user_id: str,
        created_by: str,
        frontend_url: str,
        email: str = None,
        expires_in_hours: int = 24,
        code_hash: str = None,
        profile: str = "member",
        max_uses: int = 1,
        department: str = "",
        position: str = "",
        metadata: str = "",
    ) -> "RegistrationCode":
        """Create a new registration code."""
        expires_timestamp = _now_ts() + expires_in_hours * 3600

        if code_hash:
            code = ""
        else:
            code = _random_code(16)
            code_hash = hashlib.sha256(code.encode()).hexdigest()

        return cls(
            code_hash=code_hash,
            code=code,
            user_id=user_id,
            email=email or "",
            expires_at=expires_timestamp,
            used=0,
            used_at=0,
            created_by=created_by,
            frontend_url=frontend_url.rstrip("/") if frontend_url else "",
            profile=profile,
            department=department or "",
            position=position or "",
            metadata=metadata or "",
            max_uses=max_uses,
            uses_count=0,
            principals_redeemed="",
            revoked=0,
        )

    @property
    def registration_url(self) -> str:
        """Full registration URL (only meaningful when plaintext code exists)."""
        return f"{self.frontend_url}/join?invite={self.code}"

    def mark_used(self, principal: str = None):
        """Record a redemption, optionally tracking the redeeming principal."""
        self.uses_count += 1

        if principal and not self.has_principal_redeemed(principal):
            if self.principals_redeemed:
                self.principals_redeemed += "," + principal
            else:
                self.principals_redeemed = principal

        if self.uses_count >= self.max_uses:
            self.used = 1
            self.used_at = _now_ts()

    def is_valid(self) -> bool:
        """Return True when the code can still be redeemed."""
        current_timestamp = _now_ts()
        if self.revoked == 1:
            return False
        if current_timestamp >= self.expires_at:
            return False
        if self.used == 1:
            return False
        if self.uses_count >= self.max_uses:
            return False
        return True

    def has_principal_redeemed(self, principal: str) -> bool:
        """Check whether *principal* has already redeemed this code."""
        if not self.principals_redeemed:
            return False
        return principal in self.principals_redeemed.split(",")

    @classmethod
    def find_by_code_hash(cls, code_hash: str) -> "RegistrationCode":
        """Look up a code by its SHA-256 hex digest."""
        return cls[code_hash]

    @classmethod
    def find_by_code(cls, code: str) -> "RegistrationCode":
        """Look up a code by plaintext value (hashes first)."""
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        return cls[code_hash]

    @classmethod
    def find_by_user_id(cls, user_id: str) -> list["RegistrationCode"]:
        """Return all codes targeted at *user_id*."""
        return [c for c in cls.instances() if c.user_id == user_id]

    @classmethod
    def find_by_department(cls, department: str) -> list["RegistrationCode"]:
        """Return all codes linked to *department* (org staff invites)."""
        return [c for c in cls.instances() if (c.department or "") == department]


# ---------------------------------------------------------------------------
# Module-level helpers (replace the extension entry-point functions)
# ---------------------------------------------------------------------------

def create_registration_code(
    code_hash: str,
    profile: str = "member",
    max_uses: int = 1,
    expires_in_hours: int = 24,
    created_by: str = "admin",
    user_id: str = "",
    frontend_url: str = "",
    email: str = "",
    department: str = "",
    position: str = "",
    metadata: str = "",
) -> RegistrationCode:
    """Create and persist a new RegistrationCode, returning the entity."""
    return RegistrationCode.create(
        user_id=user_id,
        created_by=created_by,
        frontend_url=frontend_url,
        email=email,
        expires_in_hours=expires_in_hours,
        code_hash=code_hash,
        profile=profile,
        max_uses=max_uses,
        department=department,
        position=position,
        metadata=metadata,
    )


def validate_registration_code(code_hash_hex: str) -> dict:
    """Look up a code by its SHA-256 hex digest and report validity."""
    if not code_hash_hex:
        return {"success": False, "error": "code is required"}

    reg_code = RegistrationCode.find_by_code_hash(code_hash_hex)
    if not reg_code:
        return {"success": False, "error": "Invalid registration code"}

    if not reg_code.is_valid():
        current_timestamp = _now_ts()
        if reg_code.revoked == 1:
            reason = "revoked"
        elif reg_code.expires_at <= current_timestamp:
            reason = "expired"
        else:
            reason = "already used"
        return {"success": False, "error": f"Registration code is {reason}"}

    return {
        "success": True,
        "data": {
            "valid": True,
            "profile": reg_code.profile,
            "department": reg_code.department or "",
            "position": reg_code.position or "",
            "expires_at": datetime.fromtimestamp(reg_code.expires_at).isoformat(),
            "user_id": reg_code.user_id,
            "email": reg_code.email,
        },
    }


def consume_registration_code(code_hash_hex: str, principal: str) -> dict:
    """Validate, consume, and return the granted profile."""
    if not code_hash_hex:
        return {"success": False, "error": "code is required"}
    if not principal:
        return {"success": False, "error": "principal is required"}

    reg_code = RegistrationCode.find_by_code_hash(code_hash_hex)

    if not reg_code:
        return {"success": False, "error": "Invalid registration code"}

    if not reg_code.is_valid():
        return {"success": False, "error": "Registration code is no longer valid"}

    if reg_code.has_principal_redeemed(principal):
        return {"success": False, "error": "Already redeemed by this principal"}

    reg_code.mark_used(principal)

    return {
        "success": True,
        "data": {
            "profile": reg_code.profile,
            "department": reg_code.department or "",
            "position": reg_code.position or "",
            "user_id": reg_code.user_id or "",
            "metadata": reg_code.metadata or "",
        },
    }


def revoke_registration_code(code: str = None, code_hash: str = None) -> dict:
    """Revoke a code by plaintext or hash."""
    if not code and not code_hash:
        return {"success": False, "error": "code or code_hash is required"}

    if code and not code_hash:
        code_hash = hashlib.sha256(code.encode()).hexdigest()

    reg_code = RegistrationCode.find_by_code_hash(code_hash)
    if not reg_code:
        return {"success": False, "error": "Registration code not found"}

    reg_code.revoked = 1

    return {
        "success": True,
        "data": {
            "code_hash": code_hash[:8],
            "revoked": True,
        },
    }


def list_registration_codes(include_used: bool = False) -> list[dict]:
    """Return a list of code summaries (never exposes plaintext)."""
    codes = RegistrationCode.instances()

    if not include_used:
        codes = [c for c in codes if c.used == 0]

    return [
        {
            "code_hash": c.code_hash[:8],
            "user_id": c.user_id,
            "email": c.email,
            "profile": c.profile,
            "department": c.department or "",
            "position": c.position or "",
            "expires_at": datetime.fromtimestamp(c.expires_at).isoformat(),
            "uses_count": c.uses_count,
            "max_uses": c.max_uses,
            "revoked": c.revoked == 1,
            "is_valid": c.is_valid(),
            "created_by": c.created_by,
        }
        for c in codes
    ]
