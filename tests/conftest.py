"""Shared fixtures for faucet integration tests.

Testnet tests (``@pytest.mark.testnet``) call the Alchemy faucet via
``nodriver`` (opens a Chrome window briefly to solve Cloudflare Turnstile)
and verify the wallet balance on-chain.

Configure via environment variables::

    TESTNET_PRIVATE_KEY       — hex private key of the test wallet (required)
    CHAINSTACK_API_KEY        — Chainstack API key for fast REST-API drips (optional)
    SEPOLIA_RPC_URL           — Sepolia JSON-RPC URL       (default: https://rpc.sepolia.org)
    OP_SEPOLIA_RPC_URL        — OP Sepolia RPC URL         (default: https://sepolia.optimism.io)
    BASE_SEPOLIA_RPC_URL      — Base Sepolia RPC URL       (default: https://sepolia.base.org)
    ZKSYNC_SEPOLIA_RPC_URL    — zkSync Sepolia RPC URL     (default: https://sepolia.era.zksync.dev)
    ARBITRUM_SEPOLIA_RPC_URL  — Arbitrum Sepolia RPC URL   (default: https://sepolia-rollup.arbitrum.io/rpc)
    POLYGON_AMOY_RPC_URL      — Polygon Amoy RPC URL       (default: https://rpc-amoy.polygon.technology)
    AVALANCHE_FUJI_RPC_URL    — Avalanche Fuji RPC URL     (default: https://avalanche-fuji-c-chain-rpc.publicnode.com)

Run testnet tests with::

    pytest -m testnet
"""

import pytest
import asyncio
import os
from web3 import AsyncWeb3

from dotenv import load_dotenv

load_dotenv()


SEPOLIA_RPC_URL = os.environ.get("SEPOLIA_RPC_URL", "https://rpc.sepolia.org")
OP_SEPOLIA_RPC_URL = os.environ.get("OP_SEPOLIA_RPC_URL", "https://sepolia.optimism.io")
BASE_SEPOLIA_RPC_URL = os.environ.get(
    "BASE_SEPOLIA_RPC_URL", "https://sepolia.base.org"
)
ZKSYNC_SEPOLIA_RPC_URL = os.environ.get(
    "ZKSYNC_SEPOLIA_RPC_URL", "https://sepolia.era.zksync.dev"
)
ARBITRUM_SEPOLIA_RPC_URL = os.environ.get(
    "ARBITRUM_SEPOLIA_RPC_URL", "https://sepolia-rollup.arbitrum.io/rpc"
)
POLYGON_AMOY_RPC_URL = os.environ.get(
    "POLYGON_AMOY_RPC_URL", "https://rpc-amoy.polygon.technology"
)
AVALANCHE_FUJI_RPC_URL = os.environ.get(
    "AVALANCHE_FUJI_RPC_URL", "https://avalanche-fuji-c-chain-rpc.publicnode.com"
)
TESTNET_PRIVATE_KEY = os.environ.get("TESTNET_PRIVATE_KEY", "")

# EVM chains that have a known USDC contract — used by the parametrized USDC test.
# Maps chain slug → public RPC URL.
_USDC_EVM_CHAINS: dict[str, str] = {
    "ethereum-sepolia": SEPOLIA_RPC_URL,
    "arbitrum-sepolia": ARBITRUM_SEPOLIA_RPC_URL,
    "base-sepolia": BASE_SEPOLIA_RPC_URL,
    "optimism-sepolia": OP_SEPOLIA_RPC_URL,
    "polygon-amoy": POLYGON_AMOY_RPC_URL,
    "avalanche-fuji": AVALANCHE_FUJI_RPC_URL,
}

_MIN_BALANCE = 10**16  # 0.01 ETH


@pytest.fixture
def sepolia_w3() -> AsyncWeb3:
    """AsyncWeb3 connected to Sepolia."""
    return AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(SEPOLIA_RPC_URL))


@pytest.fixture
def op_sepolia_w3() -> AsyncWeb3:
    """AsyncWeb3 connected to OP Sepolia."""
    return AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(OP_SEPOLIA_RPC_URL))


@pytest.fixture
def base_sepolia_w3() -> AsyncWeb3:
    """AsyncWeb3 connected to Base Sepolia."""
    return AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(BASE_SEPOLIA_RPC_URL))


@pytest.fixture
def zksync_sepolia_w3() -> AsyncWeb3:
    """AsyncWeb3 connected to zkSync Sepolia."""
    return AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(ZKSYNC_SEPOLIA_RPC_URL))


