"""Marketplace status — health check + counts."""

import sys
from typing import Any, Dict

from _cdk import ic
from api.config import get_config
from core.models import (
    AssistantListingEntity,
    CodexListingEntity,
    DeveloperLicenseEntity,
    ExtensionListingEntity,
    LikeEntity,
    PurchaseEntity,
)
from ic_python_logging import get_logger

logger = get_logger("api.status")


def _stamped(value: str) -> str:
    """A value stamped at build time (scripts/stamp_build.py), or "" if this
    build wasn't stamped."""
    return "" if "PLACEHOLDER" in value else value


def get_status() -> Dict[str, Any]:
    logger.info("Marketplace status check requested")

    try:
        extensions_count = ExtensionListingEntity.count()
    except Exception:
        extensions_count = 0
    try:
        codices_count = CodexListingEntity.count()
    except Exception:
        codices_count = 0
    try:
        assistants_count = AssistantListingEntity.count()
    except Exception:
        assistants_count = 0
    try:
        purchases_count = PurchaseEntity.count()
    except Exception:
        purchases_count = 0
    try:
        likes_count = LikeEntity.count()
    except Exception:
        likes_count = 0
    try:
        licenses_count = DeveloperLicenseEntity.count()
    except Exception:
        licenses_count = 0

    cfg = get_config().get("config", {})

    is_caller_controller = False
    try:
        is_caller_controller = bool(ic.is_controller(ic.caller()))
    except Exception:
        pass

    return {
        "version": _stamped("VERSION_PLACEHOLDER"),
        "commit": _stamped("COMMIT_HASH_PLACEHOLDER"),
        "commit_datetime": _stamped("COMMIT_DATETIME_PLACEHOLDER"),
        "status": "ok",
        "extensions_count": extensions_count,
        "codices_count": codices_count,
        "assistants_count": assistants_count,
        "purchases_count": purchases_count,
        "likes_count": likes_count,
        "licenses_count": licenses_count,
        "file_registry_canister_id": cfg.get("file_registry_canister_id", ""),
        "billing_service_principal": cfg.get("billing_service_principal", ""),
        "license_price_usd_cents": int(cfg.get("license_price_usd_cents", 0)),
        "license_duration_seconds": int(cfg.get("license_duration_seconds", 0)),
        "is_caller_controller": is_caller_controller,
        "dependencies": [
            d
            for d in (
                "ic-basilisk==BASILISK_VERSION_PLACEHOLDER",
                "ic-basilisk-toolkit==IC_BASILISK_TOOLKIT_VERSION_PLACEHOLDER",
                "ic-python-db==IC_PYTHON_DB_VERSION_PLACEHOLDER",
                "ic-python-logging==IC_PYTHON_LOGGING_VERSION_PLACEHOLDER",
            )
            if _stamped(d)
        ],
        "python_version": sys.version,
    }
