"""Quarter-local self-bootstrap driven by the ic_basilisk_toolkit TaskManager.

When the capital mints a new quarter and calls ``bootstrap_as_quarter`` on it,
the quarter does the cheap config synchronously, records an install *plan*
(federation codex + extensions) on its ``Realm``, and seeds a recurring
``TaskManager`` task that installs **one item per tick** (``advance_bootstrap``).

Why one item per tick:

* A single IC update message cannot install ~28 extensions (instruction/time
  limit), so parity has to be reached incrementally.
* The recurrence interval doubles as **retry backoff**: a failed item stays at
  the cursor and is retried on the next tick, up to ``MAX_ATTEMPTS_PER_ITEM``,
  after which it is recorded as failed and the cursor advances. When the cursor
  passes the last item the task disables its own schedule.

Heavy logic lives here (native + unit-testable). The ``TaskManager`` step is a
tiny, stable codex shim that only calls ``advance_bootstrap()`` — so the
framework provides timer scheduling and upgrade recovery, and we provide the
install/retry semantics.
"""

import json

from ic_python_logging import get_logger

logger = get_logger("core.quarter_bootstrap")

# Name of the recurring TaskManager task and tuning knobs.
BOOTSTRAP_TASK_NAME = "quarter_self_bootstrap"
BOOTSTRAP_INTERVAL_S = 10  # tick cadence == per-item retry backoff
MAX_ATTEMPTS_PER_ITEM = 3  # give up on an item after this many tries

# Matches ``Realm.bootstrap_state`` String(max_length=...) in ggg/governance/realm.py.
BOOTSTRAP_STATE_MAX_LENGTH = 8192
# Failure records are the only unbounded growth vector; keep them small so a full
# 24-extension plan (~1.8k chars of items) always fits with headroom.
FAILED_ERROR_MAX_CHARS = 120
FAILED_DETAIL_MAX_ENTRIES = 8

# Stable codex shim run by the recurring TaskManager step. Kept intentionally
# tiny: all real work is the native advance_bootstrap() generator below. The
# presence of "yield"/"async_task" marks the step async for the framework.
BOOTSTRAP_STEP_CODE = (
    "def async_task():\n"
    "    from core.quarter_bootstrap import advance_bootstrap\n"
    "    res = yield from advance_bootstrap()\n"
    "    return res\n"
)

# Capital-side trigger task: drives auto-scale provisioning while a scale is in
# flight, so the loop runs fully on-chain with no external poker (issue #156).
AUTOSCALE_TASK_NAME = "quarter_autoscale_trigger"
AUTOSCALE_INTERVAL_S = 15
AUTOSCALE_STEP_CODE = (
    "def async_task():\n"
    "    from core.quarter_scaling import run_autoscale_tick\n"
    "    res = yield from run_autoscale_tick()\n"
    "    return res\n"
)

# ── Pure plan construction + state machine (unit-testable, no canister) ──────

# Core extensions installed early on new quarters so joiners get a usable dashboard.
_BOOTSTRAP_EXT_PRIORITY = (
    "member_dashboard",
    "public_dashboard",
    "realm_settings",
    "voting",
    "admin_dashboard",
    "access_manager",
    "role_manager",
    "codex_viewer",
    "task_monitor",
    "import_export",
    "vault",
    "mundus_explorer",
)


def _bootstrap_ext_sort_key(item):
    ext_id = item.get("id") or ""
    try:
        prio = _BOOTSTRAP_EXT_PRIORITY.index(ext_id)
    except ValueError:
        prio = len(_BOOTSTRAP_EXT_PRIORITY)
    return (prio, item.get("_orig_idx", 0))