@pytest.fixture
def arbitrum_sepolia_w3() -> AsyncWeb3:
    """AsyncWeb3 connected to Arbitrum Sepolia."""
    return AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(ARBITRUM_SEPOLIA_RPC_URL))


@pytest.fixture
def polygon_amoy_w3() -> AsyncWeb3:
    """AsyncWeb3 connected to Polygon Amoy."""
    return AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(POLYGON_AMOY_RPC_URL))


@pytest.fixture
def avalanche_fuji_w3() -> AsyncWeb3:
    """AsyncWeb3 connected to Avalanche Fuji."""
    return AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(AVALANCHE_FUJI_RPC_URL))


@pytest.fixture(
    params=list(_USDC_EVM_CHAINS.keys()),
    ids=list(_USDC_EVM_CHAINS.keys()),
)
def usdc_chain_w3(request: pytest.FixtureRequest):
    """Parametrized fixture — yields (chain_slug, AsyncWeb3) for every EVM chain
    that has a known USDC contract in :data:`faucet.USDC_CONTRACTS`."""
    chain: str = request.param
    return chain, AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(_USDC_EVM_CHAINS[chain]))


async def _ensure_funded(w3: AsyncWeb3, address: str, chain_slug: str) -> None:
    """Drip *address* on *chain_slug* if balance is below 0.01 ETH.

    Skips the test when the faucet is rate-limited and the wallet has
    insufficient balance.
    """
    from faucet import drip

    checksum = AsyncWeb3.to_checksum_address(address)
    balance = await w3.eth.get_balance(checksum)
    if balance >= _MIN_BALANCE:
        return

    try:
        await drip(address, chain_slug)
    except Exception as exc:
        balance = await w3.eth.get_balance(checksum)
        if balance < _MIN_BALANCE:
            pytest.skip(
                f"Faucet unavailable and wallet has insufficient balance: {exc}"
            )
        return

    deadline = asyncio.get_event_loop().time() + 60.0
    while asyncio.get_event_loop().time() < deadline:
        balance = await w3.eth.get_balance(checksum)
        if balance >= _MIN_BALANCE:
            return
        await asyncio.sleep(3)
    pytest.skip(f"Timed out waiting for faucet funds on {chain_slug}")


@pytest.fixture
async def funded_sepolia_account(sepolia_w3):
    """eth_account LocalAccount funded with Sepolia ETH."""
    from eth_account import Account

    if not TESTNET_PRIVATE_KEY:
        pytest.skip("TESTNET_PRIVATE_KEY not set — cannot derive testnet wallet")
    account = Account.from_key(TESTNET_PRIVATE_KEY)
    await _ensure_funded(sepolia_w3, account.address, "ethereum-sepolia")
    return account


@pytest.fixture
async def funded_op_sepolia_account(op_sepolia_w3):
    """eth_account LocalAccount funded with OP Sepolia ETH."""
    from eth_account import Account

    if not TESTNET_PRIVATE_KEY:
        pytest.skip("TESTNET_PRIVATE_KEY not set — cannot derive testnet wallet")
    account = Account.from_key(TESTNET_PRIVATE_KEY)
    await _ensure_funded(op_sepolia_w3, account.address, "optimism-sepolia")
    return account


@pytest.fixture
async def funded_base_sepolia_account(base_sepolia_w3):
    """eth_account LocalAccount funded with Base Sepolia ETH."""
    from eth_account import Account

    if not TESTNET_PRIVATE_KEY:
        pytest.skip("TESTNET_PRIVATE_KEY not set — cannot derive testnet wallet")
    account = Account.from_key(TESTNET_PRIVATE_KEY)
    await _ensure_funded(base_sepolia_w3, account.address, "base-sepolia")
    return account


@pytest.fixture
async def funded_zksync_sepolia_account(zksync_sepolia_w3):
    """eth_account LocalAccount funded with zkSync Sepolia ETH."""
    from eth_account import Account

    if not TESTNET_PRIVATE_KEY:
        pytest.skip("TESTNET_PRIVATE_KEY not set — cannot derive testnet wallet")
    account = Account.from_key(TESTNET_PRIVATE_KEY)
    await _ensure_funded(zksync_sepolia_w3, account.address, "zksync-sepolia")
    return account


@pytest.fixture
def testnet_address() -> str:
    """Return the wallet address derived from TESTNET_PRIVATE_KEY (no funding)."""
    from eth_account import Account

    if not TESTNET_PRIVATE_KEY:
        pytest.skip("TESTNET_PRIVATE_KEY not set — cannot derive testnet wallet")
    return Account.from_key(TESTNET_PRIVATE_KEY).address
