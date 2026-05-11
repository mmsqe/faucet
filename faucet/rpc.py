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

from dotenv import load_dotenv

load_dotenv()


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
HUMANITY_TESTNET_RPC_URL = _rpc(
    None,
    "HUMANITY_TESTNET_RPC_URL",
    "https://humanity-testnet.g.alchemy.com/public",
)
SONEIUM_MINATO_RPC_URL = _rpc(
    None, "SONEIUM_MINATO_RPC_URL", "https://rpc.minato.soneium.org"
)
LENS_SEPOLIA_RPC_URL = _rpc(
    None, "LENS_SEPOLIA_RPC_URL", "https://rpc.testnet.lens.dev"
)
SHAPE_SEPOLIA_RPC_URL = _rpc(
    None, "SHAPE_SEPOLIA_RPC_URL", "https://sepolia.shape.network"
)
WORLDCHAIN_SEPOLIA_RPC_URL = _rpc(
    None,
    "WORLDCHAIN_SEPOLIA_RPC_URL",
    "https://worldchain-sepolia.g.alchemy.com/public",
)
CROSSFI_TESTNET_RPC_URL = _rpc(
    None, "CROSSFI_TESTNET_RPC_URL", "https://rpc.testnet.ms"
)
GENSYN_TESTNET_RPC_URL = _rpc(
    None,
    "GENSYN_TESTNET_RPC_URL",
    "https://gensyn-testnet.g.alchemy.com/public",
)
MONAD_TESTNET_RPC_URL = _rpc(
    None, "MONAD_TESTNET_RPC_URL", "https://testnet-rpc.monad.xyz"
)
ABSTRACT_TESTNET_RPC_URL = _rpc(
    None, "ABSTRACT_TESTNET_RPC_URL", "https://api.testnet.abs.xyz"
)

#: All EVM testnets: chain slug → (rpc_url, inject_poa_middleware, native_token_symbol)
#: PoA middleware is needed when eth_getBlock returns extraData > 32 bytes
#: (Polygon-CDK / Cosmos-EVM chains).
EVM_CHAINS: dict[str, tuple[str, bool, str]] = {
    "ethereum-sepolia": (SEPOLIA_RPC_URL, False, "ETH"),
    "optimism-sepolia": (OP_SEPOLIA_RPC_URL, False, "ETH"),
    "base-sepolia": (BASE_SEPOLIA_RPC_URL, False, "ETH"),
    "zksync-sepolia": (ZKSYNC_SEPOLIA_RPC_URL, False, "ETH"),
    "arbitrum-sepolia": (ARBITRUM_SEPOLIA_RPC_URL, False, "ETH"),
    "polygon-amoy": (POLYGON_AMOY_RPC_URL, True, "POL"),
    "avalanche-fuji": (AVALANCHE_FUJI_RPC_URL, True, "AVAX"),
    "hyperliquid-testnet": (HL_TESTNET_RPC_URL, False, "HYPE"),
    "humanity-testnet": (HUMANITY_TESTNET_RPC_URL, True, "tHP"),
    "soneium-minato": (SONEIUM_MINATO_RPC_URL, False, "ETH"),
    "lens-sepolia": (LENS_SEPOLIA_RPC_URL, False, "GRASS"),
    "shape-sepolia": (SHAPE_SEPOLIA_RPC_URL, False, "ETH"),
    "worldchain-sepolia": (WORLDCHAIN_SEPOLIA_RPC_URL, False, "ETH"),
    "crossfi-testnet": (CROSSFI_TESTNET_RPC_URL, True, "XFI"),
    "gensyn-testnet": (GENSYN_TESTNET_RPC_URL, False, "ETH"),
    "monad-testnet": (MONAD_TESTNET_RPC_URL, False, "MON"),
    "abstract-testnet": (ABSTRACT_TESTNET_RPC_URL, False, "ETH"),
    # Note: stable-testnet, syndicate-risa, worldl3-devnet are dripped via
    # Alchemy but have no public no-auth RPC. Add them here once an Alchemy
    # API key is wired up (https://<slug>.g.alchemy.com/v2/<key>).
    # Note: zksync-era-sepolia is the same on-chain as zksync-sepolia (just a
    # second faucet path via Chainstack); existing zksync-sepolia entry sweeps
    # both balances.
}