def build_bootstrap_plan(spec):
    """Turn a bootstrap spec into an ordered install plan + cursor state.

    ``spec`` keys (all optional except where a value is needed to act):
        parent_realm_canister_id, registry_canister_id, frontend_canister_id,
        codex ({codex_id, version, run_init} | None),
        codices ([{codex_id, version, run_init}, ...] — mirrors a capital that
            has more than one codex package; takes precedence over ``codex``),
        extensions ([{ext_id, version} | "ext_id", ...]).

    Returns a JSON-able dict consumed by ``advance_bootstrap``. Codex/extension
    items are only included when a ``registry_canister_id`` is present (nothing
    to pull from otherwise). Extensions are installed first (one per tick) so a
    codex install never pulls its full dependency closure inline; codex ``init``
    runs after those plan items are in place.
    """
    spec = spec or {}
    registry = (spec.get("registry_canister_id") or "").strip()
    frontend = (spec.get("frontend_canister_id") or "").strip()
    parent = (spec.get("parent_realm_canister_id") or "").strip()

    items = []
    codex_items = []
    # Prefer the explicit ``codices`` list (auto-derived from the capital's live
    # set); fall back to the single ``codex`` for back-compat with older callers.
    codices = spec.get("codices")
    if not codices:
        single = spec.get("codex") or None
        codices = [single] if single else []
    if registry:
        for codex in codices:
            if not isinstance(codex, dict):
                continue
            codex_id = (codex.get("codex_id") or "").strip()
            if codex_id:
                codex_items.append(
                    {
                        "kind": "codex",
                        "id": codex_id,
                        "version": codex.get("version"),
                        "run_init": codex.get("run_init", True),
                    }
                )
    ext_items = []
    if registry:
        for orig_idx, ext in enumerate(spec.get("extensions") or []):
            if isinstance(ext, dict):
                ext_id = (ext.get("ext_id") or "").strip()
                version = ext.get("version")
            else:
                ext_id = str(ext).strip()
                version = None
            if not ext_id:
                continue
            ext_items.append(
                {
                    "kind": "extension",
                    "id": ext_id,
                    "version": version,
                    "_orig_idx": orig_idx,
                }
            )
        ext_items.sort(key=_bootstrap_ext_sort_key)
        for item in ext_items:
            item.pop("_orig_idx", None)
        items.extend(ext_items)
        items.extend(codex_items)

    has_extensions = bool(ext_items)
    return {
        "parent": parent,
        "registry": registry,
        "frontend": frontend,
        "items": items,
        "cursor": 0,
        "attempts": 0,
        "done": [],
        "failed": [],
        "status": "complete" if not items else "pending",
        "install_codex_dependencies": not has_extensions,
    }


def reorder_pending_bootstrap_items(state):
    """Re-sort pending plan items: extensions (priority) then codices.

    Only reorders from ``cursor`` onward so completed installs stay put.
    Also sets ``install_codex_dependencies`` when the plan lists extensions.
    Returns True if ``items`` order changed.
    """
    if not isinstance(state, dict):
        return False

    items = state.get("items") or []
    cursor = int(state.get("cursor") or 0)
    if cursor >= len(items):
        return False

    head = items[:cursor]
    tail = items[cursor:]
    ext_items = [i for i in tail if i.get("kind") == "extension"]
    codex_items = [i for i in tail if i.get("kind") == "codex"]
    other_items = [i for i in tail if i.get("kind") not in ("extension", "codex")]

    for idx, item in enumerate(ext_items):
        item["_orig_idx"] = idx
    ext_items.sort(key=_bootstrap_ext_sort_key)
    for item in ext_items:
        item.pop("_orig_idx", None)

    new_tail = ext_items + codex_items + other_items
    changed = new_tail != tail
    if changed:
        state["items"] = head + new_tail

    if any(i.get("kind") == "extension" for i in state["items"]):
        state["install_codex_dependencies"] = False

    return changed


def step_plan(state, ok, error=None):
    """Advance the plan cursor given the outcome of installing the current item.

    Mutates ``state`` and returns a *bounded* copy of it: callers that intend to
    persist the result must use the return value, since the argument keeps the
    full untruncated failure detail.
      * success      -> record done, advance cursor, reset attempts.
      * failure      -> bump attempts; once ``MAX_ATTEMPTS_PER_ITEM`` is reached,
                        record the failure and advance (so one bad item never
                        wedges the whole bootstrap).
    ``status`` becomes ``complete`` once the cursor passes the last item.
    """
    items = state.get("items") or []
    cursor = int(state.get("cursor") or 0)
    if cursor >= len(items):
        state["status"] = "complete"
        return state

    item = items[cursor]
    attempts = int(state.get("attempts") or 0) + 1
    if ok:
        state.setdefault("done", []).append(item.get("id"))
        state["cursor"] = cursor + 1
        state["attempts"] = 0
    elif attempts >= MAX_ATTEMPTS_PER_ITEM:
        state.setdefault("failed", []).append(
            {"id": item.get("id"), "error": str(error)[:FAILED_ERROR_MAX_CHARS]}
        )
        state["cursor"] = cursor + 1
        state["attempts"] = 0
    else:
        state["attempts"] = attempts

    state["status"] = "complete" if int(state["cursor"]) >= len(items) else "pending"
    return _bounded_state(state)


# ── Capital runtime config + branding mirroring (capital → quarter) ──────────

