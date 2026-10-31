"""Quarter-local codex sync driven by the ic_basilisk_toolkit TaskManager (issue #295).

When the capital proposes a codex upgrade and the quarter's members approve,
the approved replay writes an install *plan* onto ``Realm.sync_state`` (separate
from first-boot ``bootstrap_state``) and seeds a recurring ``TaskManager`` task
that installs **one item per tick** (``advance_sync``).

Heavy logic lives here (native + unit-testable). The ``TaskManager`` step is a
tiny, stable codex shim that only calls ``advance_sync()`` — same split as
``core.quarter_bootstrap``.
"""

import json

from ic_python_logging import get_logger

logger = get_logger("core.quarter_sync")

# Deliberately NOT BOOTSTRAP_TASK_NAME: re-seeding that task resets cursor/done/
# failed (first-boot recovery), which would erase an incremental sync's history.
SYNC_TASK_NAME = "quarter_codex_sync"

# Matches ``Realm.sync_state`` String(max_length=...) in ggg/governance/realm.py.
SYNC_STATE_MAX_LENGTH = 8192

# Proposal.metadata cap — code_inline is stored inside this JSON blob.
PROPOSAL_METADATA_MAX_LENGTH = 4096

# Stable codex shim run by the recurring TaskManager step. Kept intentionally
# tiny: all real work is the native advance_sync() generator below. The
# presence of "yield"/"async_task" marks the step async for the framework.
SYNC_STEP_CODE = (
    "def async_task():\n"
    "    from core.quarter_sync import advance_sync\n"
    "    res = yield from advance_sync()\n"
    "    return res\n"
)


# ── Pure delta derivation + plan construction (unit-testable, no canister) ───


def derive_sync_delta(target, current):
    """Return the codex item needed to reach *target*, or ``None`` when already
    aligned.

    ``target`` / ``current`` are ``{codex_id, version}`` dicts. ``current`` may
    be empty/None when the quarter has no codex installed yet.

    Raises ``ValueError`` when the capital's codex id differs from the quarter's
    — a cross-identity sync is refused at install time (singleton rule) and must
    not produce a doomed ballot.
    """
    target = target or {}
    current = current or {}

    target_id = (target.get("codex_id") or "").strip()
    if not target_id:
        raise ValueError("target codex_id is required")

    target_version = target.get("version")
    current_id = (current.get("codex_id") or "").strip()
    current_version = current.get("version")

    if current_id and current_id != target_id:
        raise ValueError(
            f"Cannot sync codex identity: quarter has '{current_id}' but capital "
            f"targets '{target_id}'. Only version upgrades of the same codex id "
            f"are permitted (one codex per realm)."
        )

    if current_id == target_id and current_version == target_version:
        return None

    return {
        "codex_id": target_id,
        "version": target_version,
        "run_init": True,
    }


def build_sync_plan(spec):
    """Turn a sync spec into an ordered install plan + cursor state.

    Reuses ``build_bootstrap_plan`` for the cursor/items shape. The plan carries
    only the codex item — ``install_codex_from_registry`` resolves and installs
    the dependency closure at install time.

    ``spec`` keys:
        registry_canister_id, frontend_canister_id, parent_realm_canister_id,
        target ({codex_id, version}), current ({codex_id, version} | None),
        proposal_id (optional — recorded on the plan for operator visibility).
    """
    from core.quarter_bootstrap import build_bootstrap_plan

    spec = spec or {}
    delta = derive_sync_delta(spec.get("target"), spec.get("current"))

    plan_spec = {
        "parent_realm_canister_id": spec.get("parent_realm_canister_id"),
        "registry_canister_id": spec.get("registry_canister_id"),
        "frontend_canister_id": spec.get("frontend_canister_id"),
    }
    if delta is not None:
        plan_spec["codex"] = delta
    plan = build_bootstrap_plan(plan_spec)

    plan["origin"] = "sync"
    proposal_id = (spec.get("proposal_id") or "").strip()
    if proposal_id:
        plan["proposal_id"] = proposal_id
    return plan


