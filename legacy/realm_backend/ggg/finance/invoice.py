import uuid
from datetime import datetime
from typing import Optional

try:
    from _cdk import (
        Async,
        Opt,
        Principal,
        Record,
        Service,
        Variant,
        Vec,
        blob,
        ic,
        nat,
        service_query,
        text,
    )
    _CDK_AVAILABLE = True
except ImportError:
    from typing import Any, List
    Async = List  # subscriptable placeholder for type annotations
    Principal = Any
    ic = None
    _CDK_AVAILABLE = False

    # Minimal stub base-classes so module-level Record/Service/Variant
    # subclasses don't explode in non-ICP (test / CLI) environments.
    class Record:       # type: ignore[no-redef]
        pass
    class Service:      # type: ignore[no-redef]
        pass
    class Variant:      # type: ignore[no-redef]
        def __init_subclass__(cls, **kwargs):
            pass

    def service_query(f):   # type: ignore[no-redef]
        return f

    Vec = list
    blob = bytes
    nat = int
    text = str
    Opt = Optional

from ic_python_db import Entity, Float, Integer, ManyToOne, OneToMany, String, TimestampedMixin
from ic_python_logging import get_logger

# Try ICP-compatible random, fall back to uuid for CLI/regular Python
try:
    from core.random import generate_unique_id
except ImportError:
    def generate_unique_id(prefix: str = "", length: int = 12) -> str:
        """Fallback using uuid.uuid4() for non-ICP environments."""
        return f"{prefix}{uuid.uuid4().hex[:length]}"

logger = get_logger("entity.invoice")

DEFAULT_DECIMALS = 8


def _now_iso() -> str:
    """Return the current time as an ISO string, working in the ICP canister."""
    try:
        from ic_basilisk_toolkit.date_utils import epoch_to_datetime_str, ic_time_to_epoch
        if ic is not None:
            return epoch_to_datetime_str(ic_time_to_epoch(int(ic.time()))).replace(" ", "T")
    except Exception:
        pass
    try:
        return datetime.utcnow().isoformat()
    except Exception:
        return ""

# ---------------------------------------------------------------------------
# Payment mode
# ---------------------------------------------------------------------------
#
# SUBACCOUNT_PAYMENTS_ENABLED controls whether invoices use per-invoice
# subaccounts (the native ICRC-1 mechanism) or the nonce-suffix fallback.
#
# Subaccounts are the *correct* long-term approach: each invoice gets a
# 32-byte subaccount derived from its ID.  Users send tokens to
# (vault_principal, invoice_subaccount) and refresh() checks the balance on
# that subaccount to detect payment.
#
# The problem: as of 2026, most ICP ecosystem wallets (Plug, Stoic, NFID,
# Oisy, …) cannot specify a subaccount when initiating a transfer.  Until
# wallet support matures, the nonce-suffix approach is used instead:
#
#   • Each invoice is assigned a small random integer nonce (1–NONCE_MAX).
#   • Users are shown the *exact* amount to transfer:
#         base_amount_raw + nonce  (in smallest token units)
#     e.g. 1.002347 ckUSDC  instead of  1.000000 ckUSDC
#   • refresh() scans the token's ICRC-1 indexer for an incoming transfer
#     to the canister's main account with that exact raw amount, and marks
#     the invoice paid on match.
#
# TODO: Set SUBACCOUNT_PAYMENTS_ENABLED = True once major ICP wallets
#       support subaccount-addressed transfers.
#
SUBACCOUNT_PAYMENTS_ENABLED = False

# Nonce range.  The nonce is added (in raw units) to the invoice amount so
# each pending invoice requests a *unique* exact amount from the user.
# Keeping it small limits the tiny "overpayment" built into the request:
#   ckUSDC (6 dec): NONCE_MAX = 999 → max extra ≈ $0.001
#   ckBTC  (8 dec): NONCE_MAX = 999 → max extra ≈ $0.6 at $60k / BTC
NONCE_MIN = 1
NONCE_MAX = 999


# ---------------------------------------------------------------------------
# ICRC-1 indexer Candid types — used by _refresh_by_nonce()
# Defined at module level (not inside the method) so the CDK's type system
# can resolve them correctly at runtime.
# ---------------------------------------------------------------------------

