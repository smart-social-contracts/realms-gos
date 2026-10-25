"""Lifecycle readiness checklist and alpha→beta hard gate (issue #241).

For incumbent migrations all preparation happens during alpha (creator is
root). The codex manifest can declare the alpha→beta transition as
``{"mode": "checklist"}``, in which case the transition is *blocked* until
every readiness milestone passes. The same checklist feeds the migration
console UI, so creators see exactly what stands between them and beta.

Milestones (computed live from GGG entities, no cached flags):
  - departments seeded (all template orgs exist)
  - departments staffed (every open Position has a holder; falls back to
    "every org has members" when the codex defines no positions)
  - budgets linked (every org has a fund envelope)
  - citizens imported (population reached the codex target)
  - treasury configured (token canister linked)
  - zones defined
  - required extensions installed
  - root handed over (root org has members beyond the creator)
"""

import json

from ic_python_logging import get_logger

logger = get_logger("core.lifecycle_gate")


def _manifest(realm) -> dict:
    """Realm configuration through the codex hook API (issue #244).

    ``codex_hooks.get_config`` merges the active codex's declared config
    over ``Realm.manifest_data``, so this module never digs into raw
    manifests. Falls back to ``manifest_data`` alone if the hook layer is
    unavailable (e.g. minimal test harness).
    """
    try:
        from core.codex_hooks import get_config

        return get_config()
    except Exception:
        pass
    try:
        data = json.loads(realm.manifest_data or "{}")
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def transition_mode(realm, from_stage: str, to_stage: str) -> str:
    """Return the codex-declared mode for a lifecycle transition.

    E.g. ``lifecycle.transitions.alpha_to_beta.mode`` — "admin_approval"
    (default) or "checklist" (hard gate).
    """
    lifecycle = _manifest(realm).get("lifecycle", {}) or {}
    transitions = lifecycle.get("transitions", {}) or {}
    entry = transitions.get(f"{from_stage}_to_{to_stage}", {}) or {}
    return str(entry.get("mode", "admin_approval") or "admin_approval")


def readiness_checklist(realm) -> list:
    """Live milestones toward the alpha→beta gate.

    Each item: ``{id, label, done, detail}``.
    """
    from ggg import ROOT_ORG_NAME, Department, User

    config = _manifest(realm)
    items = []

    depts = list(Department.instances())
    non_root = [
        d
        for d in depts
        if not (getattr(d, "is_root", False) or d.name == ROOT_ORG_NAME)
    ]
    expected = config.get("departments", []) or []
    items.append(
        {
            "id": "departments_seeded",
            "label": "Departments seeded",
            "done": len(non_root) > 0 and len(non_root) >= len(expected),
            "detail": f"{len(non_root)} departments (template lists {len(expected)})",
        }
    )

    def _member_count(dept) -> int:
        from core.membership import department_member_count

        return department_member_count(dept)

    # Staffing: explicit Position seats when the codex seeded them (each open
    # position needs >=1 active appointment), member-count heuristic otherwise.
    open_positions = []
    try:
        from ggg import Position, PositionStatus

        open_positions = [
            p
            for p in Position.instances()
            if (p.status or PositionStatus.OPEN) == PositionStatus.OPEN
        ]
    except Exception:
        open_positions = []

    if open_positions:
        staffed_positions = [p for p in open_positions if p.filled_count() > 0]
        items.append(
            {
                "id": "departments_staffed",
                "label": "Civil servants onboarded",
                "done": len(staffed_positions) == len(open_positions),
                "detail": f"{len(staffed_positions)} of {len(open_positions)} positions staffed",
            }
        )
    else:
        staffed = [d for d in non_root if _member_count(d) > 0]
        items.append(
            {
                "id": "departments_staffed",
                "label": "Civil servants onboarded",
                "done": len(non_root) > 0 and len(staffed) == len(non_root),
                "detail": f"{len(staffed)} of {len(non_root)} departments have members",
            }
        )

    budgets = [d for d in non_root if getattr(d, "fund", None)]
    items.append(
        {
            "id": "budgets_linked",
            "label": "Department budgets linked",
            "done": len(non_root) > 0 and len(budgets) == len(non_root),
            "detail": f"{len(budgets)} of {len(non_root)} departments have a fund",
        }
    )

    population = User.count()
    target = int((config.get("lifecycle", {}) or {}).get("population_target", 0) or 0)
    items.append(
        {
            "id": "citizens_imported",
            "label": "Citizens imported",
            "done": target > 0 and population >= target,
            "detail": f"{population} members (target {target})",
        }
    )

    currency = ""
    try:
        from core.realm_currency import realm_currency

        currency = realm_currency()
    except Exception:
        currency = (getattr(realm, "accounting_currency", "") or "").strip()
    items.append(
        {
            "id": "treasury_configured",
            "label": "Currency / treasury configured",
            "done": bool(currency),
            "detail": f"treasury token: {currency or 'not resolved'}",
        }
    )

    try:
        from ggg import Zone

        zone_count = Zone.count()
    except Exception:
        zone_count = 0
    items.append(
        {
            "id": "zones_defined",
            "label": "Geographic zones defined",
            "done": zone_count > 0,
            "detail": f"{zone_count} zones",
        }
    )

    # Dependencies may be a list (latest) or a {name: version_pin} dict
    # (issue #242); either way we only need the extension names here.
    raw_deps = config.get("dependencies", []) or []
    deps = list(raw_deps.keys()) if isinstance(raw_deps, dict) else list(raw_deps)
    try:
        from core.runtime_extensions import list_installed

        installed = set(list_installed())
    except Exception:
        installed = set()
    missing = [d for d in deps if d not in installed]
    items.append(
        {
            "id": "extensions_installed",
            "label": "Required extensions installed",
            "done": len(deps) > 0 and not missing,
            "detail": f"missing: {', '.join(missing) if missing else 'none'}",
        }
    )

    # Root handover: the root org has members beyond the realm creator, i.e.
    # governance has been transferred to the top authority (usually congress).
    root = next(
        (d for d in depts if getattr(d, "is_root", False) or d.name == ROOT_ORG_NAME),
        None,
    )
    root_members = _member_count(root) if root else 0
    items.append(
        {
            "id": "root_handover",
            "label": "Root handed to governance authority",
            "done": root_members > 1,
            "detail": f"root department has {root_members} member(s)",
        }
    )

    return items


