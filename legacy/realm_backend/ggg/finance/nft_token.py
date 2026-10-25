from ic_python_db import (
    Entity,
    Integer,
    String,
    TimestampedMixin,
)
from ic_python_logging import get_logger

logger = get_logger("entity.nft_token")


class NFTToken(Entity, TimestampedMixin):
    """
    Represents an NFT token/collection that the realm can interact with.
    
    Stores canister ID for the NFT backend, along with metadata
    like name, symbol, description, and supply cap.
    
    NFT types:
    - "land": Land ownership NFT for the realm
    - "custom": Other realm-specific NFT collections
    """
    
    __alias__ = "id"
    id = String(max_length=16)  # Symbol serves as ID: e.g., "DLAND", "ALAND"
    symbol = String(max_length=16)  # e.g., "DLAND", "ALAND", "SLAND"
    name = String(max_length=64)  # e.g., "Dominion Land", "Agora Land"
    description = String(max_length=256, default="")  # Description of the NFT collection
    canister_id = String(max_length=64)  # Principal ID of the NFT backend canister
    supply_cap = Integer(default=0)  # Maximum number of NFTs (0 = unlimited)
    total_supply = Integer(default=0)  # Current number of minted NFTs
    nft_type = String(max_length=16, default="land")  # "land" or "custom"
    enabled = String(max_length=8, default="true")  # "true" or "false" for display

    def is_enabled(self) -> bool:
        """Check if this NFT collection is enabled for display."""
        return self.enabled == "true"

    def get_config(self) -> dict:
        """Get NFT configuration for API calls."""
        return {
            "symbol": self.symbol,
            "name": self.name,
            "description": self.description,
            "canister_id": self.canister_id,
            "supply_cap": self.supply_cap,
            "total_supply": self.total_supply,
            "nft_type": self.nft_type,
        }
