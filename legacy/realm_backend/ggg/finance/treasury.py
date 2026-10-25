import json
import traceback
try:
    from pprint import pformat
except ImportError:
    pformat = repr

try:
    from _cdk import Async, ic
except ImportError:
    from typing import List
    Async = List  # subscriptable placeholder for type annotations
    ic = None
from ic_python_db import Entity, Integer, OneToOne, String, TimestampedMixin
from ic_python_logging import get_logger

try:
    from core.extensions import extension_async_call
except ImportError:
    try:
        from ..core.extensions import extension_async_call
    except ImportError:
        extension_async_call = None

logger = get_logger("entity.treasury")


class Treasury(Entity, TimestampedMixin):
    __alias__ = "name"
    name = String(min_length=2, max_length=256)
    balance = Integer(default=0)
    realm = OneToOne("Realm", "treasury")

    def refresh(self) -> Async:
        """Refresh treasury data from embedded vault extension"""
        try:
            logger.info(f"Refreshing treasury '{self.name}'")
            result = yield extension_async_call("vault", "refresh", "{}")
            result_data = json.loads(result)
            if not result_data.get("success"):
                logger.error(f"Failed to refresh vault: {result_data.get('error')}")
                return
            summary = result_data["data"]["TransactionSummary"]
            logger.info(
                f"Synced {summary['new_txs_count']} new transactions for treasury '{self.name}'"
            )
            return result
        except Exception as e:
            logger.error(
                f"Error refreshing treasury '{self.name}': {str(e)}\n{traceback.format_exc()}"
            )

    @staticmethod
    def send_hook(treasury: "Treasury", to_principal: str, amount: int) -> Async[str]:
        """Hook for sending tokens from treasury. Can be overridden by codex."""
        raise NotImplementedError("Treasury send hook has not been implemented")

    def send(self, to_principal: str, amount: int) -> Async[str]:
        """Send tokens from treasury to a principal.

        Dispatch order (issue #244): the active hook-API codex's
        ``on_treasury_send`` hook when implemented, else the legacy
        ``send_hook`` (monkey-patched by pre-hook codices).
        """

        logger.info(f"Treasury '{self.name}' sending {amount} tokens to {to_principal}")

        try:
            from core.codex_hooks import treasury_send_async

            result = yield from treasury_send_async(
                self.name, to_principal, amount
            )
            if result is not None:
                return result
        except Exception as e:
            logger.error(
                f"Treasury send hook failed for '{self.name}': {e}\n{traceback.format_exc()}"
            )
            raise

        result = yield self.send_hook(self, to_principal, amount)

        return result

    # def sync_balance(self) -> Async[None]:
    #     """Sync treasury balance from embedded vault extension"""
    #     try:
    #         logger.info(f"Syncing balance for treasury '{self.name}'")

    #         from core.extensions import extension_async_call

    #         # First, refresh transactions from ICRC ledger
    #         refresh_result = yield extension_async_call("vault", "refresh", "{}")

    #         refresh_data = json.loads(refresh_result)
    #         if not refresh_data.get("success"):
    #             logger.error(
    #                 f"Failed to refresh vault: {refresh_data.get('error')}"
    #             )
    #             return

    #         summary = refresh_data["data"]["TransactionSummary"]
    #         logger.info(
    #             f"Synced {summary['new_txs_count']} new transactions for treasury '{self.name}'"
    #         )

    #         # Then get updated balance
    #         balance_result = yield extension_async_call(
    #             "vault",
    #             "get_balance",
    #             json.dumps({"principal_id": ic.id().to_str()}),
    #         )

    #         balance_data = json.loads(balance_result)
    #         if balance_data.get("success"):
    #             self.balance = balance_data["data"]["Balance"]["amount"]
    #             logger.info(
    #                 f"Updated treasury '{self.name}' balance to {self.balance}"
    #             )
    #         else:
    #             logger.error(
    #                 f"Failed to get balance: {balance_data.get('error')}"
    #             )

    #     except Exception as e:
    #         logger.error(
    #             f"Error syncing treasury balance: {str(e)}\n{traceback.format_exc()}"
    #         )

    # def refresh(self) -> Async:
    #     """Refresh treasury data from embedded vault extension"""
    #     try:
    #         logger.info(f"Refreshing treasury '{self.name}'")

    #         from core.extensions import extension_async_call

    #         # Sync balance from vault
    #         yield self.sync_balance()

    #         # Get transactions for this treasury's principal
    #         args = json.dumps({"principal_id": ic.id().to_str()})

    #         transactions_result = yield extension_async_call(
    #             "vault", "get_transactions", args
    #         )

    #         transactions_data = json.loads(transactions_result)
    #         if not transactions_data.get("success"):
    #             logger.error(
    #                 f"Failed to get transactions: {transactions_data.get('error')}"
    #             )
    #             return

    #         transactions_list = transactions_data["data"]["Transactions"]
    #         logger.info(
    #             f"Retrieved {len(transactions_list)} transactions for treasury '{self.name}'"
    #         )
    #         logger.debug("transactions_list: %s" % pformat(transactions_list))

    #         # Get vault status
    #         status_result = yield extension_async_call("vault", "get_status", "{}")
    #         status_data = json.loads(status_result)
    #         if status_data.get("success"):
    #             logger.debug("vault status: %s" % pformat(status_data["data"]["Stats"]))

    #         # Process transactions and create Transfer entities
    #         for transaction in transactions_list:
    #             logger.info(
    #                 f"Processing transaction {transaction['id']} for treasury '{self.name}'"
    #             )
    #             logger.debug("transaction: %s" % pformat(transaction))

    #             tx_id = transaction["id"]
    #             t = Transfer[tx_id]
    #             if not t:
    #                 t = Transfer(_id=tx_id)

    #             t.principal_from = transaction["principal_from"]
    #             t.principal_to = transaction["principal_to"]
    #             t.amount = transaction["amount"]
    #             # t.timestamp = transaction['timestamp']
    #             # t.kind = transaction['kind']

    #     except Exception as e:
    #         logger.error(
    #             f"Error refreshing treasury: {str(e)}\n{traceback.format_exc()}"
