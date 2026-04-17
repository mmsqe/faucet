"""Shared fixtures for faucet integration tests.

Testnet tests (``@pytest.mark.testnet``) call the Alchemy faucet via
``nodriver`` (opens a Chrome window briefly to solve Cloudflare Turnstile)
and verify the wallet balance on-chain.

Configure via environment variables::

    TESTNET_ADDRESS   — wallet address to fund (required)
    CHAINSTACK_API_KEY — Chainstack API key for fast REST-API drips (optional)
    INFURA_KEY        — Infura project key; if set, all RPC URLs use Infura

Run testnet tests with::

    pytest -m testnet
"""

import pytest
import asyncio
import os
from web3 import AsyncWeb3

from dotenv import load_dotenv

load_dotenv()

_INFURA_KEY = os.environ.get("INFURA_KEY", "")


def _rpc(infura_network: str, fallback: str) -> str:
    if _INFURA_KEY:
        return f"https://{infura_network}.infura.io/v3/{_INFURA_KEY}"
    return fallback


SEPOLIA_RPC_URL = _rpc("sepolia", "https://rpc.sepolia.org")
OP_SEPOLIA_RPC_URL = _rpc("optimism-sepolia", "https://sepolia.optimism.io")
BASE_SEPOLIA_RPC_URL = _rpc("base-sepolia", "https://sepolia.base.org")
ZKSYNC_SEPOLIA_RPC_URL = _rpc("zksync-sepolia", "https://sepolia.era.zksync.dev")
ARBITRUM_SEPOLIA_RPC_URL = os.environ.get("ARBITRUM_SEPOLIA_RPC_URL", "https://sepolia-rollup.arbitrum.io/rpc")
POLYGON_AMOY_RPC_URL = os.environ.get("POLYGON_AMOY_RPC_URL", "https://rpc-amoy.polygon.technology")
AVALANCHE_FUJI_RPC_URL = os.environ.get("AVALANCHE_FUJI_RPC_URL", "https://avalanche-fuji-c-chain-rpc.publicnode.com")
HL_TESTNET_RPC_URL = os.environ.get("HL_TESTNET_RPC_URL", "https://rpc.hyperliquid-testnet.xyz/evm")
TESTNET_ADDRESS = os.environ.get("TESTNET_ADDRESS", "")

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


@pytest.fixture
def hyperliquid_testnet_w3() -> AsyncWeb3:
    """AsyncWeb3 connected to Hyperliquid testnet."""
    return AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(HL_TESTNET_RPC_URL))


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
    """Always attempt to drip *address* on *chain_slug*.

    Skips the test only when the faucet fails AND the wallet has
    insufficient balance.
    """
    from faucet import drip

    checksum = AsyncWeb3.to_checksum_address(address)

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
def testnet_address() -> str:
    """Return the testnet wallet address, skipping if not configured."""
    if not TESTNET_ADDRESS:
        pytest.skip("TESTNET_ADDRESS not set")
    return TESTNET_ADDRESS


@pytest.fixture
async def funded_sepolia_account(testnet_address, sepolia_w3):
    """Wallet address funded with Sepolia ETH."""
    await _ensure_funded(sepolia_w3, testnet_address, "ethereum-sepolia")
    return testnet_address


@pytest.fixture
async def funded_op_sepolia_account(testnet_address, op_sepolia_w3):
    """Wallet address funded with OP Sepolia ETH."""
    await _ensure_funded(op_sepolia_w3, testnet_address, "optimism-sepolia")
    return testnet_address


@pytest.fixture
async def funded_base_sepolia_account(testnet_address, base_sepolia_w3):
    """Wallet address funded with Base Sepolia ETH."""
    await _ensure_funded(base_sepolia_w3, testnet_address, "base-sepolia")
    return testnet_address


@pytest.fixture
async def funded_zksync_sepolia_account(testnet_address, zksync_sepolia_w3):
    """Wallet address funded with zkSync Sepolia ETH."""
    await _ensure_funded(zksync_sepolia_w3, testnet_address, "zksync-sepolia")
    return testnet_address


@pytest.fixture
async def funded_hyperliquid_testnet_account(testnet_address, hyperliquid_testnet_w3):
    """Wallet address funded with Hyperliquid testnet ETH."""
    await _ensure_funded(hyperliquid_testnet_w3, testnet_address, "hyperliquid-testnet")
    return testnet_address
