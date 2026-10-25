"""Join-target assignment helpers (issue #156).

Pure stdlib only so the policy is unit-testable without a replica.
"""

JOIN_QUARTER_NOT_READY = (
    "This quarter is still setting up. Please try again in a moment."
)


def is_joinable_status(status) -> bool:
    """True only when a quarter catalog row may accept new joins."""
    return (status or "").strip() == "active"


def catalog_status_for_self(is_quarter, dashboard_installed) -> str:
    """Catalog status for this canister's own directory row."""
    if is_quarter and not dashboard_installed:
        return "setup"
    return "active"


def should_activate_quarter(current_status, join_ready) -> bool:
    """True when a setup quarter should be promoted to active."""
    current = (current_status or "").strip() or "setup"
    return current == "setup" and bool(join_ready)


def is_dashboard_installed(installed_ids, resolved_id="member_dashboard") -> bool:
    """True if member_dashboard or its codex override is in the installed set."""
    ids = {str(x) for x in (installed_ids or []) if x}
    if resolved_id and resolved_id in ids:
        return True
    return "member_dashboard" in ids


def pick_default_join_quarter(active_subs, capital_id: str = "") -> str:
    """Pick the system-assigned join target among joinable sub-quarters.

    Least-populated wins; ties break toward the highest catalog index so a
    freshly minted empty quarter is preferred over older empty peers
    (issue #156 join-assignment addendum).
    """
    if not active_subs:
        return capital_id or ""
    least = min(
        active_subs,
        key=lambda q: (int(q.get("population") or 0), -int(q.get("index") or 0)),
    )
    return least.get("canister_id") or capital_id or ""