# Realm fields copied verbatim from the capital onto a new quarter so it comes
# up branded + registration-ready (issue #156). ``test_mode_demo_data`` is
# deliberately excluded — a quarter must not spin up its own synthetic personas
# (that would defeat the load distribution the quarter exists to provide).
_QUARTER_CONFIG_STR_FIELDS = (
    "name",
    "manifesto",
    "welcome_message",
    "logo_url",
    "background_image_url",
    "network",
    "accounting_currency",
    "file_registry_canister_id",
    "marketplace_canister_id",
    "token_canister_id",
    "nft_canister_id",
    "demo_notice_body",
)
_QUARTER_CONFIG_BOOL_FIELDS = (
    "open_registration",
    "ai_assistant_enabled",
    "require_marketplace_approval",
)
_QUARTER_CONFIG_INT_FIELDS = ("accounting_currency_decimals",)
_QUARTER_TEST_FLAG_FIELDS = (
    "test_mode",
    "test_mode_ii_bypass",
    "test_mode_user_self_registration",
    "test_mode_skip_terms",
    "test_mode_skip_passport_zkproof",
    "test_mode_disable_monetary_tokens",
    "test_mode_demo_notice",
)
# RealmStatus values from ggg/governance/realm.py — duplicated here so this
# module stays pure-stdlib and unit-testable without the canister.
_QUARTER_VALID_STATUSES = frozenset({
    "setup",
    "alpha",
    "beta",
    "production",
    "deprecation",
    "terminated",
})


def apply_quarter_config(realm, config):
    """Apply a capital's mirrored runtime config + branding onto a quarter's
    ``realm`` (issue #156). Only sets attributes — pure enough to unit-test with
    a stand-in object. Returns the list of applied field names.

    Safe + partial: missing/None keys are skipped, and empty *string* values are
    ignored so we never wipe a field to a constraint-violating blank (e.g.
    ``name`` has a min length). Test flags live under a nested ``test_flags`` dict
    and are copied verbatim — if the capital runs production (flags off) the
    quarter inherits them off, so this never enables test mode on a real realm.
    """
    config = config or {}
    applied = []
    for f in _QUARTER_CONFIG_STR_FIELDS:
        v = config.get(f)
        if v:  # truthy: skip None and "" (don't clobber with a blank)
            setattr(realm, f, str(v))
            applied.append(f)
    for f in _QUARTER_CONFIG_BOOL_FIELDS:
        if config.get(f) is not None:
            setattr(realm, f, bool(config[f]))
            applied.append(f)
    for f in _QUARTER_CONFIG_INT_FIELDS:
        if config.get(f) is not None:
            try:
                setattr(realm, f, int(config[f]))
                applied.append(f)
            except (TypeError, ValueError):
                pass
    flags = config.get("test_flags") or {}
    for f in _QUARTER_TEST_FLAG_FIELDS:
        if flags.get(f) is not None:
            setattr(realm, f, bool(flags[f]))
            applied.append(f)
    # Empty string is meaningful ("trust only the configured marketplace"), so
    # use an explicit None-check rather than the truthy skip used for branding.
    if config.get("trusted_approvers") is not None:
        setattr(realm, "trusted_approvers", str(config["trusted_approvers"]))
        applied.append("trusted_approvers")
    status = config.get("status")
    if status:
        status = str(status).strip()
        if status in _QUARTER_VALID_STATUSES:
            setattr(realm, "status", status)
            applied.append("status")
    return applied


# ── Live install-set derivation (capital → quarter parity) ──────────────────


