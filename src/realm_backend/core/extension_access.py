"""Fail-closed access control for extension entry points (issue #262).

Enforced at the canister boundary (``extension_call`` / ``extension_sync_call``
/ ``extension_async_call`` in main.py). Internal calls — codex hooks,
proposal replay, extension-to-extension — go through
``core.extensions.call_extension_function`` directly and are *not* gated:
they already run with canister authority.

Manifest declaration (``manifest.json``)::

    "entry_access": {
      "default": "realm.data_view",
      "functions": {
        "get_public_info": "public",
        "transfer":        {"level": "realm.admin", "governed": true},
        "assign_profile":  {"level": "role.assign", "governed": true, "org": "root"},
        "join_committee":  {"level": "org.manage_members", "governed": true, "org_from_arg": "department"}
      }
    }

Access levels:

- ``"public"`` — any caller, including anonymous.
- any other string — an operation name from the ``Operations`` catalog
  (``ggg.system.user_profile``), checked via ``_check_access`` (e.g.
  ``"realm.data_view"``, ``"role.assign"``). Controllers, trusted
  principals, test mode, and governance replay pass any operation.

The old profile-named levels ``"member"`` and ``"admin"`` are gone:
profiles are bundles of operations assigned to users, not access levels.
Which profiles carry which operations is data (``Profiles`` baselines,
``Permission`` grants), never something this gate knows about.

``"governed": true`` adds the org-policy layer on top: when the governing
org's policy is not 1/1, the call must be confirmed (``confirm: true`` in
args) and then becomes an org-scoped Proposal that replays the same call
after the vote passes (see core.governed_action).

The governing org is configurable per function:

- ``"org": "<name>"`` — a fixed org;
- ``"org_from_arg": "<key>"`` — read the org name from that key in the
  call args, so e.g. department-scoped actions are voted by the affected
  department itself;
- neither — the root org.

If the named org does not exist, governance falls back to the root org
(the stricter direction), never to direct execution.

FAIL-CLOSED RULES — this is the whole point:

1. Extension has no ``entry_access`` at all  → every function is ``realm.admin``.
2. Function not listed in ``functions``      → the manifest ``default``.
3. No ``default`` declared                   → ``realm.admin``.
4. Malformed spec / resolution error         → ``realm.admin``.

A developer who forgets to declare access for a new function or a new
extension gets an admin-only endpoint, never a member-open one.
"""

import json
from typing import Any, Dict, Optional, Tuple

from ic_python_logging import get_logger

logger = get_logger("core.extension_access")

# System-wide fallback when nothing is declared. Never weaken this.
FAIL_CLOSED_LEVEL = "realm.admin"


def resolve_spec(manifest: Optional[dict], function_name: str) -> Dict[str, Any]:
    """Resolve the access spec for a function. Pure logic — unit-testable.

    Returns ``{"level": str, "governed": bool, "org": Optional[str],
    "org_from_arg": Optional[str]}``.
    """
    fallback = {
        "level": FAIL_CLOSED_LEVEL,
        "governed": False,
        "org": None,
        "org_from_arg": None,
    }
    try:
        if not isinstance(manifest, dict):
            return fallback
        entry_access = manifest.get("entry_access")
        if not isinstance(entry_access, dict):
            return fallback

        functions = entry_access.get("functions")
        raw = None
        if isinstance(functions, dict) and function_name in functions:
            raw = functions[function_name]
        else:
            raw = entry_access.get("default", FAIL_CLOSED_LEVEL)

        if isinstance(raw, str) and raw.strip():
            return {
                "level": raw.strip(),
                "governed": False,
                "org": None,
                "org_from_arg": None,
            }
        if isinstance(raw, dict):
            level = raw.get("level")
            if not isinstance(level, str) or not level.strip():
                return fallback
            return {
                "level": level.strip(),
                "governed": bool(raw.get("governed", False)),
                "org": raw.get("org") or None,
                "org_from_arg": raw.get("org_from_arg") or None,
            }
        return fallback
    except Exception as e:
        logger.error(f"resolve_spec failed for '{function_name}': {e}")
        return fallback


def _level_allows(caller: str, level: str) -> bool:
    from core.access import _check_access

    if level == "public":
        return True
    # Everything else is an operation name from the Operations catalog.
    # _check_access grants controllers, trusted principals, test mode, and
    # governance replay a pass for any operation.
    return _check_access(caller, level)


