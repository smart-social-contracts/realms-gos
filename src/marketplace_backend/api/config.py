"""Marketplace singleton configuration.

Stores the file_registry canister id, the billing-service principal
allowed to call ``record_license_payment``, and license pricing.

Canister settings and the admin list are controller-only; the
marketplace canister's controller list is curated by Smart Social
Contracts. Admins may manage the reviewer list.
"""

from typing import Dict

from _cdk import ic
from core.models import MarketplaceConfigEntity
from ic_python_logging import get_logger

logger = get_logger("api.config")

CONFIG_ID = "config"

DEFAULT_LICENSE_PRICE_USD_CENTS = 2000              # $20 / month
DEFAULT_LICENSE_DURATION_SECONDS = 30 * 24 * 3600   # 1 month
_LEGACY_YEARLY_PRICE_USD_CENTS = 9900
_LEGACY_YEARLY_DURATION_SECONDS = 365 * 24 * 3600


def _is_controller() -> bool:
    try:
        return bool(ic.is_controller(ic.caller()))
    except Exception:
        return False


def _require_controller() -> Dict:
    if not _is_controller():
        return {"success": False, "error": "Unauthorized: controller-only"}
    return {"success": True}


def _ensure_config() -> MarketplaceConfigEntity:
    cfg = MarketplaceConfigEntity[CONFIG_ID]
    if cfg is None:
        cfg = MarketplaceConfigEntity(
            id=CONFIG_ID,
            file_registry_canister_id="",
            casals_frontend_canister_id="",
            billing_service_principal="",
            license_price_usd_cents=DEFAULT_LICENSE_PRICE_USD_CENTS,
            license_duration_seconds=DEFAULT_LICENSE_DURATION_SECONDS,
        )
        return cfg
    if (
        int(cfg.license_price_usd_cents or 0) == _LEGACY_YEARLY_PRICE_USD_CENTS
        and int(cfg.license_duration_seconds or 0) == _LEGACY_YEARLY_DURATION_SECONDS
    ):
        cfg.license_price_usd_cents = DEFAULT_LICENSE_PRICE_USD_CENTS
        cfg.license_duration_seconds = DEFAULT_LICENSE_DURATION_SECONDS
        logger.info("migrated license pricing $99/year -> $20/month")
    return cfg


def get_config() -> Dict:
    cfg = _ensure_config()
    return {
        "success": True,
        "config": {
            "file_registry_canister_id": cfg.file_registry_canister_id or "",
            "casals_frontend_canister_id": cfg.casals_frontend_canister_id or "",
            "billing_service_principal": cfg.billing_service_principal or "",
            "license_price_usd_cents": int(cfg.license_price_usd_cents or DEFAULT_LICENSE_PRICE_USD_CENTS),
            "license_duration_seconds": int(cfg.license_duration_seconds or DEFAULT_LICENSE_DURATION_SECONDS),
            "reviewers": get_reviewers(),
            "admins": get_admins(),
        },
    }


# ---------------------------------------------------------------------------
# Reviewers (issue #267)
#
# Reviewing submissions is a job; controlling the canister is a key. Keeping
# them separate means a reviewer can be added and removed without handing out
# (or taking back) the ability to upgrade the marketplace.
# ---------------------------------------------------------------------------


def get_reviewers() -> list:
    cfg = _ensure_config()
    raw = getattr(cfg, "reviewers", "") or ""
    return [p.strip() for p in raw.split(",") if p.strip()]


def is_reviewer(principal: str) -> bool:
    """Controllers and admins are implicitly reviewers, so a fresh marketplace
    is usable before anyone has been appointed."""
    if is_admin(principal):
        return True
    return (principal or "").strip() in get_reviewers()


def add_reviewer(principal: str) -> Dict:
    err = _require_admin()
    if not err["success"]:
        return err
    principal = (principal or "").strip()
    if not principal:
        return {"success": False, "error": "principal is required"}
    reviewers = get_reviewers()
    if principal not in reviewers:
        reviewers.append(principal)
        _ensure_config().reviewers = ",".join(reviewers)
        logger.info(f"add_reviewer -> {principal}")
    return {"success": True, "reviewers": reviewers}


def remove_reviewer(principal: str) -> Dict:
    err = _require_admin()
    if not err["success"]:
        return err
    principal = (principal or "").strip()
    reviewers = [p for p in get_reviewers() if p != principal]
    _ensure_config().reviewers = ",".join(reviewers)
    logger.info(f"remove_reviewer -> {principal}")
    return {"success": True, "reviewers": reviewers}


# ---------------------------------------------------------------------------
# Admins
#
# An admin runs the marketplace from its UI: every listing, every user, every
# license, and the reviewer list. Who is an admin is still a key holder's
# decision, so only controllers add or remove them. Canister settings (file
# registry, billing principal, pricing) stay controller-only.
# ---------------------------------------------------------------------------


def get_admins() -> list:
    cfg = _ensure_config()
    raw = getattr(cfg, "admins", "") or ""
    return [p.strip() for p in raw.split(",") if p.strip()]