def derive_capital_install_set(default_registry=""):
    """Build the codex + extension install set from the capital's *own live
    state*, so a freshly minted quarter mirrors whatever the capital currently
    has installed — no admin-curated list to maintain (issue #156).

    Reads the capital's runtime-installed extensions and codex packages from the
    persistent filesystem (the same source ``list_runtime_extensions`` /
    ``list_codex_packages`` expose):

      * extensions — each ``/extensions/<id>/_source.json`` gives the version it
        was installed at (and the registry it came from);
      * codices    — each ``/codex_packages/<id>/manifest.json`` gives its version.

    The pull ``registry_canister_id`` is inferred from the extensions' recorded
    sources (they all come from the same file_registry in practice), falling
    back to ``default_registry`` (the configured casals-block value) when no
    source records a registry — e.g. a capital whose extensions were baked in.

    Returns ``{registry_canister_id, codices, extensions}`` shaped for
    ``build_bootstrap_plan``. Best-effort: any read failure degrades to an empty
    set for that kind rather than aborting provisioning.
    """
    registry = (default_registry or "").strip()
    inferred_registry = ""
    extensions = []
    codices = []

    try:
        from core.runtime_extensions import (
            get_extension_source,
        )
        from core.runtime_extensions import list_installed as _list_installed_exts

        for ext_id in _list_installed_exts():
            src = get_extension_source(ext_id) or {}
            if not inferred_registry:
                inferred_registry = (src.get("registry_canister_id") or "").strip()
            version = (src.get("version") or "").strip() or None
            extensions.append({"ext_id": ext_id, "version": version})
    except Exception as e:
        logger.error(f"derive_capital_install_set: extensions read failed — {e}")

    try:
        from core.runtime_codex import (
            get_all_codex_manifests,
        )
        from core.runtime_codex import list_installed as _list_installed_codices

        manifests = get_all_codex_manifests()
        for codex_id in _list_installed_codices():
            manifest = manifests.get(codex_id) or {}
            version = (str(manifest.get("version") or "")).strip() or None
            codices.append({"codex_id": codex_id, "version": version, "run_init": True})
    except Exception as e:
        logger.error(f"derive_capital_install_set: codices read failed — {e}")

    # Unified ``kind: codex`` packages live under /extensions (issue #244); mirror
    # the ``list_codex_packages`` fallback so the quarter gets a codex item, not an
    # extension item that would skip ``run_init`` and inline dep control.
    try:
        from core.codex_hooks import get_active_codex
        from core.runtime_extensions import get_all_extension_manifests

        active = get_active_codex()
        if active:
            installed_ids = {c["codex_id"] for c in codices}
            if active not in installed_ids:
                manifest = get_all_extension_manifests().get(active) or {}
                version = (str(manifest.get("version") or "")).strip() or None
                codices.append(
                    {"codex_id": active, "version": version, "run_init": True}
                )
    except Exception as e:
        logger.error(f"derive_capital_install_set: active codex fallback failed — {e}")

    codex_ids = {c["codex_id"] for c in codices}
    if codex_ids:
        extensions = [e for e in extensions if e.get("ext_id") not in codex_ids]

    return {
        # Trust the registry the extensions actually came from (recorded at
        # install time) over the configured default: the casals-block value is
        # operator-supplied and has been observed to carry the *realm* registry
        # id, which cannot serve extension pulls.
        "registry_canister_id": inferred_registry or registry,
        "codices": codices,
        "extensions": extensions,
    }


# ── Realm-persisted state helpers ───────────────────────────────────────────


def _state_json_len(state):
    return len(json.dumps(state, separators=(",", ":")))


def _truncate_failed_errors(failed):
    """Normalize failure records to short, fixed-width error strings."""
    out = []
    for entry in failed or []:
        if not isinstance(entry, dict):
            continue
        rec = {"id": entry.get("id")}
        if entry.get("error") is not None:
            rec["error"] = str(entry["error"])[:FAILED_ERROR_MAX_CHARS]
        out.append(rec)
    return out


def bound_state(state, max_length=BOOTSTRAP_STATE_MAX_LENGTH):
    """Return ``state`` trimmed so its JSON serialization fits ``max_length``.

    Degrades gracefully: shortens error text, then drops error detail, then
    spills overflow failures into a count + bare-id list. Never raises.
    """
    if not isinstance(state, dict):
        return state

    bounded = dict(state)
    bounded["failed"] = _truncate_failed_errors(bounded.get("failed"))
    # Overflow spilled by an earlier pass has to survive re-bounding: a tick
    # bounds once through step_plan and again through save_state, and the
    # entries it counts are already gone from ``failed``.
    if int(state.get("failed_overflow") or 0):
        bounded["failed_overflow"] = int(state["failed_overflow"])
        bounded["failed_overflow_ids"] = [
            i for i in (state.get("failed_overflow_ids") or []) if i
        ]
    else:
        bounded.pop("failed_overflow", None)
        bounded.pop("failed_overflow_ids", None)

    if _state_json_len(bounded) <= max_length:
        return bounded

    # Drop per-entry error text first — ids alone are enough to diagnose counts.
    bounded["failed"] = [{"id": e.get("id")} for e in bounded["failed"]]
    if _state_json_len(bounded) <= max_length:
        return bounded

    # Still too large: retain detailed head, spill the tail as bare ids + count.
    failed = bounded["failed"]
    keep = failed[:FAILED_DETAIL_MAX_ENTRIES]
    overflow = failed[FAILED_DETAIL_MAX_ENTRIES:]
    bounded["failed"] = keep
    if overflow:
        bounded["failed_overflow"] = int(bounded.get("failed_overflow") or 0) + len(
            overflow
        )
        bounded["failed_overflow_ids"] = list(
            bounded.get("failed_overflow_ids") or []
        ) + [e.get("id") for e in overflow if e.get("id")]

    while bounded["failed"] or bounded.get("failed_overflow"):
        if _state_json_len(bounded) <= max_length:
            return bounded
        if len(bounded["failed"]) > 1:
            spill = bounded["failed"].pop()
            bounded["failed_overflow"] = int(bounded.get("failed_overflow") or 0) + 1
            ids = list(bounded.get("failed_overflow_ids") or [])
            if spill.get("id"):
                ids.append(spill["id"])
            bounded["failed_overflow_ids"] = ids
        elif bounded.get("failed_overflow_ids"):
            # Shed ids, never the count: the failures happened whether or not
            # there is room left to name them.
            bounded["failed_overflow_ids"] = bounded["failed_overflow_ids"][:-1]
        else:
            bounded["failed"] = []
            break

    if not bounded.get("failed_overflow_ids"):
        bounded.pop("failed_overflow_ids", None)
    if not bounded.get("failed_overflow"):
        bounded.pop("failed_overflow", None)
    return bounded


