"""
RPC URL resolution for all supported EVM testnets.

Priority (highest first):
  1. ``INFURA_KEY`` env var — builds ``https://{network}.infura.io/v3/{key}``
     for chains that have an Infura endpoint.
  2. Per-chain ``*_RPC_URL`` env var override.
  3. Hard-coded public fallback.
"""

from __future__ import annotations

import os


def _rpc(infura_network: str | None, env_var: str, fallback: str) -> str:
    infura_key = os.environ.get("INFURA_KEY", "")
    if infura_key and infura_network:
        return f"https://{infura_network}.infura.io/v3/{infura_key}"
    return os.environ.get(env_var, fallback)


SEPOLIA_RPC_URL = _rpc("sepolia", "SEPOLIA_RPC_URL", "https://rpc.sepolia.org")
OP_SEPOLIA_RPC_URL = _rpc(
    "optimism-sepolia", "OP_SEPOLIA_RPC_URL", "https://sepolia.optimism.io"
)
BASE_SEPOLIA_RPC_URL = _rpc(
    "base-sepolia", "BASE_SEPOLIA_RPC_URL", "https://sepolia.base.org"
)
ZKSYNC_SEPOLIA_RPC_URL = _rpc(
    "zksync-sepolia", "ZKSYNC_SEPOLIA_RPC_URL", "https://sepolia.era.zksync.dev"
)
ARBITRUM_SEPOLIA_RPC_URL = _rpc(
    None, "ARBITRUM_SEPOLIA_RPC_URL", "https://sepolia-rollup.arbitrum.io/rpc"
)
POLYGON_AMOY_RPC_URL = _rpc(
    None, "POLYGON_AMOY_RPC_URL", "https://rpc-amoy.polygon.technology"
)
AVALANCHE_FUJI_RPC_URL = _rpc(
    None, "AVALANCHE_FUJI_RPC_URL", "https://avalanche-fuji-c-chain-rpc.publicnode.com"
)
HL_TESTNET_RPC_URL = _rpc(
    None, "HL_TESTNET_RPC_URL", "https://rpc.hyperliquid-testnet.xyz/evm"
)

#: All EVM testnets: chain slug → (rpc_url, inject_poa_middleware, native_token_symbol)
EVM_CHAINS: dict[str, tuple[str, bool, str]] = {
    "ethereum-sepolia": (SEPOLIA_RPC_URL, False, "ETH"),
    "optimism-sepolia": (OP_SEPOLIA_RPC_URL, False, "ETH"),
    "base-sepolia": (BASE_SEPOLIA_RPC_URL, False, "ETH"),
    "zksync-sepolia": (ZKSYNC_SEPOLIA_RPC_URL, False, "ETH"),
    "arbitrum-sepolia": (ARBITRUM_SEPOLIA_RPC_URL, False, "ETH"),
    "polygon-amoy": (POLYGON_AMOY_RPC_URL, True, "POL"),
    "avalanche-fuji": (AVALANCHE_FUJI_RPC_URL, False, "AVAX"),
    "hyperliquid-testnet": (HL_TESTNET_RPC_URL, False, "HYPE"),
}