# ── Realm-persisted sync state helpers ──────────────────────────────────────


def load_sync_state(realm):
    """Read the JSON sync plan persisted on ``realm`` (or None)."""
    raw = getattr(realm, "sync_state", "") or ""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def save_sync_state(realm, state):
    """Persist the JSON sync plan onto ``realm``."""
    from core.quarter_bootstrap import bound_state

    realm.sync_state = json.dumps(
        bound_state(state, SYNC_STATE_MAX_LENGTH), separators=(",", ":")
    )


def _persist_sync_state(realm, state):
    """Best-effort save: a persistence failure must never kill the sync tick."""
    try:
        save_sync_state(realm, state)
        return True
    except Exception as e:
        logger.error(f"save_sync_state failed ({e}); retrying with minimal state")
        try:
            minimal = {
                "parent": state.get("parent"),
                "registry": state.get("registry"),
                "frontend": state.get("frontend"),
                "items": state.get("items"),
                "cursor": state.get("cursor"),
                "attempts": state.get("attempts"),
                "done": state.get("done"),
                "status": state.get("status"),
                "failed": [],
                "failed_overflow": len(state.get("failed") or []),
                "origin": state.get("origin"),
                "proposal_id": state.get("proposal_id"),
            }
            save_sync_state(realm, minimal)
            return True
        except Exception as e2:
            logger.error(f"save_sync_state minimal retry also failed: {e2}")
            return False


def plan_in_flight(realm):
    """True when a sync plan exists whose ``status`` is not ``complete``."""
    state = load_sync_state(realm)
    if not state:
        return False
    return (state.get("status") or "") != "complete"


# ── Canister runtime: install one item per tick ─────────────────────────────


_sync_tick_in_flight = False


def advance_sync():
    """Install the next pending sync-plan item, one per call (generator).

    Invoked repeatedly by the recurring TaskManager task. Returns a JSON-able
    dict describing this tick. When the plan is exhausted it disables the
    recurring schedule so the task stops firing.
    """
    global _sync_tick_in_flight

    try:
        from ggg import Realm
    except ImportError:
        from realm_backend.ggg import Realm  # test/module layout

    from core.quarter_bootstrap import (
        disable_recurring_task,
        step_plan,
        _install_item,
    )

    if _sync_tick_in_flight:
        return {
            "success": True,
            "status": "busy",
            "message": "previous tick still installing",
        }

    realm = Realm.load("1")
    if not realm:
        return {"success": False, "error": "Realm not found"}

    state = load_sync_state(realm)
    if not state:
        disable_recurring_task(SYNC_TASK_NAME)
        return {"success": True, "status": "idle", "message": "no sync plan"}

    items = state.get("items") or []
    cursor = int(state.get("cursor") or 0)
    if state.get("status") == "complete" or cursor >= len(items):
        state["status"] = "complete"
        _persist_sync_state(realm, state)
        disable_recurring_task(SYNC_TASK_NAME)
        return {
            "success": True,
            "status": "complete",
            "done": state.get("done", []),
            "failed": state.get("failed", []),
        }

    item = items[cursor]
    ok = False
    result = None
    _sync_tick_in_flight = True
    try:
        try:
            result = yield from _install_item(state, item)
            ok = not (isinstance(result, dict) and result.get("success") is False)
        except Exception as e:
            logger.error(f"advance_sync install of {item.get('id')} failed: {e}")
            result = {"success": False, "error": str(e)}
            ok = False
    finally:
        _sync_tick_in_flight = False

    # Reload across the async boundary, then record progress — but only if the
    # plan still points at the item we actually installed (a concurrent driver
    # or re-seed may have moved the cursor; stepping then would mark a
    # never-installed item as done).
    realm = Realm.load("1")
    state = load_sync_state(realm) or state
    fresh_items = state.get("items") or []
    fresh_cursor = int(state.get("cursor") or 0)
    fresh_item = fresh_items[fresh_cursor] if fresh_cursor < len(fresh_items) else None
    if fresh_item is None or fresh_item.get("id") != item.get("id"):
        logger.warning(
            f"advance_sync: plan moved under us (installed {item.get('id')!r}, "
            f"cursor now at {fresh_item.get('id') if fresh_item else 'end'!r}); not stepping"
        )
        return {
            "success": ok,
            "status": state.get("status"),
            "cursor": fresh_cursor,
            "item": item.get("id"),
            "result": result,
        }
    error = (
        None
        if ok
        else (result.get("error") if isinstance(result, dict) else str(result))
    )
    state = step_plan(state, ok, error=error)
    _persist_sync_state(realm, state)

    if state.get("status") == "complete":
        disable_recurring_task(SYNC_TASK_NAME)
        logger.info(
            f"Quarter codex sync complete: done={state.get('done')} failed={state.get('failed')}"
        )

    return {
        "success": ok,
        "status": state.get("status"),
        "cursor": int(state.get("cursor") or 0),
        "item": item.get("id"),
        "result": result,
    }