def _bounded_state(state):
    return bound_state(state, BOOTSTRAP_STATE_MAX_LENGTH)


def load_state(realm):
    """Read the JSON bootstrap plan persisted on ``realm`` (or None)."""
    raw = getattr(realm, "bootstrap_state", "") or ""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def save_state(realm, state):
    """Persist the JSON bootstrap plan onto ``realm``."""
    realm.bootstrap_state = json.dumps(
        _bounded_state(state), separators=(",", ":")
    )


def _persist_state(realm, state):
    """Best-effort save: a persistence failure must never kill the bootstrap tick."""
    try:
        save_state(realm, state)
        return True
    except Exception as e:
        logger.error(f"save_state failed ({e}); retrying with minimal state")
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
            }
            save_state(realm, minimal)
            return True
        except Exception as e2:
            logger.error(f"save_state minimal retry also failed: {e2}")
            return False


# ── Canister runtime: install one item per tick ─────────────────────────────


def _install_item(state, item):
    """Generator: install a single plan item locally via the file registry.

    Returns the parsed install result dict (``{"success": ...}``).
    """
    registry = state.get("registry") or ""
    frontend = state.get("frontend") or ""

    if item.get("kind") == "codex":
        from api.file_registry import install_codex_from_registry as _install_codex

        res = yield from _install_codex(
            registry,
            item.get("id"),
            item.get("version"),
            item.get("run_init", True),
            install_dependencies=state.get("install_codex_dependencies", True),
        )
        return json.loads(res) if isinstance(res, str) else res

    # Extension: pull + install, then fire the optional initialize() hook.
    from api.file_registry import install_extension_from_registry as _install_ext

    res = yield from _install_ext(
        registry,
        item.get("id"),
        item.get("version"),
        frontend_canister_id=frontend or None,
    )
    parsed = json.loads(res) if isinstance(res, str) else res
    if isinstance(parsed, dict) and parsed.get("success"):
        try:
            import api

            api.extensions.extension_sync_call(item.get("id"), "initialize", "{}")
        except Exception as init_err:
            logger.info(
                f"Extension {item.get('id')} has no initialize hook (ok): {init_err}"
            )
    return parsed


# Reentrancy guard: an install can outlast the tick interval (chunked registry
# pulls), and a plan re-seed can leave two live schedules for a moment. Two
# concurrent ticks corrupt the plan — each installs items[cursor] but marks the
# *reloaded* cursor item done, silently skipping every other item.
_bootstrap_tick_in_flight = False


