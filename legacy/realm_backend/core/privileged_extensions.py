"""Which extensions the *host* grants in-process (unsandboxed) execution to.

An extension package must never be able to decide how much privilege it gets.
``resolve_mode`` used to read ``"runtime": "in_process"`` and ``"system": true``
straight out of the installed manifest, which meant the code being sandboxed
chose whether it was sandboxed — and the only thing standing between an attacker
and full host access was the permission check on installing an extension.

Privilege is granted here instead, in host code that ships with the canister.
A manifest may still *request* in-process execution; the request is honoured only
for an id on one of these lists, and otherwise falls through to the sandbox.

Two lists, with different meanings:

* ``CORE_EXTENSION_IDS`` (``core.core_extensions``) — platform-plane extensions
  that administer the realm rather than run on top of it.
* ``IN_PROCESS_EXEMPT_EXTENSION_IDS`` — non-core extensions not yet ported to the
  capability bridge. This is tracked debt and may only shrink; it mirrors
  ``scripts/sandbox_exemptions.json``, and ``tests/backend/test_sandbox_exemption_ratchet.py``
  fails if the two drift apart.
"""

from typing import FrozenSet

# Mirrors the "exempt" block of scripts/sandbox_exemptions.json. Keep in sync;
# the ratchet test enforces it.
IN_PROCESS_EXEMPT_EXTENSION_IDS: FrozenSet[str] = frozenset(
    {
        "demo_simulator",
        "passport_verification",
    }
)


def is_in_process_exempt(ext_id: str) -> bool:
    """True for a non-core extension the host still allows to run in-process."""
    return ext_id in IN_PROCESS_EXEMPT_EXTENSION_IDS


def may_run_in_process(ext_id: str) -> bool:
    """True when the host grants ``ext_id`` unsandboxed execution."""
    try:
        from core.core_extensions import is_core_extension

        if is_core_extension(ext_id):
            return True
    except Exception:
        pass
    return is_in_process_exempt(ext_id)