def is_admin(principal: str) -> bool:
    """Controllers are implicitly admins. ``principal`` must be the caller:
    the controller check is made against ``ic.caller()``."""
    if _is_controller():
        return True
    return (principal or "").strip() in get_admins()


def caller_is_admin() -> bool:
    try:
        return is_admin(str(ic.caller()))
    except Exception:
        return False


def _require_admin() -> Dict:
    if not caller_is_admin():
        return {"success": False, "error": "Unauthorized: admins only"}
    return {"success": True}


def add_admin(principal: str) -> Dict:
    err = _require_controller()
    if not err["success"]:
        return err
    principal = (principal or "").strip()
    if not principal:
        return {"success": False, "error": "principal is required"}
    if len(principal) > 128:
        return {"success": False, "error": "principal too long"}
    admins = get_admins()
    if principal not in admins:
        admins.append(principal)
        _ensure_config().admins = ",".join(admins)
        logger.info(f"add_admin -> {principal}")
    return {"success": True, "admins": admins}


def remove_admin(principal: str) -> Dict:
    err = _require_controller()
    if not err["success"]:
        return err
    principal = (principal or "").strip()
    admins = [p for p in get_admins() if p != principal]
    _ensure_config().admins = ",".join(admins)
    logger.info(f"remove_admin -> {principal}")
    return {"success": True, "admins": admins}


def get_file_registry_canister_id() -> str:
    cfg = _ensure_config()
    return cfg.file_registry_canister_id or ""


def get_casals_frontend_canister_id() -> str:
    cfg = _ensure_config()
    return cfg.casals_frontend_canister_id or ""


def set_casals_frontend_canister_id(canister_id: str) -> Dict:
    err = _require_controller()
    if not err["success"]:
        return err
    cfg = _ensure_config()
    cfg.casals_frontend_canister_id = canister_id.strip()
    logger.info(
        f"set_casals_frontend_canister_id -> {cfg.casals_frontend_canister_id!r}"
    )
    return {
        "success": True,
        "casals_frontend_canister_id": cfg.casals_frontend_canister_id,
    }


def set_file_registry_canister_id(canister_id: str) -> Dict:
    err = _require_controller()
    if not err["success"]:
        return err
    cfg = _ensure_config()
    cfg.file_registry_canister_id = canister_id.strip()
    logger.info(f"set_file_registry_canister_id -> {cfg.file_registry_canister_id!r}")
    return {"success": True, "file_registry_canister_id": cfg.file_registry_canister_id}


def get_billing_service_principal() -> str:
    cfg = _ensure_config()
    return cfg.billing_service_principal or ""


def set_billing_service_principal(principal: str) -> Dict:
    err = _require_controller()
    if not err["success"]:
        return err
    cfg = _ensure_config()
    cfg.billing_service_principal = principal.strip()
    logger.info(f"set_billing_service_principal -> {cfg.billing_service_principal!r}")
    return {"success": True, "billing_service_principal": cfg.billing_service_principal}


def get_license_pricing() -> Dict:
    cfg = _ensure_config()
    return {
        "success": True,
        "pricing": {
            "license_price_usd_cents": int(cfg.license_price_usd_cents or DEFAULT_LICENSE_PRICE_USD_CENTS),
            "license_duration_seconds": int(cfg.license_duration_seconds or DEFAULT_LICENSE_DURATION_SECONDS),
        },
    }


def set_license_pricing(usd_cents: int, duration_seconds: int) -> Dict:
    err = _require_controller()
    if not err["success"]:
        return err
    if usd_cents <= 0 or duration_seconds <= 0:
        return {"success": False, "error": "pricing values must be positive"}
    cfg = _ensure_config()
    cfg.license_price_usd_cents = int(usd_cents)
    cfg.license_duration_seconds = int(duration_seconds)
    logger.info(
        f"set_license_pricing -> {cfg.license_price_usd_cents} cents / {cfg.license_duration_seconds}s"
    )
    return {
        "success": True,
        "license_price_usd_cents": cfg.license_price_usd_cents,
        "license_duration_seconds": cfg.license_duration_seconds,
    }


def init_config_from_args(file_registry_canister_id: str, billing_service_principal: str) -> None:
    """Best-effort initialisation called from main.py on canister init.

    Empty strings leave the existing value untouched (so a re-deploy
    with no init args doesn't clobber the configured registry id).
    """
    cfg = _ensure_config()
    if file_registry_canister_id:
        cfg.file_registry_canister_id = file_registry_canister_id.strip()
        logger.info(f"init: file_registry_canister_id = {cfg.file_registry_canister_id!r}")
    if billing_service_principal:
        cfg.billing_service_principal = billing_service_principal.strip()
        logger.info(f"init: billing_service_principal = {cfg.billing_service_principal!r}")
    if not cfg.license_price_usd_cents:
        cfg.license_price_usd_cents = DEFAULT_LICENSE_PRICE_USD_CENTS
    if not cfg.license_duration_seconds:
        cfg.license_duration_seconds = DEFAULT_LICENSE_DURATION_SECONDS
