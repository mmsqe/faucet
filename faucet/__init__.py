"""
Testnet faucet automation — native ETH and USDC/EURC.

Native ETH (:func:`drip`)
    Alchemy faucet with automatic Chainstack fallback when Alchemy runs dry.
    Supports all 19 Alchemy chains plus Chainstack-only chains.

USDC / EURC (:func:`drip_usdc`)
    Circle faucet (https://faucet.circle.com/).  Drips 20 testnet tokens.
    Rate-limited to one request per (address, chain, token) every 2 hours.

Quick start::

    import asyncio
    from faucet import drip, drip_usdc

    # Native ETH on OP Sepolia
    tx = asyncio.run(drip("0xYourAddress", "optimism-sepolia"))

    # USDC on Base Sepolia
    asyncio.run(drip_usdc("0xYourAddress", "base-sepolia"))
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from web3 import AsyncWeb3

from faucet import chainstack as _chainstack
from faucet.alchemy import (
    CHAINS,
    FaucetError,
    InsufficientFaucetBalanceError,
    RateLimitError,
)
from faucet.alchemy import drip as _alchemy_drip
from faucet.circle import CHAINS as USDC_CHAINS
from faucet.circle import USDC_CONTRACTS
from faucet.circle import drip as drip_usdc
from faucet.sweep import sweep

# ---------------------------------------------------------------------------
# Chainstack fallback — maps Alchemy chain slug → Chainstack chain slug
# for chains that frequently run dry on Alchemy.
# ---------------------------------------------------------------------------

_CHAINSTACK_FALLBACK: dict[str, str] = {
    "zksync-sepolia": "zksync-era-sepolia",
    "ethereum-sepolia": "ethereum-sepolia",
    "base-sepolia": "base-sepolia",
    "optimism-sepolia": "optimism-sepolia",
    "arbitrum-sepolia": "arbitrum-sepolia",
    "polygon-amoy": "polygon-amoy",
}

# Chains only available on Chainstack (not on Alchemy).
_CHAINSTACK_ONLY: set[str] = _chainstack.CHAINS - CHAINS.keys()


async def drip(
    address: str,
    chain: str,
    *,
    headless: bool = False,
    timeout: float = 60.0,
) -> str | None:
    """Fund *address* on *chain* via the Alchemy faucet, with Chainstack fallback.

    When Alchemy's faucet returns HTTP 503 (insufficient faucet balance) and a
    Chainstack fallback exists for the chain, the request is retried against
    Chainstack automatically.

    Chains only available on Chainstack (e.g. ``"hyperliquid-testnet"``) are
    routed directly to Chainstack without attempting Alchemy first.

    Args:
        address: Wallet address to fund.
        chain: Chain slug, e.g. ``"optimism-sepolia"`` or ``"hyperliquid-testnet"``.
            Must be a key in :data:`CHAINS` or a Chainstack-only chain.
        headless: Run Chrome in headless mode (default: ``False``).
        timeout: Seconds to wait for Turnstile to solve (default: 60).

    Returns:
        Transaction hash string, or ``None`` if the API did not return one.

    Raises:
        ValueError: *chain* is not supported by any provider.
        RateLimitError: Daily limit hit on all attempted providers.
        FaucetError: All providers failed or Turnstile timed out.
    """
    if chain in _CHAINSTACK_ONLY:
        return await _chainstack.drip(
            address, chain, headless=headless, timeout=timeout
        )
    try:
        return await _alchemy_drip(address, chain, headless=headless, timeout=timeout)
    except RateLimitError:
        raise
    except FaucetError:
        cs_chain = _CHAINSTACK_FALLBACK.get(chain)
        if cs_chain is None:
            raise
        return await _chainstack.drip(
            address, cs_chain, headless=headless, timeout=timeout
        )


async def is_contract_deployed(w3: "AsyncWeb3", address: str) -> bool:
    """Return ``True`` if *address* has contract bytecode on the connected chain.

    Args:
        w3: Connected :class:`~web3.AsyncWeb3` instance.
        address: Contract address to inspect.
    """
    from web3 import AsyncWeb3 as _W3

    code = await w3.eth.get_code(_W3.to_checksum_address(address))
    return bool(code) and code != b"\x00"


async def is_chain_synced(w3: "AsyncWeb3") -> bool:
    """Return ``True`` if the connected node reports it is fully synced.

    Many L2 RPC providers do not implement ``eth_syncing``; if the method is
    unavailable the node is assumed to be synced and ``True`` is returned.

    Args:
        w3: Connected :class:`~web3.AsyncWeb3` instance.
    """
    try:
        syncing = await w3.eth.syncing
    except Exception:  # noqa: BLE001 — method not supported by this provider
        return True
    return not syncing


__all__ = [
    "CHAINS",
    "USDC_CHAINS",
    "USDC_CONTRACTS",
    "FaucetError",
    "InsufficientFaucetBalanceError",
    "RateLimitError",
    "drip",
    "drip_usdc",
    "is_contract_deployed",
    "is_chain_synced",
    "sweep",
]
