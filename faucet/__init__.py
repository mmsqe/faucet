"""
Testnet faucet automation — native ETH and USDC/EURC.

Native ETH (:func:`drip`)
    Alchemy faucet with automatic Chainstack fallback when Alchemy runs dry.
    Supports all 19 Alchemy chains plus Chainstack-only chains.

USDC / EURC (:func:`drip_usdc`)
    Circle faucet (https://faucet.circle.com/).  Drips 20 testnet tokens.
    Rate-limited to one request per (address, chain, token) every 2 hours.

LINK (:func:`drip_link_all`)
    Chainlink faucet (https://faucets.chain.link/).  Drips canonical LINK across
    chains in one browser.  Wallet-connect only, so it injects a wallet for the
    target address; claiming requires a signature, so a private key is needed.

Tempo TIP-20 stablecoins (:func:`drip_tempo`)
    Tempo testnet faucet via the ``tempo_fundAddress`` JSON-RPC method.  No
    captcha or wallet — a single call mints pathUSD/AlphaUSD/BetaUSD/ThetaUSD.

Quick start::

    import asyncio
    from faucet import drip, drip_usdc, drip_link_all

    # Native ETH on OP Sepolia
    tx = asyncio.run(drip("0xYourAddress", "optimism-sepolia"))

    # USDC on Base Sepolia
    asyncio.run(drip_usdc("0xYourAddress", "base-sepolia"))

    # LINK on all chains (or a subset)
    asyncio.run(drip_link_all("0xYourAddress", private_key="0x..."))
"""

from __future__ import annotations

from faucet import chainstack as _chainstack
from faucet.alchemy import (
    CHAINS,
    FaucetError,
    InsufficientFaucetBalanceError,
    RateLimitError,
)
from faucet.alchemy import drip as _alchemy_drip
from faucet.chainlink import CHAINS as LINK_CHAINS
from faucet.chainlink import LINK_CONTRACTS
from faucet.chainlink import drip as drip_link_all
from faucet.circle import CHAINS as USDC_CHAINS
from faucet.circle import USDC_CONTRACTS
from faucet.circle import drip as drip_usdc
from faucet.sweep import sweep
from faucet.tempo import CHAINS as TEMPO_CHAINS
from faucet.tempo import TOKENS as TEMPO_TOKENS
from faucet.tempo import drip as drip_tempo

# ---------------------------------------------------------------------------
# Chainstack fallback — maps Alchemy chain slug → Chainstack chain slug
# for chains that frequently run dry on Alchemy.
# ---------------------------------------------------------------------------

_CHAINSTACK_FALLBACK: dict[str, str] = {
    "zksync-sepolia": "zksync-era-sepolia",
    "ethereum-sepolia": "ethereum-sepolia",
    "base-sepolia": "base-sepolia",
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


__all__ = [
    "CHAINS",
    "USDC_CHAINS",
    "USDC_CONTRACTS",
    "LINK_CHAINS",
    "LINK_CONTRACTS",
    "TEMPO_CHAINS",
    "TEMPO_TOKENS",
    "FaucetError",
    "InsufficientFaucetBalanceError",
    "RateLimitError",
    "drip",
    "drip_usdc",
    "drip_link_all",
    "drip_tempo",
    "sweep",
]