def auto_milestones_ready(realm, from_stage: str = "alpha", to_stage: str = "beta"):
    """Return ``(ready, missing_labels)`` for an ``auto_milestones`` transition.

    The codex declares the transition as
    ``{"mode": "auto_milestones", "milestones": [...]}``. Known milestone ids:

      - ``critical_mass`` — population reached ``lifecycle.critical_mass``
        (or ``lifecycle.population_target`` as fallback).
      - anything else — a truthy boolean flag of the same name in the
        ``lifecycle`` config block (e.g. ``land_acquired``).
    """
    from ggg import User

    config = _manifest(realm)
    lifecycle = config.get("lifecycle", {}) or {}
    transitions = lifecycle.get("transitions", {}) or {}
    entry = transitions.get(f"{from_stage}_to_{to_stage}", {}) or {}
    milestones = entry.get("milestones") or []

    missing = []
    for milestone in milestones:
        if milestone == "critical_mass":
            target = int(
                lifecycle.get("critical_mass")
                or lifecycle.get("population_target")
                or 0
            )
            population = User.count()
            if target > 0 and population < target:
                missing.append(
                    f"Critical mass not reached ({population} of {target} citizens)"
                )
        elif not lifecycle.get(milestone):
            missing.append(f"Milestone '{milestone}' not met")
    return (not missing, missing)


def alpha_to_beta_ready(realm):
    """Return ``(ready, missing_labels)`` for the alpha→beta hard gate.

    A codex implementing the ``check_lifecycle_transition`` hook (issue
    #244) is authoritative; the built-in readiness checklist is the
    default for codices that only declare a checklist mode.
    """
    try:
        from core.codex_hooks import check_lifecycle_transition

        verdict = check_lifecycle_transition("alpha", "beta")
        if verdict is not None:
            return (verdict["allowed"], verdict["missing"])
    except Exception as e:
        logger.warning(f"codex check_lifecycle_transition failed, using checklist: {e}")

    checklist = readiness_checklist(realm)
    missing = [i["label"] for i in checklist if not i["done"]]
    return (not missing, missing)


def _now_seconds() -> int:
    try:
        from _cdk import ic

        t = int(ic.time())
        if t > 0:
            return t // 1_000_000_000
    except Exception:
        pass
    import time

    return int(time.time())


def _stage_entered_at(realm, stage: str) -> int:
    """Unix seconds when the realm entered *stage* (0 if unknown).

    Read from the timestamped lifecycle history that ``set_realm_stage``
    appends to ``manifest_data``.
    """
    try:
        raw = json.loads(getattr(realm, "manifest_data", "{}") or "{}")
        history = (raw.get("lifecycle", {}) or {}).get("history", []) or []
    except (json.JSONDecodeError, TypeError):
        return 0
    for entry in reversed(history):
        if isinstance(entry, dict) and entry.get("stage") == stage:
            return int(entry.get("at") or 0)
    return 0


def beta_to_production_ready(realm):
    """Return ``(ready, missing_labels)`` for the beta→production gate.

    Two conditions (issue #253):

    1. **Proving period** — ``lifecycle.beta_proving_days`` must have elapsed
       since the realm entered beta (0/absent disables the requirement).
    2. **Root handover** — the root org has members beyond the creator.

    The governance vote itself is no longer checked here: ``set_realm_stage``
    is a governed action (issue #262), so when the root policy is not 1/1
    the transition only executes as the replay of an accepted root-scoped
    proposal. This gate runs again at replay time, so the proving period
    cannot be bypassed by voting early.
    """
    from ggg import ROOT_ORG_NAME, Department

    missing = []
    config = _manifest(realm)
    lifecycle = config.get("lifecycle", {}) or {}

    try:
        from core.realm_currency import realm_currency

        if not realm_currency():
            missing.append(
                "Treasury currency not resolved — configure the treasury ledger"
            )
    except Exception:
        missing.append(
            "Treasury currency not resolved — configure the treasury ledger"
        )

    proving_days = float(lifecycle.get("beta_proving_days") or 0)
    if proving_days > 0:
        entered = _stage_entered_at(realm, "beta")
        if entered <= 0:
            missing.append(
                f"Proving period unknown: no timestamped beta entry in history "
                f"(requires {proving_days} days in beta)"
            )
        else:
            elapsed_days = (_now_seconds() - entered) / 86400.0
            if elapsed_days < proving_days:
                missing.append(
                    f"Proving period not elapsed ({elapsed_days:.2f} of "
                    f"{proving_days} days in beta)"
                )

    root = next(
        (
            d
            for d in Department.instances()
            if getattr(d, "is_root", False) or d.name == ROOT_ORG_NAME
        ),
        None,
    )
    from core.membership import department_member_count

    if root is None or department_member_count(root) < 1:
        missing.append("Root not handed to governance authority")

    return (not missing, missing)