# ── Proposal replay target + ballot assembly ────────────────────────────────


def apply_sync_plan(payload):
    """Write an approved sync plan to ``sync_state`` and seed the install task.

    Called by proposal replay — must return promptly; installation happens on
    the tick engine (``advance_sync``), which retries per item and bounds state.
    """
    from core.quarter_bootstrap import BOOTSTRAP_INTERVAL_S, seed_recurring_codex_task

    try:
        from ggg import Realm
    except ImportError:
        from realm_backend.ggg import Realm

    realm = Realm.load("1")
    if not realm:
        return {"success": False, "error": "Realm not found"}

    plan = payload if isinstance(payload, dict) else json.loads(payload)
    save_sync_state(realm, plan)
    seed_recurring_codex_task(
        SYNC_TASK_NAME, SYNC_STEP_CODE, BOOTSTRAP_INTERVAL_S
    )
    return {
        "success": True,
        "status": plan.get("status"),
        "items": len(plan.get("items") or []),
    }


def _format_version(version):
    v = "" if version is None else str(version).strip()
    return v or "(latest)"


def _sync_summary(target, current, registry_canister_id):
    """Human-readable one-liner: codex id, version transition, source registry."""
    target = target or {}
    current = current or {}
    codex_id = (target.get("codex_id") or "").strip()
    target_v = _format_version(target.get("version"))
    registry = (registry_canister_id or "").strip() or "(unknown registry)"
    current_id = (current.get("codex_id") or "").strip()
    if not current_id:
        return f"Install codex {codex_id} {target_v} from registry {registry}"
    current_v = _format_version(current.get("version"))
    return (
        f"Sync codex {codex_id}: {current_v} → {target_v} "
        f"from registry {registry}"
    )


def _sync_description(target, current, registry_canister_id, plan):
    """Longer ballot text listing the plan items."""
    summary = _sync_summary(target, current, registry_canister_id)
    items = plan.get("items") or []
    if not items:
        return f"{summary}. No packages to install (already aligned)."
    lines = [summary, "", "Packages to install (codex first; dependencies resolved at install time):"]
    for item in items:
        kind = item.get("kind") or "package"
        item_id = item.get("id") or "?"
        version = _format_version(item.get("version"))
        lines.append(f"  - {kind} {item_id} {version}")
    return "\n".join(lines)