def advance_bootstrap():
    """Install the next pending plan item, one per call (generator).

    Invoked repeatedly by the recurring TaskManager task. Returns a JSON-able
    dict describing this tick. When the plan is exhausted it disables the
    recurring schedule so the task stops firing.
    """
    global _bootstrap_tick_in_flight

    try:
        from ggg import Realm
    except ImportError:
        from realm_backend.ggg import Realm  # test/module layout

    if _bootstrap_tick_in_flight:
        return {
            "success": True,
            "status": "busy",
            "message": "previous tick still installing",
        }

    realm = Realm.load("1")
    if not realm:
        return {"success": False, "error": "Realm not found"}

    state = load_state(realm)
    if not state:
        disable_recurring_task(BOOTSTRAP_TASK_NAME)
        return {"success": True, "status": "idle", "message": "no bootstrap plan"}

    if reorder_pending_bootstrap_items(state):
        _persist_state(realm, state)

    items = state.get("items") or []
    cursor = int(state.get("cursor") or 0)
    if state.get("status") == "complete" or cursor >= len(items):
        state["status"] = "complete"
        _persist_state(realm, state)
        inherit_res = None
        if not state.get("acting_inherited"):
            inherit_res = yield from inherit_acting_appointments_from_parent(realm, state)
        if inherit_res and inherit_res.get("success") is False:
            return {
                "success": False,
                "status": "complete",
                "inherit": inherit_res,
                "done": state.get("done", []),
                "failed": state.get("failed", []),
            }
        disable_recurring_task(BOOTSTRAP_TASK_NAME)
        return {
            "success": True,
            "status": "complete",
            "done": state.get("done", []),
            "failed": state.get("failed", []),
        }

    item = items[cursor]
    ok = False
    result = None
    _bootstrap_tick_in_flight = True
    try:
        try:
            result = yield from _install_item(state, item)
            if isinstance(result, dict) and result.get("status") == "in_progress":
                return {
                    "success": True,
                    "status": "in_progress",
                    "phase": result.get("phase"),
                    "item": item.get("id"),
                    "done": result.get("done"),
                    "total": result.get("total"),
                }
            ok = not (isinstance(result, dict) and result.get("success") is False)
        except Exception as e:
            logger.error(f"advance_bootstrap install of {item.get('id')} failed: {e}")
            result = {"success": False, "error": str(e)}
            ok = False
    finally:
        _bootstrap_tick_in_flight = False

    # Reload across the async boundary, then record progress — but only if the
    # plan still points at the item we actually installed (a concurrent driver
    # or re-seed may have moved the cursor; stepping then would mark a
    # never-installed item as done).
    realm = Realm.load("1")
    state = load_state(realm) or state
    fresh_items = state.get("items") or []
    fresh_cursor = int(state.get("cursor") or 0)
    fresh_item = fresh_items[fresh_cursor] if fresh_cursor < len(fresh_items) else None
    if fresh_item is None or fresh_item.get("id") != item.get("id"):
        logger.warning(
            f"advance_bootstrap: plan moved under us (installed {item.get('id')!r}, "
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
    _persist_state(realm, state)

    if ok and not state.get("ready_reported"):
        parent_id = (
            (getattr(realm, "federation_realm_id", "") or "").strip()
            or (state.get("parent") or "").strip()
        )
        if parent_id:
            from core.join_targets import is_dashboard_installed
            from core.runtime_extensions import list_installed, resolve_extension_id

            if is_dashboard_installed(
                list_installed(),
                resolve_extension_id("member_dashboard"),
            ):
                from api.cross_quarter import report_ready_to_capital

                report_res = yield from report_ready_to_capital(parent_id)
                if report_res.get("success"):
                    state["ready_reported"] = True
                    _persist_state(realm, state)
                else:
                    logger.warning(
                        f"report_quarter_ready to {parent_id} failed: {report_res}"
                    )

    if state.get("status") == "complete":
        logger.info(
            f"Quarter bootstrap complete: done={state.get('done')} failed={state.get('failed')}"
        )
        inherit_res = None
        if not state.get("acting_inherited"):
            inherit_res = yield from inherit_acting_appointments_from_parent(realm, state)
        if inherit_res and inherit_res.get("success") is False:
            return {
                "success": False,
                "status": "complete",
                "inherit": inherit_res,
                "cursor": int(state.get("cursor") or 0),
                "item": item.get("id"),
                "result": result,
            }
        disable_recurring_task(BOOTSTRAP_TASK_NAME)

    return {
        "success": ok,
        "status": state.get("status"),
        "cursor": int(state.get("cursor") or 0),
        "item": item.get("id"),
        "result": result,
    }


# ── Acting appointment inherit (quarter ← capital, issue #301) ───────────────


def inherit_acting_appointments_from_parent(realm, state):
    """One-shot: copy capital seat-holders as acting officers after bootstrap.

    Runs only once bootstrap is complete (positions exist from codex init).
    Persists ``acting_inherited`` on bootstrap state so we do not re-fetch.
    """
    if state.get("acting_inherited"):
        return {"success": True, "status": "skip", "reason": "already inherited"}

    if not bool(getattr(realm, "is_quarter", False)):
        return {"success": True, "status": "skip", "reason": "not a quarter"}

    parent_id = (
        (getattr(realm, "federation_realm_id", "") or "").strip()
        or (state.get("parent") or "").strip()
    )
    if not parent_id:
        return {"success": True, "status": "skip", "reason": "no parent"}

    from api.cross_quarter import fetch_position_holders
    from core.acting_appointments import apply_inherited_holders

    fetched = yield from fetch_position_holders(parent_id)
    if not fetched.get("success"):
        return {
            "success": False,
            "status": "error",
            "error": fetched.get("error", "fetch failed"),
        }

    result = apply_inherited_holders(fetched, parent_id)
    state["acting_inherited"] = True
    _persist_state(realm, state)
    logger.info(
        f"Inherited acting appointments from capital {parent_id}: {result}"
    )
    return {"success": True, "status": "inherited", "result": result}


# ── Capital-side quarter directory merge (capital ← quarters) ───────────────


def sync_one_peer(peer_canister_id):
    """Generator: pull one peer quarter's coarse directory and merge it into
    ours (un-gated). Adds ``Quarter`` entities for peers we did not know about
    and refreshes known populations (monotonic: takes the larger count, per
    ``merge_quarter_directory``). Used by the ``sync_quarters`` gossip endpoint.
    Returns a JSON-able dict.
    """
    try:
        from _cdk import ic
        from api.cross_quarter import fetch_peer_directory
        from core.cross_quarter import merge_quarter_directory
        from ggg import Quarter, Realm

        fetched = yield from fetch_peer_directory(peer_canister_id)
        if not fetched.get("success"):
            return {"success": False, "error": fetched.get("error", "fetch failed")}

        peer_quarters = fetched.get("quarters", [])
        peer_self = fetched.get("self")
        self_id = ic.id().to_str()
        realm = Realm.load("1")

        # One scan of the Quarter table; reused for the directory merge below
        # instead of re-scanning per merged entry.
        quarters = Quarter.instances()
        quarters_by_cid = {}
        for q in quarters:
            quarters_by_cid.setdefault(q.canister_id, q)
        local = []
        for q in quarters:
            local.append(
                {
                    "name": q.name or "",
                    "canister_id": q.canister_id or "",
                    "population": int(q.population or 0),
                    "status": q.status or "setup",
                }
            )

        merged, changed = merge_quarter_directory(
            local,
            peer_quarters,
            peer_self=peer_self,
            peer_canister_id=peer_canister_id,
        )

        from core.quarter_drift import apply_self_report_to_quarter

        existing_ids = set(quarters_by_cid)
        added = 0
        for entry in merged:
            cid = entry.get("canister_id")
            if not cid or cid == self_id or cid in existing_ids:
                # Update population and drift report on a known quarter.
                q = quarters_by_cid.get(cid)
                if q is not None:
                    new_pop = int(entry.get("population", 0) or 0)
                    if new_pop > int(q.population or 0):
                        q.population = new_pop
                    merged_status = entry.get("status")
                    if merged_status and merged_status != q.status:
                        q.status = merged_status
                    if cid == peer_canister_id:
                        apply_self_report_to_quarter(q, peer_self)
                continue
            new_q = Quarter(
                name=entry.get("name") or cid[:8],
                canister_id=cid,
                population=int(entry.get("population", 0) or 0),
                status=entry.get("status") or "setup",
            )
            if realm is not None:
                new_q.federation = realm
            existing_ids.add(cid)
            quarters_by_cid[cid] = new_q
            added += 1

        return {
            "success": True,
            "peer": peer_canister_id,
            "added": added,
            "known_quarters": len(existing_ids),
            "changed": bool(changed),
        }
    except Exception as e:
        import traceback as _tb

        logger.error(f"Error in sync_one_peer: {e}\n{_tb.format_exc()}")
        return {"success": False, "error": str(e)}


# ── TaskManager seeding / teardown (canister only) ──────────────────────────


def seed_recurring_codex_task(name, code, interval_s):
    """Create (or re-enable) a recurring single-step TaskManager task.

    Idempotent: if a task with ``name`` already exists its schedule is simply
    re-enabled (and its interval refreshed) rather than duplicated.
    """
    from _cdk import ic
    from core.task_manager import TaskManager
    from ggg import Call, Codex, Task, TaskSchedule, TaskStep

    existing = None
    try:
        for t in Task.instances():
            if t.name == name:
                existing = t
                break
    except Exception:
        existing = None

    if existing is not None:
        # Refresh the persisted shim. The Codex ``code`` is stored as an entity
        # from the first seeding, so a corrected/updated step shim (e.g. the
        # from-main → from-core fix, issue #156) only takes effect if we rewrite
        # it here — otherwise re-seeding silently keeps running the stale code.
        try:
            for step in existing.steps:
                if step.call is not None and step.call.codex is not None:
                    step.call.codex.code = code
        except Exception as e:
            logger.error(
                f"seed_recurring_codex_task: codex refresh failed for {name}: {e}"
            )

        # A task left in a terminal state ("failed"/"completed") is never
        # rescheduled by TaskManager._update_timers (which only acts on
        # pending/running tasks), so a re-seed would no-op. Reset it back to a
        # schedulable state so re-seeding genuinely recovers it (issue #156).
        if str(existing.status) in ("failed", "completed"):
            existing.status = "pending"
            existing.step_to_execute = 0
            for step in existing.steps:
                step.status = "pending"
            for s in existing.schedules:
                s.last_run_at = 0  # fire promptly on the next _update_timers pass

        # A re-seed is an explicit recovery action: also reset the persisted
        # plan state. advance_bootstrap short-circuits on status=="complete"
        # and never retries items recorded in ``failed`` — without this reset
        # a re-seed instantly replays the stale failure list (e.g. failures
        # caused by since-fixed registry/platform issues) instead of working.
        if name == BOOTSTRAP_TASK_NAME:
            try:
                from ggg import Realm
            except ImportError:
                try:
                    from realm_backend.ggg import Realm
                except Exception:
                    Realm = None
            if Realm is not None:
                try:
                    realm = Realm.load("1")
                    state = load_state(realm) if realm else None
                    if state:
                        state["status"] = "in_progress"
                        state["cursor"] = 0
                        state["attempts"] = 0
                        state["done"] = []
                        state["failed"] = []
                        save_state(realm, state)
                except Exception as e:
                    logger.warning(f"re-seed: could not reset bootstrap plan state: {e}")

        for s in existing.schedules:
            s.disabled = False
            s.repeat_every = interval_s
        TaskManager().run()
        return existing

    codex = Codex(name=f"_{name}_step_{int(ic.time())}", code=code)
    # MUST be async: the step code (BOOTSTRAP_STEP_CODE / AUTOSCALE_STEP_CODE)
    # defines an ``async_task()`` generator that ``yield from``s into native
    # code making inter-canister calls. With is_async=False the framework only
    # exec()s the code (defining the function but never calling/driving it), so
    # advance_bootstrap()/run_autoscale_tick() would silently never run.
    call = Call(codex=codex, is_async=True)
    step = TaskStep(call=call, run_next_after=0)
    task = Task(name=name)
    step.task = task
    TaskSchedule(
        name=f"schedule_{name}",
        task=task,
        run_at=0,
        repeat_every=interval_s,
        last_run_at=0,
        disabled=False,
    )
    TaskManager().add_task(task)
    TaskManager().run()
    return task


def disable_recurring_task(name):
    """Disable the schedule of a recurring task so it stops firing."""
    try:
        from ggg import TaskSchedule
    except ImportError:
        try:
            from realm_backend.ggg import TaskSchedule
        except Exception:
            return
    sname = f"schedule_{name}"
    try:
        for s in TaskSchedule.instances():
            if s.name == sname:
                s.disabled = True
    except Exception as e:
        logger.error(f"Failed to disable recurring task {name}: {e}")


# Retired (issue #156): populations refresh via join-time push instead.
RETIRED_POP_SYNC_TASK_NAME = "quarter_population_sync"


def disable_population_sync_task():
    """Stop the retired quarter_population_sync task on upgraded capitals."""
    disable_recurring_task(RETIRED_POP_SYNC_TASK_NAME)


def seed_bootstrap_task(interval_s=BOOTSTRAP_INTERVAL_S):
    """Seed the recurring quarter self-bootstrap task."""
    return seed_recurring_codex_task(
        BOOTSTRAP_TASK_NAME, BOOTSTRAP_STEP_CODE, interval_s
    )


def ensure_autoscale_task() -> bool:
    """Seed (or re-enable) the recurring task that drives auto-scale
    provisioning while ``scale_in_flight`` is set. Idempotent.

    Lives in core: the registration path (``core.autoscale``) runs in contexts
    where ``from main import ...`` resolves nothing (WASI sandbox), so the
    seeding primitive must be importable without the entry module.
    """
    try:
        seed_recurring_codex_task(AUTOSCALE_TASK_NAME, AUTOSCALE_STEP_CODE, AUTOSCALE_INTERVAL_S)
        return True
    except Exception as e:
        logger.error(f"ensure_autoscale_task failed: {e}")
        return False
