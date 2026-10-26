"""Fixed canister ids the realm relies on."""

# Chain-key tokens on the IC mainnet: the ledgers a realm may adopt as its treasury token, with their index canisters.
TOKENS = (
    {"symbol": "ckBTC", "name": "ckBTC", "description": "Chain-Key Bitcoin — IC-native Bitcoin twin",
     "ledger": "mxzaz-hqaaa-aaaar-qaada-cai", "indexer": "n5wcd-faaaa-aaaar-qaaea-cai", "decimals": 8,
     "monetary": True},
    {"symbol": "ckUSDC", "name": "ckUSDC", "description": "Chain-Key USDC — IC-native USD stablecoin",
     "ledger": "xevnm-gaaaa-aaaar-qafnq-cai", "indexer": "xrs4b-hiaaa-aaaar-qafoa-cai", "decimals": 6,
     "monetary": True},
    {"symbol": "ckEURC", "name": "ckEURC", "description": "Chain-Key EURC — IC-native euro stablecoin",
     "ledger": "pe5t5-diaaa-aaaar-qahwa-cai", "indexer": "pd4vj-oqaaa-aaaar-qahwq-cai", "decimals": 6,
     "monetary": True},
)