def derive_quarter_current_codex():
    """Return ``{codex_id, version}`` for this quarter's installed codex.

    Mirrors ``derive_capital_install_set``'s active-codex fallback:
    ``runtime_codex.list_installed`` first, then ``codex_hooks.get_active_codex``
    plus ``runtime_extensions.get_all_extension_manifests`` for the version.
    """
    codex_id = ""
    version = None

    try:
        from core.runtime_codex import get_all_codex_manifests, list_installed

        installed = list_installed()
        manifests = get_all_codex_manifests()
        if installed:
            codex_id = (installed[0] or "").strip()
            manifest = manifests.get(codex_id) or {}
            version = (str(manifest.get("version") or "")).strip() or None
    except Exception as e:
        logger.error(f"derive_quarter_current_codex: runtime_codex read failed — {e}")

    try:
        from core.codex_hooks import get_active_codex
        from core.runtime_extensions import get_all_extension_manifests

        active = get_active_codex()
        if active:
            codex_id = active.strip()
            manifest = get_all_extension_manifests().get(codex_id) or {}
            version = (str(manifest.get("version") or "")).strip() or None
    except Exception as e:
        logger.error(f"derive_quarter_current_codex: active codex fallback failed — {e}")

    if not codex_id:
        return None
    return {"codex_id": codex_id, "version": version}


def _quarter_capital_id(realm):
    """Parent/capital canister id set by ``bootstrap_as_quarter`` / ``set_quarter_config``."""
    return (getattr(realm, "federation_realm_id", "") or "").strip()


def request_sync(caller, payload):
    """Quarter-side entry: capital requests a codex sync ballot (issue #295).

    Always creates a proposal — never consults ``gate()`` (Gap 1). The capital
    is both an IC controller and a trusted principal of its quarters; routing
    through ``gate()`` would apply the sync with no vote.
    """
    try:
        from ggg import Realm
    except ImportError:
        from realm_backend.ggg import Realm

    from core.governed_action import submit_replay_proposal

    realm = Realm.load("1")
    if not realm:
        return {"success": False, "error": "Realm not found"}

    caller = (caller or "").strip()
    capital = _quarter_capital_id(realm)
    if not capital:
        return {
            "success": False,
            "error": "This realm is not configured as a quarter (no federation_realm_id)",
        }
    if caller != capital:
        return {
            "success": False,
            "error": (
                f"Only this quarter's capital ({capital}) may request codex sync; "
                f"caller is {caller}"
            ),
        }

    if plan_in_flight(realm):
        return {"success": False, "error": "A sync plan is already in flight"}

    try:
        params = payload if isinstance(payload, dict) else json.loads(payload or "{}")
    except (json.JSONDecodeError, TypeError) as e:
        return {"success": False, "error": f"bad payload: {e}"}

    target = params.get("target") or {}
    registry = (params.get("registry_canister_id") or "").strip()
    frontend = (params.get("frontend_canister_id") or "").strip()

    current = derive_quarter_current_codex()

    metadata_extra = {
        "sync_type": "codex_sync",
        "requested_by_capital": capital,
        "target_codex_id": (target.get("codex_id") or "").strip(),
        "target_version": target.get("version"),
        "current_codex_id": (current.get("codex_id") or "").strip() if current else "",
        "current_version": current.get("version") if current else None,
    }

    try:
        proposal_pkg = build_sync_proposal(
            target,
            current,
            registry,
            frontend_canister_id=frontend,
            parent_realm_canister_id=capital,
            metadata_extra=metadata_extra,
        )
    except ValueError as e:
        return {"success": False, "error": str(e)}

    description = (
        f"Codex sync requested by capital {capital}.\n\n"
        + proposal_pkg["description"]
    )

    return submit_replay_proposal(
        None,
        proposal_pkg["summary"],
        proposal_pkg["code_inline"],
        caller,
        metadata_extra=metadata_extra,
        description=description,
        allow_system_proposer=True,
        realm_wide=True,
    )


