from typing import Optional

from ic_python_db import (
    Entity,
    Integer,
    ManyToOne,
    OneToMany,
    String,
    TimestampedMixin,
)
from ic_python_logging import get_logger

logger = get_logger("entity.transfer")


class Transfer(Entity, TimestampedMixin):
    """
    Represents a token transfer on the ledger.
    
    Uses ic-basilisk-toolkit Wallet natively for ICRC-1 transfers.
    Pre/post hooks allow custom logic via Codex overrides.
    
    Accounting Integration:
    - Call record_accounting() after creating a Transfer to auto-generate LedgerEntry
    - Override accounting_hook() via Codex for custom accounting logic
    """
    
    __alias__ = "id"
    id = String()
    principal_from = String()
    principal_to = String()
    subaccount = String(max_length=64)  # Hex-encoded destination subaccount
    invoice = ManyToOne("Invoice", "transfers")  # Linked invoice if this paid one
    instrument = String()
    amount = Integer()
    timestamp = String()
    tags = String()
    status = String()
    ledger_entries = OneToMany("LedgerEntry", "transfer")

    def execute(self):
        """
        Execute this transfer via ic-basilisk-toolkit Wallet (ICRC-1).
        
        Calls pre_execute_hook() before and post_execute_hook() after.
        Either hook can be overridden via Codex for custom logic.
        
        Must be called with ``yield``::
        
            result = yield transfer.execute()
        
        Returns:
            dict with {"ok": tx_id} on success or {"err": ...} on failure.
        """
        from core.realm_currency import no_treasury_token_wallet_error
        from ic_basilisk_toolkit.wallet import Wallet

        token_name = (self.instrument or "").strip()
        if not token_name:
            result = no_treasury_token_wallet_error()
            logger.error(f"Transfer.execute: refused — {result['err']}")
            self.status = "failed"
            return result

        logger.info(
            f"Transfer.execute: {self.amount} {token_name} "
            f"from {self.principal_from} to {self.principal_to}"
        )

        # Pre-execute hook (overridable via Codex)
        pre_result = self.pre_execute_hook()
        if pre_result is not None:
            # Hook returned a value — abort the transfer
            logger.info(f"Transfer.execute: pre_execute_hook returned {pre_result}, aborting")
            self.status = "aborted"
            return pre_result

        self.status = "executing"

        try:
            wallet = Wallet()

            # Convert hex subaccount strings to bytes if present
            to_sub = bytes.fromhex(self.subaccount) if self.subaccount else None

            result = yield wallet.transfer(
                token_name=token_name,
                to_principal=self.principal_to,
                amount=self.amount,
                to_subaccount=to_sub,
            )

            if isinstance(result, dict) and "ok" in result:
                tx_id = str(result["ok"])
                if not self.id:
                    self.id = tx_id
                self.status = "completed"
                logger.info(f"Transfer.execute: completed, tx_id={tx_id}")
            else:
                self.status = "failed"
                logger.error(f"Transfer.execute: failed — {result}")

        except Exception as e:
            self.status = "failed"
            result = {"err": str(e)}
            logger.error(f"Transfer.execute: exception — {e}")

        # Post-execute hook (overridable via Codex)
        self.post_execute_hook(result)

        return result

    @staticmethod
    def pre_execute_hook(transfer=None):
        """
        Called before executing the ICRC-1 transfer.
        Override via Codex to add custom validation or side-effects.
        
        Return None to proceed with the transfer.
        Return any other value to abort (that value becomes the result).
        """
        return None

    @staticmethod
    def post_execute_hook(transfer=None, result=None):
        """
        Called after the ICRC-1 transfer completes (success or failure).
        Override via Codex to add custom post-transfer logic
        (e.g., record accounting, send notifications).
        
        Args:
            transfer: The Transfer instance
            result: The transfer result dict ({"ok": tx_id} or {"err": ...})
        """
        pass

    def record_accounting(
        self,
        fund: Optional["Fund"] = None,
        fiscal_period: Optional["FiscalPeriod"] = None,
        revenue_category: Optional[str] = None,
        expense_category: Optional[str] = None,
        description: Optional[str] = None
    ) -> list:
        """
        Record accounting entries for this transfer (out-of-the-box).
        
        Creates balanced double-entry LedgerEntry records:
        - Incoming transfer (to vault): Debit Cash, Credit Revenue
        - Outgoing transfer (from vault): Debit Expense, Credit Cash
        
        Args:
            fund: Optional Fund to categorize the entries
            fiscal_period: Optional FiscalPeriod for the entries
            revenue_category: Category for incoming transfers (default: 'fee')
            expense_category: Category for outgoing transfers (default: 'services')
            description: Optional description override
            
        Returns:
            List of created LedgerEntry instances
        """
        from .ledger_entry import Category, EntryType, LedgerEntry
        
        # Use hook for custom logic (can be overridden via Codex)
        custom_entries = self.accounting_hook(
            fund=fund,
            fiscal_period=fiscal_period,
            revenue_category=revenue_category,
            expense_category=expense_category,
            description=description
        )
        if custom_entries is not None:
            return custom_entries
        
        # Default out-of-the-box accounting logic
        transaction_id = f"TXN-TRF-{self.id}"
        entry_date = self.timestamp or ""
        desc = description or f"Transfer {self.id}"
        
        # Determine if this is incoming (revenue) or outgoing (expense)
        # by checking if principal_to matches vault/realm principal
        is_incoming = bool(self.subaccount)  # Subaccount implies incoming to vault
        
        entries = []
        if is_incoming:
            # Incoming: Debit Cash, Credit Revenue
            rev_cat = revenue_category or Category.FEE
            entries = [
                {
                    "entry_type": EntryType.ASSET,
                    "category": Category.CASH,
                    "debit": self.amount,
                    "credit": 0,
                    "entry_date": entry_date,
                    "description": f"{desc} - Cash received",
                    "tags": self.tags,
                },
                {
                    "entry_type": EntryType.REVENUE,
                    "category": rev_cat,
                    "debit": 0,
                    "credit": self.amount,
                    "entry_date": entry_date,
                    "description": f"{desc} - Revenue",
                    "tags": self.tags,
                }
            ]
        else:
            # Outgoing: Debit Expense, Credit Cash
            exp_cat = expense_category or Category.SERVICES
            entries = [
                {
                    "entry_type": EntryType.EXPENSE,
                    "category": exp_cat,
                    "debit": self.amount,
                    "credit": 0,
                    "entry_date": entry_date,
                    "description": f"{desc} - Expense",
                    "tags": self.tags,
                },
                {
                    "entry_type": EntryType.ASSET,
                    "category": Category.CASH,
                    "debit": 0,
                    "credit": self.amount,
                    "entry_date": entry_date,
                    "description": f"{desc} - Cash disbursed",
                    "tags": self.tags,
                }
            ]
        
        # Add fund and fiscal_period to all entries
        for entry in entries:
            if fund:
                entry["fund"] = fund
            if fiscal_period:
                entry["fiscal_period"] = fiscal_period
            entry["transfer"] = self
        
        created = LedgerEntry.create_transaction(transaction_id, entries)
        logger.info(f"Created {len(created)} accounting entries for Transfer {self.id}")
        return created

    @staticmethod
    def accounting_hook(
        transfer: "Transfer" = None,
        fund=None,
        fiscal_period=None,
        revenue_category=None,
        expense_category=None,
        description=None
    ):
        """
        Hook for custom accounting logic. Override via Codex.
        
        Return None to use default logic, or return a list of LedgerEntry
        to use custom entries instead.
        """
        return None