def _delegation_allows(args: str, caller: str, level: str) -> bool:
    """True when ``args`` act ``on_behalf_of`` a grantor whose active grant to ``caller`` covers ``level``.

    The delegate itself need not hold ``level`` (or be a member); the endpoint
    re-checks the grant through ``core.delegation.resolve_acting_context``.
    """
    try:
        args_obj = json.loads(args) if args else {}
        grantor = str((args_obj or {}).get("on_behalf_of") or "").strip()
        if not grantor or grantor == caller:
            return False
        from core.delegation import AccessDenied, resolve_acting_context

        try:
            resolve_acting_context(args_obj, level)
        except AccessDenied:
            return False
        return True
    except Exception as e:
        logger.error(f"extension_access: delegation check failed ({e})")
        return False


def gate_extension_call(
    extension_name: str,
    function_name: str,
    args: str,
    caller: str,
    allow_governed: bool = True,
) -> Optional[Dict[str, Any]]:
    """Boundary gate for an incoming extension call.

    Returns None when the call may proceed unchanged. Otherwise returns the
    response dict that must be sent back instead of calling the function
    (access denied, requires_confirmation, or proposal-created).

    ``allow_governed=False`` is for the @query endpoint, where proposals
    cannot be created; governed functions are then denied outright when the
    policy is not direct.
    """
    try:
        from core.runtime_extensions import _load_manifest, resolve_extension_id

        resolved = resolve_extension_id(extension_name)
        manifest = _load_manifest(resolved)
        spec = resolve_spec(manifest, function_name)
    except Exception as e:
        logger.error(
            f"extension_access: spec resolution failed for "
            f"{extension_name}.{function_name} ({e}); failing closed"
        )
        spec = {"level": FAIL_CLOSED_LEVEL, "governed": False, "org": None}

    # Layer 1 — RBAC / access level, directly or through power of attorney.
    if not _level_allows(caller, spec["level"]) and not _delegation_allows(
        args, caller, spec["level"]
    ):
        logger.warning(
            f"extension_access: denied {caller} -> "
            f"{extension_name}.{function_name} (level '{spec['level']}')"
        )
        from core.extension_errors import permission_denied_payload

        return permission_denied_payload(
            (
                f"Access denied: '{extension_name}.{function_name}' requires "
                f"access level '{spec['level']}'"
            ),
            spec["level"],
        )

    # Layer 2 — org policy (governed functions).
    if not spec["governed"]:
        return None

    from core.governed_action import (
        INITIATOR_KEY,
        build_extension_replay_code,
    )
    from core.governed_action import gate as governed_gate

    confirm = False
    replay_args = args or "{}"
    org_name = spec["org"]
    try:
        args_obj = json.loads(args) if args else {}
        if isinstance(args_obj, dict):
            confirm = bool(args_obj.pop("confirm", False))
            # Record who is asking, so caller-bound actions replay against
            # the right person (issue #262). Client-supplied values are
            # discarded first — only the gate may write this key.
            args_obj.pop(INITIATOR_KEY, None)
            args_obj[INITIATOR_KEY] = caller
            replay_args = json.dumps(args_obj)
            if spec["org_from_arg"]:
                # Governing org named by the call itself (e.g. the affected
                # department). Missing/unknown orgs fall back to root inside
                # governing_org() — stricter, never direct.
                dynamic = args_obj.get(spec["org_from_arg"])
                if isinstance(dynamic, str) and dynamic.strip():
                    org_name = dynamic.strip()
    except (json.JSONDecodeError, TypeError):
        pass

    if not allow_governed:
        # Query context: cannot create proposals; only direct-policy calls
        # (or controllers / replay) may proceed.
        verdict = governed_gate(
            caller=caller,
            summary="",
            replay_code="",
            org_name=org_name,
            confirm=False,
        )
        if verdict is None:
            return None
        return {
            "success": False,
            "error": (
                f"'{extension_name}.{function_name}' is governed by org policy "
                f"and cannot run as a query; use an update call"
            ),
        }

    summary = f"{extension_name}: {function_name}"
    try:
        preview = json.loads(replay_args)
        if isinstance(preview, dict) and preview:
            compact = {k: preview[k] for k in sorted(preview)[:6]}
            summary = f"{extension_name}: {function_name} {json.dumps(compact)[:160]}"
    except (json.JSONDecodeError, TypeError):
        pass

    return governed_gate(
        caller=caller,
        summary=summary,
        replay_code=build_extension_replay_code(
            extension_name, function_name, replay_args
        ),
        org_name=org_name,
        confirm=confirm,
        metadata_extra={
            "governed_extension": extension_name,
            "governed_function": function_name,
        },
    )
