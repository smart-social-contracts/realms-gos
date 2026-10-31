"""Template-driven justice-system seeding (courts, license, hierarchy).

Codices ship a declarative court-hierarchy template (``data/justice.json``)
and call :func:`seed_justice_template` from their ``init`` hook. Because a
quarter's self-bootstrap re-runs codex init (``core.quarter_bootstrap``
installs codices with ``run_init=True``), every quarter automatically
receives its share of the hierarchy.

Scope semantics (per court spec, key ``scope``):

* ``"quarter"`` (default) — seeded on every canister: the capital and each
  quarter, so litigations can always be filed locally (cross-quarter cases
  live on the plaintiff's quarter).
* ``"capital"`` — seeded only on the capital or a standalone realm; skipped
  on plain quarters. When a quarter-scoped court's ``parent`` was skipped
  this way, the capital's canister id is recorded as ``appellate_quarter_id``
  in the court's metadata so appeals can be routed cross-quarter.

Idempotent: entities are looked up by name (their alias) and existing ones
are never mutated, so re-running on upgrade or reinstall neither duplicates
entities nor resets admin edits.
"""

import json
import time
from typing import Any, Dict, List, Optional

from ic_python_logging import get_logger

logger = get_logger("ggg.justice.seeding")

_DEFAULT_LICENSE_VALIDITY_S = 365 * 86400


def _now_s() -> int:
    """Current unix time in seconds, canister-safe."""
    try:
        from _cdk import ic

        return int(ic.time() / 1_000_000_000)
    except Exception:
        return int(time.time())


def _is_plain_quarter(realm) -> bool:
    """True for a quarter that is not the federation capital."""
    if realm is None:
        return False
    return bool(getattr(realm, "is_quarter", False)) and not bool(
        getattr(realm, "is_capital", False)
    )


def _get_by_alias(entity_cls, name: str):
    try:
        return entity_cls[name] if name else None
    except Exception:
        return None


def _ensure_justice_system(spec: Dict[str, Any], realm) -> Any:
    from .justice_system import JusticeSystem, JusticeSystemType

    name = (spec.get("name") or "").strip()
    if not name:
        raise ValueError("justice_system template requires a name")

    existing = _get_by_alias(JusticeSystem, name)
    if existing is not None:
        return existing

    js = JusticeSystem(
        name=name,
        description=spec.get("description", "") or "",
        system_type=spec.get("system_type", JusticeSystemType.PUBLIC),
        status=spec.get("status", "active"),
        metadata=spec.get("metadata", "") or "",
    )
    if realm is not None:
        js.realm = realm
    logger.info(f"Seeded JusticeSystem {name!r}")
    return js


def _ensure_license(license_data: Optional[Dict[str, Any]], justice_system) -> Optional[Any]:
    """Create the justice license from codex data and attach it (idempotent).

    Accepts either the raw ``justice_license.json`` shape (``{"license":
    {...}}``) or the inner license dict directly.
    """
    if not license_data:
        return None
    from ..governance.license import License, LicenseType

    spec = license_data.get("license", license_data)
    name = (spec.get("name") or "").strip()
    if not name:
        return None

    lic = _get_by_alias(License, name)
    if lic is None:
        now = _now_s()
        extra = {
            k: spec[k] for k in ("scope", "terms") if spec.get(k) is not None
        }
        lic = License(
            name=name,
            license_type=spec.get("license_type", LicenseType.JUSTICE_PROVIDER),
            description=license_data.get("description", spec.get("description", "")) or "",
            status=spec.get("status", "active"),
            issued_at=now,
            expires_at=now + int(spec.get("validity_seconds", _DEFAULT_LICENSE_VALIDITY_S)),
            issuing_authority=spec.get("issuing_authority", "") or "",
            metadata=json.dumps(extra) if extra else "",
        )
        logger.info(f"Seeded License {name!r}")

    if justice_system is not None and getattr(justice_system, "license", None) is None:
        lic.justice_system = justice_system
    return lic


def seed_justice_template(
    template: Dict[str, Any],
    license_data: Optional[Dict[str, Any]] = None,
    realm=None,
) -> Dict[str, Any]:
    """Seed a justice system + court hierarchy from a codex template.

    Args:
        template: ``{"justice_system": {...}, "courts": [{...}, ...]}``.
            Each court spec supports ``name`` (required), ``description``,
            ``jurisdiction``, ``level``, ``scope`` (``quarter``/``capital``,
            default ``quarter``) and ``parent`` (name of the appellate court).
        license_data: contents of the codex's ``justice_license.json``
            (optional) — issued once and attached to the justice system.
        realm: the Realm entity; resolved from the store when omitted.

    Returns:
        Summary dict: names of created / already-existing / scope-skipped
        courts, plus the justice system and license names.
    """
    from .court import Court

    if realm is None:
        try:
            from ..governance.realm import Realm

            realms = Realm.instances()
            realm = realms[0] if realms else None
        except Exception:
            realm = None

    plain_quarter = _is_plain_quarter(realm)
    capital_id = str(getattr(realm, "federation_realm_id", "") or "") if realm else ""

    js = _ensure_justice_system(template.get("justice_system") or {}, realm)
    lic = _ensure_license(license_data, js)

    court_specs: List[Dict[str, Any]] = list(template.get("courts") or [])
    created: List[str] = []
    existing: List[str] = []
    skipped: List[str] = []
    skipped_names = set()

    # Pass 1: create courts that belong on this canister.
    for spec in court_specs:
        name = (spec.get("name") or "").strip()
        if not name:
            continue
        scope = (spec.get("scope") or "quarter").strip().lower()
        if scope == "capital" and plain_quarter:
            skipped.append(name)
            skipped_names.add(name)
            continue

        if _get_by_alias(Court, name) is not None:
            existing.append(name)
            continue

        Court(
            name=name,
            description=spec.get("description", "") or "",
            jurisdiction=spec.get("jurisdiction", "") or "",
            level=spec.get("level", "first_instance"),
            status=spec.get("status", "active"),
            justice_system=js,
            metadata="",
        )
        created.append(name)
        logger.info(f"Seeded Court {name!r} (scope={scope})")

    # Pass 2: wire the hierarchy on the courts created in this run. Courts
    # that already existed are left untouched (their links may be admin-edited).
    for spec in court_specs:
        name = (spec.get("name") or "").strip()
        if name not in created:
            continue
        court = _get_by_alias(Court, name)
        if court is None:
            continue

        meta = {"scope": (spec.get("scope") or "quarter").strip().lower()}
        parent_name = (spec.get("parent") or "").strip()
        if parent_name:
            parent = _get_by_alias(Court, parent_name)
            if parent is not None:
                court.parent_court = parent
            elif parent_name in skipped_names and capital_id:
                # The appellate court lives on the capital: record where
                # appeals must be routed, since parent_court can only point
                # at a local entity.
                meta["appellate_court"] = parent_name
                meta["appellate_quarter_id"] = capital_id
        court.metadata = json.dumps(meta)

    result = {
        "justice_system": js.name,
        "license": lic.name if lic is not None else None,
        "created": created,
        "existing": existing,
        "skipped": skipped,
    }
    logger.info(f"seed_justice_template: {result}")
    return result