class _IcrcAccount(Record):
    owner: Principal
    subaccount: Opt[blob]


class _IcrcTransfer(Record):
    # Only the fields we need; the CDK ignores unknown fields from the response.
    # ('from' is omitted because it is a Python keyword and we don't need it.)
    amount: nat
    to: _IcrcAccount


class _IcrcTransaction(Record):
    kind: text
    transfer: Opt[_IcrcTransfer]


class _IcrcTransactionWithId(Record):
    id: nat
    transaction: _IcrcTransaction


class _GetAccountTransactionsArgs(Record):
    account: _IcrcAccount
    start: Opt[nat]
    max_results: nat


class _GetAccountTransactionsOk(Record):
    balance: nat
    transactions: Vec[_IcrcTransactionWithId]


class _GetAccountTransactionsResult(Variant, total=False):
    Ok: _GetAccountTransactionsOk
    Err: text


class _ICRC1IndexerService(Service):
    @service_query
    def get_account_transactions(
        self, args: _GetAccountTransactionsArgs
    ) -> _GetAccountTransactionsResult:
        ...


# ---------------------------------------------------------------------------
# Invoice entity
# ---------------------------------------------------------------------------

class Invoice(Entity, TimestampedMixin):
    __owner_field__ = "user"  # realms#282 — SecureORM ownership stamp/protect
    """
    Represents an invoice denominated in the realm's currency.

    Payment identification
    ─────────────────────
    Two modes are supported, toggled by SUBACCOUNT_PAYMENTS_ENABLED:

    • Subaccount mode  (SUBACCOUNT_PAYMENTS_ENABLED = True)
        The canonical ICRC-1 approach.  Each invoice has a unique 32-byte
        subaccount derived from its ID (invoice_id padded with null bytes).
        Users pay by sending tokens to (vault_principal, invoice_subaccount).
        refresh() checks the balance on that subaccount via the Wallet.

    • Nonce-suffix mode  (SUBACCOUNT_PAYMENTS_ENABLED = False)  ← ACTIVE
        Temporary fallback while ICP wallets lack subaccount support.
        A small random nonce (1–NONCE_MAX) is stored on the invoice.
        Users are asked to send *exactly* (base_amount_raw + nonce) tokens
        to the canister's main principal — the unique last digits serve as
        the identifier.  refresh() scans the token's ICRC-1 indexer for an
        incoming transfer with that exact raw amount.
    """

    __alias__ = "id"
    id = String(max_length=32)  # Max 32 chars to fit in subaccount
    amount = Float()            # Amount in accounting currency (e.g. 10.00 ckUSDC)
    currency = String(max_length=16, default="")
    due_date = String(max_length=64)
    status = String(max_length=32)   # Pending | Paid | Overdue | Expired
    user = ManyToOne("User", "invoices")
    transfers = OneToMany("Transfer", "invoice")
    ledger_entries = OneToMany("LedgerEntry", "invoice")
    paid_at = String(max_length=64)
    metadata = String(max_length=256)

    payment_currency = String(max_length=16)
    payment_amount = Float()
    payment_amount_raw = Integer()

    # Nonce for payment identification when subaccounts are unavailable.
    # Zero means "not yet assigned".
    payment_nonce = Integer(default=0)

    def __init__(self, **kwargs):
        if "id" not in kwargs and "_id" not in kwargs:
            kwargs["id"] = generate_unique_id("inv_")
        if not kwargs.get("currency"):
            try:
                from core.realm_currency import realm_currency

                acct = realm_currency()
                if acct:
                    kwargs["currency"] = acct[:16]
            except Exception:
                pass
        super().__init__(**kwargs)
        # Auto-assign a nonce in nonce-suffix mode so callers don't have to
        # remember to call assign_nonce() separately.
        if not SUBACCOUNT_PAYMENTS_ENABLED and not self.payment_nonce:
            self.payment_nonce = self._generate_nonce()

    # ------------------------------------------------------------------
    # Subaccount helpers
    # (kept intact; bypassed when SUBACCOUNT_PAYMENTS_ENABLED = False)
    # ------------------------------------------------------------------

    def get_subaccount(self) -> bytes:
        """
        Return the 32-byte subaccount for this invoice.

        The invoice ID is UTF-8 encoded and right-padded with null bytes to
        32 bytes.  This allows O(1) reverse lookup: subaccount → invoice_id.

        NOTE: Not used while SUBACCOUNT_PAYMENTS_ENABLED = False.
        """
        return self.id.encode().ljust(32, b'\x00')

    def get_subaccount_hex(self) -> str:
        """Return the subaccount as a lowercase hex string."""
        return self.get_subaccount().hex()

    def get_subaccount_list(self) -> list:
        """Return the subaccount as a list[int] for ICRC-1 API calls."""
        return list(self.get_subaccount())

    @staticmethod
    def from_subaccount(subaccount: bytes) -> "Invoice":
        """Look up an Invoice by its subaccount bytes."""
        invoice_id = subaccount.rstrip(b'\x00').decode()
        return Invoice[invoice_id]

    @staticmethod
    def id_from_subaccount(subaccount: bytes) -> str:
        """Extract the invoice ID string from a subaccount byte sequence."""
        return subaccount.rstrip(b'\x00').decode()

    # ------------------------------------------------------------------
    # Amount helpers
    # ------------------------------------------------------------------

    def get_amount_raw(self, decimals: int = None) -> int:
        """Return the invoice amount in smallest token units (base, no nonce)."""
        if decimals is None:
            decimals = DEFAULT_DECIMALS
        return int(self.amount * (10 ** decimals))

    def get_nonce_amount_raw(self, decimals: int = None) -> int:
        """
        Return the nonce-adjusted amount in smallest token units.

        This is the *exact* amount the user must send when subaccount-based
        routing is unavailable:

            base_amount_raw + payment_nonce

        Example (ckUSDC, 6 decimals, fee = 1.00, nonce = 2347):
            1_000_000 + 2347 = 1_002_347 raw → display as 1.002347 ckUSDC
        """
        if decimals is None:
            decimals = DEFAULT_DECIMALS
        return self.get_amount_raw(decimals) + (self.payment_nonce or 0)

    def get_nonce_amount_human(self, decimals: int = None) -> float:
        """Return get_nonce_amount_raw() converted to human-readable float."""
        if decimals is None:
            decimals = DEFAULT_DECIMALS
        return self.get_nonce_amount_raw(decimals) / (10 ** decimals)

    # ------------------------------------------------------------------
    # Nonce management
    # ------------------------------------------------------------------

    def _generate_nonce(self) -> int:
        """
        Generate a pseudo-random nonce in [NONCE_MIN, NONCE_MAX].

        Uses ic.time() (nanoseconds) on-chain.  Falls back to Python's
        random module in non-ICP environments (tests / CLI).

        Collision avoidance: scans existing pending invoices for the same
        currency and picks a different value if there is a conflict.
        """
        try:
            raw = int(ic.time()) if ic is not None else 0
        except Exception:
            raw = 0

        span = NONCE_MAX - NONCE_MIN + 1   # 9999

        if raw == 0:
            try:
                import random
                candidate = random.randint(NONCE_MIN, NONCE_MAX)
            except Exception:
                candidate = 1234
        else:
            candidate = raw % span + NONCE_MIN

        # Avoid collisions with other pending invoices for the same currency.
        # An unresolved currency has no pool of its own, so it avoids every
        # pending nonce: the invoice must stay unambiguous if its currency is
        # filled in later.
        my_currency = (self.currency or "").strip()
        used = {
            inv.payment_nonce
            for inv in Invoice.instances()
            if inv.status == "Pending"
            and (not my_currency or (inv.currency or "").strip() == my_currency)
            and inv.payment_nonce
            and inv.id != self.id
        }
        for offset in range(span):
            probe = NONCE_MIN + (candidate - NONCE_MIN + offset) % span
            if probe not in used:
                return probe
        raise RuntimeError(
            f"All {span} nonce slots for currency '{my_currency or '(unresolved)'}' "
            f"are occupied by pending invoices. Expire or pay existing invoices "
            f"before creating new ones."
        )

    def assign_nonce(self) -> int:
        """Assign a nonce if one has not been set yet.  Returns the nonce."""
        if not self.payment_nonce:
            self.payment_nonce = self._generate_nonce()
        return self.payment_nonce

    @staticmethod
    def find_by_nonce_amount(currency: str, amount_raw: int) -> "Optional[Invoice]":
        """
        Find the first pending invoice for *currency* whose nonce-adjusted
        raw amount equals *amount_raw*.

        Useful when processing an incoming transfer event to identify which
        invoice it belongs to.
        """
        currency = (currency or "").strip()
        if not currency:
            return None
        for inv in Invoice.instances():
            if inv.status != "Pending":
                continue
            inv_currency = (inv.currency or "").strip()
            if not inv_currency or inv_currency != currency:
                continue
            token = inv._find_token()
            decimals = (token.decimals if token else None) or DEFAULT_DECIMALS
            if inv.get_nonce_amount_raw(decimals) == amount_raw:
                return inv
        return None

    # ------------------------------------------------------------------
    # Payment address / instructions
    # ------------------------------------------------------------------

    def get_payment_address(self) -> dict:
        """
        Return the payment instructions for this invoice.

        • Subaccount mode  → principal + subaccount hex
        • Nonce-suffix mode → principal + exact nonce-adjusted amount to send
        """
        if SUBACCOUNT_PAYMENTS_ENABLED:
            # TODO: switch back to this path when ICP wallets support subaccounts.
            return {
                "principal": ic.id().to_str(),
                "subaccount": self.get_subaccount_hex(),
            }
        else:
            token = self._find_token()
            decimals = (token.decimals if token else None) or DEFAULT_DECIMALS
            nonce_raw = self.get_nonce_amount_raw(decimals)
            nonce_human = nonce_raw / (10 ** decimals)
            return {
                "principal": ic.id().to_str(),
                # No subaccount — send to main account only.
                "amount_raw": nonce_raw,
                "amount_human": nonce_human,
                "currency": self.currency,
                "nonce": self.payment_nonce,
                "note": (
                    f"Send EXACTLY {nonce_human} {self.currency} "
                    "to the principal above (no subaccount). "
                    "The unique last digits identify your invoice."
                ),
            }

    # ------------------------------------------------------------------
    # Token lookup
    # ------------------------------------------------------------------

    def _find_token(self):
        """Find the registered Token entity matching this invoice's currency."""
        from .token import Token

        invoice_currency = (self.currency or "").strip()
        if not invoice_currency:
            return None
        for token in Token.instances():
            if not token.indexer:
                continue
            token_symbol = getattr(token, "symbol", token.name) or token.name
            if token_symbol == invoice_currency or token.name == invoice_currency:
                return token
        return None

    def _get_token_decimals(self) -> int:
        """Return the decimal places for this invoice's currency token."""
        token = self._find_token()
        if token and token.decimals:
            try:
                return int(token.decimals)
            except Exception:
                pass
        return DEFAULT_DECIMALS

    # ------------------------------------------------------------------
    # Accounting
    # ------------------------------------------------------------------

    def record_accounting(
        self,
        fund: Optional["Fund"] = None,
        fiscal_period: Optional["FiscalPeriod"] = None,
        revenue_category: Optional[str] = None,
        description: Optional[str] = None
    ) -> list:
        """
        Record double-entry accounting entries on invoice creation.

        Debit:  Accounts Receivable (Asset)
        Credit: Deferred Revenue   (Liability)

        Returns the list of created LedgerEntry instances.
        """
        from .ledger_entry import Category, EntryType, LedgerEntry

        custom_entries = self.accounting_hook(
            invoice=self,
            event="created",
            fund=fund,
            fiscal_period=fiscal_period,
            revenue_category=revenue_category,
            description=description
        )
        if custom_entries is not None:
            return custom_entries

        transaction_id = f"TXN-INV-{self.id}"
        entry_date = self.paid_at if self.paid_at and not self.paid_at.startswith("1970-01-01T00:00:00") else _now_iso()
        desc = description or f"Invoice {self.id}"
        currency = (self.currency or "").strip()
        if not currency:
            from core.realm_currency import no_treasury_token_error

            raise ValueError(no_treasury_token_error()["error"])
        amount_raw = self.get_amount_raw(self._get_token_decimals())

        entries = [
            {
                "entry_type": EntryType.ASSET,
                "category": Category.RECEIVABLE,
                "debit": amount_raw,
                "credit": 0,
                "currency": currency,
                "entry_date": entry_date,
                "description": f"{desc} - Receivable ({currency})",
            },
            {
                "entry_type": EntryType.LIABILITY,
                "category": Category.DEFERRED_REVENUE,
                "debit": 0,
                "credit": amount_raw,
                "currency": currency,
                "entry_date": entry_date,
                "description": f"{desc} - Deferred revenue ({currency})",
            }
        ]

        for entry in entries:
            if fund:
                entry["fund"] = fund
            if fiscal_period:
                entry["fiscal_period"] = fiscal_period
            entry["invoice"] = self
            if self.user:
                entry["user"] = self.user

        created = LedgerEntry.create_transaction(transaction_id, entries)
        logger.info(f"Created {len(created)} accounting entries for Invoice {self.id}")
        return created

    def record_payment(
        self,
        transfer: Optional["Transfer"] = None,
        fund: Optional["Fund"] = None,
        fiscal_period: Optional["FiscalPeriod"] = None,
        revenue_category: Optional[str] = None,
        description: Optional[str] = None
    ) -> list:
        """
        Record double-entry accounting entries on invoice payment.

        Debit:  Cash              (Asset)     — payment received
        Credit: Receivable        (Asset)     — receivable cleared
        Debit:  Deferred Revenue  (Liability) — deferred cleared
        Credit: Revenue                       — revenue recognised

        Returns the list of created LedgerEntry instances.
        """
        from .ledger_entry import Category, EntryType, LedgerEntry

        custom_entries = self.accounting_hook(
            invoice=self,
            event="paid",
            transfer=transfer,
            fund=fund,
            fiscal_period=fiscal_period,
            revenue_category=revenue_category,
            description=description
        )
        if custom_entries is not None:
            return custom_entries

        transaction_id = f"TXN-INV-PAY-{self.id}"
        entry_date = self.paid_at if self.paid_at and not self.paid_at.startswith("1970-01-01T00:00:00") else _now_iso()
        desc = description or f"Invoice {self.id} payment"
        rev_cat = revenue_category or Category.FEE
        currency = (self.currency or "").strip()
        if not currency:
            from core.realm_currency import no_treasury_token_error

            raise ValueError(no_treasury_token_error()["error"])
        amount_raw = self.get_amount_raw(self._get_token_decimals())

        entries = [
            {
                "entry_type": EntryType.ASSET,
                "category": Category.CASH,
                "debit": amount_raw,
                "credit": 0,
                "currency": currency,
                "entry_date": entry_date,
                "description": f"{desc} - Cash received ({currency})",
            },
            {
                "entry_type": EntryType.ASSET,
                "category": Category.RECEIVABLE,
                "debit": 0,
                "credit": amount_raw,
                "currency": currency,
                "entry_date": entry_date,
                "description": f"{desc} - Receivable cleared ({currency})",
            },
            {
                "entry_type": EntryType.LIABILITY,
                "category": Category.DEFERRED_REVENUE,
                "debit": amount_raw,
                "credit": 0,
                "currency": currency,
                "entry_date": entry_date,
                "description": f"{desc} - Deferred revenue cleared ({currency})",
            },
            {
                "entry_type": EntryType.REVENUE,
                "category": rev_cat,
                "debit": 0,
                "credit": amount_raw,
                "currency": currency,
                "entry_date": entry_date,
                "description": f"{desc} - Revenue recognised ({currency})",
            }
        ]

        for entry in entries:
            if fund:
                entry["fund"] = fund
            if fiscal_period:
                entry["fiscal_period"] = fiscal_period
            entry["invoice"] = self
            if transfer:
                entry["transfer"] = transfer
            if self.user:
                entry["user"] = self.user

        created = LedgerEntry.create_transaction(transaction_id, entries)
        logger.info(f"Created {len(created)} payment entries for Invoice {self.id}")
        return created

    # ------------------------------------------------------------------
    # mark_paid
    # ------------------------------------------------------------------

    def mark_paid(
        self,
        payment_currency: str = None,
        payment_amount: float = None,
        payment_amount_raw: int = None,
    ) -> None:
        """Mark this invoice as paid and record the timestamp."""
        from ic_basilisk_toolkit.date_utils import epoch_to_datetime_str, ic_time_to_epoch

        self.status = "Paid"
        try:
            if ic is not None:
                epoch_s = ic_time_to_epoch(int(ic.time()))
                if epoch_s > 0:
                    self.paid_at = epoch_to_datetime_str(epoch_s).replace(" ", "T")
                else:
                    self.paid_at = _now_iso()
            else:
                self.paid_at = _now_iso()
        except Exception:
            self.paid_at = ""
        if payment_currency:
            self.payment_currency = payment_currency
        if payment_amount is not None:
            self.payment_amount = payment_amount
        if payment_amount_raw is not None:
            self.payment_amount_raw = payment_amount_raw
        logger.info(
            f"Invoice {self.id} marked as paid at {self.paid_at} "
            f"({payment_amount} {payment_currency})"
        )
        try:
            from core.codex_hooks import dispatch_invoice_accounting

            dispatch_invoice_accounting(self.id, "paid")
        except Exception as e:
            logger.warning(
                f"Invoice {self.id}: accounting event dispatch failed: {e}"
            )

    def _sync_treasury_transactions(self, token) -> "Async[dict]":
        """Sync WalletTransfer cache from the token indexer after payment."""
        from ic_basilisk_toolkit.wallet import Wallet

        wallet = Wallet()
        try:
            result = yield wallet.refresh(token.name)
            logger.info(
                f"Invoice {self.id}: treasury sync — "
                f"{result.get('new_txs', 0)} new txs for {token.name}"
            )
            return result
        except Exception as e:
            logger.warning(f"Invoice {self.id}: treasury sync failed: {e}")
            return {"new_txs": 0, "error": str(e)}

    # ------------------------------------------------------------------
    # refresh — public entry point
    # ------------------------------------------------------------------

    def refresh(self) -> "Async[dict]":
        """
        Check whether this invoice has been paid.

        Delegates to _refresh_by_subaccount() or _refresh_by_nonce()
        depending on SUBACCOUNT_PAYMENTS_ENABLED.

        Must be called with ``yield``::

            result = yield invoice.refresh()
        """
        if SUBACCOUNT_PAYMENTS_ENABLED:
            return self._refresh_by_subaccount()
        else:
            return self._refresh_by_nonce()

    # ------------------------------------------------------------------
    # _refresh_by_subaccount  (disabled while SUBACCOUNT_PAYMENTS_ENABLED=False)
    # ------------------------------------------------------------------

    def _refresh_by_subaccount(self) -> "Async[dict]":
        """
        Check payment by querying the token balance on this invoice's
        dedicated 32-byte subaccount via the Basilisk OS Wallet.

        This is the canonical ICRC-1 mechanism.  Each invoice gets a unique
        subaccount so payments are unambiguous.

        TODO: This path is currently inactive (SUBACCOUNT_PAYMENTS_ENABLED=False).
              Re-enable once ICP wallets support specifying a subaccount on send.
        """
        from ic_basilisk_toolkit.wallet import Wallet

        wallet = Wallet()
        subaccount = self.get_subaccount()
        invoice_currency = (self.currency or "").strip()
        if not invoice_currency:
            from core.realm_currency import no_treasury_token_error

            err = no_treasury_token_error()
            return {
                "invoice_id": self.id,
                "status": self.status,
                "currency": "",
                **err,
            }

        token = self._find_token()
        if not token:
            return {
                "invoice_id": self.id,
                "status": self.status,
                "currency": invoice_currency,
                "error": f"No registered Token for '{invoice_currency}'",
            }

        try:
            token_result = yield wallet.refresh(token.name, subaccount=subaccount)
            balance = token_result.get("balance", 0)

            if balance > 0 and self.status == "Pending":
                token_decimals = token.decimals or 8
                human_balance = balance / (10 ** token_decimals)

                if human_balance >= self.amount:
                    logger.info(
                        f"Invoice {self.id}: subaccount payment {human_balance} "
                        f"{invoice_currency} >= {self.amount} {invoice_currency}"
                    )
                    self.mark_paid(
                        payment_currency=invoice_currency,
                        payment_amount=human_balance,
                        payment_amount_raw=balance,
                    )
                else:
                    logger.info(
                        f"Invoice {self.id}: partial subaccount payment "
                        f"{human_balance} < {self.amount} {invoice_currency}"
                    )

            return {
                "invoice_id": self.id,
                "status": self.status,
                "currency": invoice_currency,
                "payment_method": "subaccount",
                "results": {token.name: token_result},
            }
        except Exception as e:
            logger.error(f"Invoice {self.id}: error in subaccount refresh: {e}")
            return {
                "invoice_id": self.id,
                "status": self.status,
                "currency": invoice_currency,
                "error": str(e),
            }

    # ------------------------------------------------------------------
    # _refresh_by_nonce  (active while SUBACCOUNT_PAYMENTS_ENABLED=False)
    # ------------------------------------------------------------------

    def _refresh_by_nonce(self) -> "Async[dict]":
        """
        Check payment by scanning the token's ICRC-1 indexer for an incoming
        transfer whose amount equals exactly (base_amount_raw + payment_nonce).

        Flow:
          1. Resolve the token and its indexer canister ID.
          2. Call get_account_transactions() on the indexer for the canister's
             main account (no subaccount).
          3. Walk the returned transactions; mark paid on the first match.

        If the token has no indexer registered, the check cannot proceed and
        an error is returned — the invoice stays Pending until the indexer is
        configured or the operator uses find_by_nonce_amount() externally.
        """
        invoice_currency = (self.currency or "").strip()
        if not invoice_currency:
            from core.realm_currency import no_treasury_token_error

            err = no_treasury_token_error()
            return {
                "invoice_id": self.id,
                "status": self.status,
                "currency": "",
                **err,
            }

        token = self._find_token()

        if not token:
            return {
                "invoice_id": self.id,
                "status": self.status,
                "currency": invoice_currency,
                "error": f"No registered Token for '{invoice_currency}'",
            }

        if not token.indexer:
            logger.warning(
                f"Invoice {self.id}: token '{invoice_currency}' has no indexer — "
                "cannot scan transactions for nonce-based payment detection"
            )
            return {
                "invoice_id": self.id,
                "status": self.status,
                "currency": invoice_currency,
                "error": (
                    f"Token '{invoice_currency}' has no indexer canister configured. "
                    "Register the ICRC-1 indexer canister ID on the Token entity "
                    "to enable nonce-based payment detection."
                ),
            }

        token_decimals = token.decimals or DEFAULT_DECIMALS
        expected_raw = self.get_nonce_amount_raw(token_decimals)
        self_principal_str = ic.id().to_str()

        logger.info(
            f"Invoice {self.id}: nonce refresh — token={token.name}, "
            f"indexer={token.indexer}, decimals={token_decimals}, "
            f"expected_raw={expected_raw}, principal={self_principal_str}"
        )

        try:
            indexer = _ICRC1IndexerService(Principal.from_str(token.indexer))

            args = _GetAccountTransactionsArgs(
                account=_IcrcAccount(
                    owner=Principal.from_str(self_principal_str),
                    subaccount=None,
                ),
                start=None,       # start from most recent
                max_results=200,  # scan last 200 transactions
            )

            result = yield indexer.get_account_transactions(args)

            _dbg = {
                "result_type": type(result).__name__,
                "result_repr": repr(result)[:500],
            }

            # Unwrap Ok/Err variant
            if isinstance(result, dict):
                if "Err" in result:
                    return {
                        "invoice_id": self.id,
                        "status": self.status,
                        "error": f"Indexer error: {result['Err']}",
                        "_debug": _dbg,
                    }
                response = result.get("Ok", result)
            else:
                err = getattr(result, "Err", None)
                if err is not None:
                    return {
                        "invoice_id": self.id,
                        "status": self.status,
                        "error": f"Indexer error: {err}",
                        "_debug": _dbg,
                    }
                response = getattr(result, "Ok", result)

            # The indexer returns a nested Ok variant:
            #   CallResult.Ok -> {'Ok': {'balance': ..., 'transactions': [...]}}
            # Unwrap the inner Ok if present.
            if isinstance(response, dict) and "Ok" in response:
                response = response["Ok"]
            elif hasattr(response, "Ok"):
                response = response.Ok

            _dbg["response_type"] = type(response).__name__
            _dbg["response_repr"] = repr(response)[:500]

            transactions = (
                getattr(response, "transactions", None)
                or (response.get("transactions") if isinstance(response, dict) else None)
                or []
            )

            _dbg["transactions_type"] = type(transactions).__name__
            _dbg["transactions_len"] = len(transactions) if hasattr(transactions, '__len__') else '?'
            if transactions:
                _dbg["first_tx"] = repr(transactions[0])[:300]

            for tx_with_id in transactions:
                tx = (
                    getattr(tx_with_id, "transaction", None)
                    or (tx_with_id.get("transaction") if isinstance(tx_with_id, dict) else None)
                )
                if not tx:
                    continue

                kind = getattr(tx, "kind", None) or (tx.get("kind") if isinstance(tx, dict) else "")
                # Both 'transfer' (ckUSDC indexer) and '1xfer' (ICRC-3 style) are accepted.
                if kind not in ("transfer", "1xfer"):
                    continue

                transfer = (
                    getattr(tx, "transfer", None)
                    or (tx.get("transfer") if isinstance(tx, dict) else None)
                )
                if not transfer:
                    continue

                amount = (
                    getattr(transfer, "amount", None)
                    or (transfer.get("amount") if isinstance(transfer, dict) else None)
                    or 0
                )
                to_account = (
                    getattr(transfer, "to", None)
                    or (transfer.get("to") if isinstance(transfer, dict) else None)
                )
                if not to_account:
                    continue

                to_owner = str(
                    getattr(to_account, "owner", None)
                    or (to_account.get("owner") if isinstance(to_account, dict) else "")
                    or ""
                )
                to_sub = (
                    getattr(to_account, "subaccount", None)
                    or (to_account.get("subaccount") if isinstance(to_account, dict) else None)
                )

                # Accept None, empty list, or empty bytes as "no subaccount"
                no_sub = to_sub is None or to_sub == [] or to_sub == b""
                if to_owner == self_principal_str and no_sub and amount == expected_raw:
                    logger.info(
                        f"Invoice {self.id}: matched nonce payment — "
                        f"{amount} raw ({invoice_currency}) via indexer"
                    )
                    self.mark_paid(
                        payment_currency=invoice_currency,
                        payment_amount=amount / (10 ** token_decimals),
                        payment_amount_raw=amount,
                    )
                    treasury_sync = yield self._sync_treasury_transactions(token)
                    return {
                        "invoice_id": self.id,
                        "status": "Paid",
                        "payment_method": "nonce",
                        "matched_amount_raw": amount,
                        "nonce": self.payment_nonce,
                        "treasury_sync": treasury_sync,
                    }

            return {
                "invoice_id": self.id,
                "status": self.status,
                "currency": invoice_currency,
                "payment_method": "nonce",
                "expected_amount_raw": expected_raw,
                "scanned_transactions": len(transactions),
                "_debug": _dbg,
            }

        except Exception as e:
            logger.error(f"Invoice {self.id}: error in nonce refresh: {e}")
            return {
                "invoice_id": self.id,
                "status": self.status,
                "currency": invoice_currency,
                "error": str(e),
            }

    # ------------------------------------------------------------------
    # Accounting hook (overridable via Codex)
    # ------------------------------------------------------------------

    @staticmethod
    def accounting_hook(
        invoice: "Invoice" = None,
        event: str = None,
        transfer=None,
        fund=None,
        fiscal_period=None,
        revenue_category=None,
        description=None
    ):
        """
        Hook for custom accounting logic.  Override via Codex.

        Return None to use default logic, or return a list of LedgerEntry
        instances to replace the default entries entirely.
        """
        return None