def trigger_quarter_codex_sync(payload):
    """Capital-side replay target: derive live codex and ask a quarter to sync.

    Module-level generator so ``build_backend_replay_code`` can reach it and
    ``execute_backend_replay`` can hand the inter-canister call to the proposal
    executor via ``yield from``.
    """
    try:
        from ggg import Realm
    except ImportError:
        from realm_backend.ggg import Realm

    from api.quarter_sync import request_quarter_codex_sync
    from core.quarter_bootstrap import derive_capital_install_set

    params = payload if isinstance(payload, dict) else json.loads(payload or "{}")
    quarter_id = (params.get("quarter_canister_id") or "").strip()
    if not quarter_id:
        return {"success": False, "error": "quarter_canister_id is required"}

    default_registry = ""
    realm = Realm.load("1")
    if realm:
        try:
            manifest = json.loads(getattr(realm, "manifest_data", "") or "{}")
            cas = (manifest.get("casals") if isinstance(manifest, dict) else None) or {}
            default_registry = (cas.get("registry_canister_id") or "").strip()
        except Exception:
            pass

    derived = derive_capital_install_set(default_registry)
    codices = derived.get("codices") or []
    if not codices:
        return {"success": False, "error": "Capital has no codex to offer; nothing to sync"}

    target_codex = codices[0]
    target = {
        "codex_id": target_codex.get("codex_id"),
        "version": target_codex.get("version"),
    }
    sync_payload = {
        "target": target,
        "registry_canister_id": (derived.get("registry_canister_id") or "").strip(),
        "frontend_canister_id": "",
    }

    return (yield from request_quarter_codex_sync(quarter_id, sync_payload))


def build_sync_proposal(
    target,
    current,
    registry_canister_id,
    *,
    frontend_canister_id="",
    parent_realm_canister_id="",
    proposal_id_placeholder="prop_999",
    metadata_extra=None,
):
    """Assemble the ballot payload for ``submit_replay_proposal``.

    Returns a dict with ``summary``, ``description``, ``code_inline``, and
    ``plan``. Raises ``ValueError`` when the serialized proposal metadata
    (including inline replay code) would exceed ``Proposal.metadata``'s
    4096-char cap.

    The ballot is realm-wide: every member of the quarter votes. It is not
    scoped to the root department because a minted quarter's root department is
    created with no members, which would leave nobody eligible to vote.
    """
    from core.governed_action import build_backend_replay_code

    spec = {
        "target": target,
        "current": current,
        "registry_canister_id": registry_canister_id,
        "frontend_canister_id": frontend_canister_id,
        "parent_realm_canister_id": parent_realm_canister_id,
    }
    plan = build_sync_plan(spec)
    items = plan.get("items") or []
    if not items:
        # build_bootstrap_plan drops all items when no registry is configured,
        # so an empty plan means either alignment or an unusable registry —
        # distinguish them, since the operator fix differs.
        if not (registry_canister_id or "").strip():
            raise ValueError("No registry_canister_id — cannot build a sync plan")
        raise ValueError("No codex drift — nothing to propose")

    summary = _sync_summary(target, current, registry_canister_id)
    description = _sync_description(target, current, registry_canister_id, plan)

    plan_json = json.dumps(plan, separators=(",", ":"))
    code_inline = build_backend_replay_code(
        "core.quarter_sync", "apply_sync_plan", plan_json
    )

    # Mirrors what submit_replay_proposal will store for a realm-wide ballot
    # (no org_scope), so the cap check measures the real payload.
    metadata = {
        "proposal_type": "governed_action",
        "code_inline": code_inline,
        "codex_name": f"governed_action_{proposal_id_placeholder}",
    }
    if metadata_extra:
        metadata.update(metadata_extra)
    serialized = json.dumps(metadata, separators=(",", ":"))
    if len(serialized) > PROPOSAL_METADATA_MAX_LENGTH:
        raise ValueError(
            f"Sync proposal metadata ({len(serialized)} chars) exceeds "
            f"Proposal.metadata limit ({PROPOSAL_METADATA_MAX_LENGTH}); "
            f"refusing to truncate"
        )

    return {
        "summary": summary,
        "description": description,
        "code_inline": code_inline,
        "plan": plan,
    }
