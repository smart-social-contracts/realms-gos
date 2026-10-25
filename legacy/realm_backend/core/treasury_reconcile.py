"""Reconcile realm treasury token metadata against the configured ledger."""

from ic_python_logging import get_logger

logger = get_logger("core.treasury_reconcile")

TREASURY_RECONCILE_TASK_NAME = "treasury_token_reconcile"
TREASURY_RECONCILE_STEP_CODE = (
    "def async_task():\n"
    "    from core.treasury_reconcile import reconcile_treasury_token\n"
    "    res = yield from reconcile_treasury_token()\n"
    "    return res\n"
)


def reconcile_treasury_token():
    """Generator: re-resolve the treasury symbol from the configured ledger."""
    try:
        from ggg import Realm

        realm = Realm.load("1")
        if not realm:
            return {
                "success": True,
                "skipped": True,
                "reason": "no_treasury_ledger",
            }

        ledger = str(getattr(realm, "token_canister_id", "") or "").strip()
        if not ledger:
            return {
                "success": True,
                "skipped": True,
                "reason": "no_treasury_ledger",
            }

        from api.tokens import register_treasury_token, resolve_ledger_token_info

        resolved = yield from resolve_ledger_token_info(ledger)
        if not resolved.get("success"):
            return {
                "success": False,
                "error": resolved.get("error", "Could not resolve ledger metadata"),
                "error_code": "ledger_unresolvable",
            }

        symbol = str(resolved.get("symbol") or "").strip()
        decimals = int(resolved.get("decimals", 8))
        indexer = str(resolved.get("indexer_canister_id") or ledger).strip()

        stored_symbol = str(getattr(realm, "accounting_currency", "") or "").strip()
        stored_decimals = int(getattr(realm, "accounting_currency_decimals", 8) or 8)
        changed = symbol != stored_symbol or decimals != stored_decimals

        if changed:
            realm.accounting_currency = symbol[:16]
            realm.accounting_currency_decimals = decimals
            logger.info(
                f"Treasury token reconciled: {stored_symbol}/{stored_decimals} -> "
                f"{symbol}/{decimals} (ledger {ledger})"
            )

        register_treasury_token(
            symbol=symbol,
            ledger_canister_id=ledger,
            indexer_canister_id=indexer,
            decimals=decimals,
        )

        return {
            "success": True,
            "symbol": symbol,
            "decimals": decimals,
            "ledger": ledger,
            "changed": changed,
        }
    except Exception as e:
        logger.error(f"treasury reconcile failed: {e}")
        return {"success": False, "error": str(e)}


def schedule_treasury_reconcile_on_boot() -> None:
    """Schedule a one-shot reconcile shortly after init/post_upgrade."""
    from core.quarter_bootstrap import seed_recurring_codex_task

    seed_recurring_codex_task(
        TREASURY_RECONCILE_TASK_NAME,
        TREASURY_RECONCILE_STEP_CODE,
        0,
    )
    logger.info("Treasury token reconcile scheduled")
