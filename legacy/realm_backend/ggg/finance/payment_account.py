from ic_python_db import (
    Boolean,
    Entity,
    ManyToOne,
    String,
    TimestampedMixin,
)
from ic_python_logging import get_logger

logger = get_logger("entity.payment_account")


class PaymentAccount(Entity, TimestampedMixin):
    __owner_field__ = "user"  # realms#282 — SecureORM ownership stamp/protect
    """
    Represents a payment account that can receive transfers.
    Users can have multiple payment accounts for different networks and currencies.
    
    The ID is auto-generated as: {currency}_{network}_{address}
    This ensures uniqueness and makes IDs human-readable.
    """

    __alias__ = "id"
    id = String()
    user = ManyToOne("User", "payment_accounts")
    address = String(max_length=256)  # The actual address/principal/IBAN
    label = String(max_length=100)  # User-friendly name like "My ICP Wallet"
    network = String(max_length=50)  # e.g., "ICP", "Bitcoin", "Ethereum", "SEPA"
    currency = String(max_length=20)  # e.g., "ckBTC", "EUR", "ICP", "ETH"
    is_active = Boolean(default=True)  # Allow soft deletion
    is_verified = Boolean(default=False)  # Optional verification status
    metadata = String(max_length=512)  # JSON string for additional data

    def __init__(self, **kwargs):
        # Auto-generate ID from currency, network, and address if not provided
        if "id" not in kwargs and "_id" not in kwargs:
            currency = kwargs.get("currency", "")
            network = kwargs.get("network", "")
            address = kwargs.get("address", "")
            if currency and network and address:
                kwargs["id"] = f"{currency}_{network}_{address}"
        super().__init__(**kwargs)