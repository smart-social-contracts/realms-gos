"""Stable error codes for user-facing host APIs (issue #393).

Do **not** translate these messages in WASM. The UI maps ``error_code``; the
English ``error`` string is for logs and CLI.

Candid ``RealmResponseData`` is a variant (one arm), so join/setup Candid
failures encode the code in the existing ``error`` text:

    [anonymous_cannot_join] Anonymous principal cannot join a realm — sign in first

JSON APIs (extension_sync_call, setup wizard) use ``error_payload`` instead.

Do not use the ``re`` module: Basilisk ships only a stub without ``re.compile``.
"""

ANONYMOUS_CANNOT_JOIN = "anonymous_cannot_join"
INVITE_REQUIRED = "invite_required"
INVALID_INVITE = "invalid_invite"
INVITE_PROFILE_MISMATCH = "invite_profile_mismatch"
SETUP_NOT_OPEN = "setup_not_open"
SETUP_FORBIDDEN = "setup_forbidden"
COORDINATOR_ONLY = "coordinator_only"
QUARTER_NOT_READY = "quarter_not_ready"
EMAIL_REQUIRED = "email_required"
EMAIL_INVALID = "email_invalid"
EMAIL_NO_VERIFICATION = "email_no_verification"
EMAIL_TOO_MANY_ATTEMPTS = "email_too_many_attempts"
EMAIL_CODE_EXPIRED = "email_code_expired"
EMAIL_CODE_INCORRECT = "email_code_incorrect"


def tagged_error(code: str, message: str) -> str:
    """Encode ``error_code`` in a Candid ``error`` text without a schema change."""
    return f"[{code}] {message}"


def parse_tagged_error(text: str):
    """Return ``(code, message)`` when *text* uses :func:`tagged_error` form."""
    raw = text if isinstance(text, str) else ""
    if not raw.startswith("[") or "]" not in raw:
        return None, raw
    close = raw.find("]")
    if close < 2:
        return None, raw
    code = raw[1:close]
    if not code or not code[0].isalpha():
        return None, raw
    for ch in code:
        if not (ch.isalnum() or ch == "_"):
            return None, raw
    rest = raw[close + 1 :]
    if rest.startswith(" "):
        rest = rest[1:]
    return code, rest
