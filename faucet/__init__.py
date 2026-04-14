"""
Alchemy testnet faucet automation.

Supports all 19 chains listed on https://www.alchemy.com/faucets.
Uses ``nodriver`` (undetectable Chrome) to solve Cloudflare Turnstile and
submit the faucet request — no API key or manual token required.

For chains where Alchemy's faucet runs dry (HTTP 503), a Chainstack fallback
is attempted automatically.

Quick start::

    import asyncio
    from faucet import drip

    # Fund a wallet on OP Sepolia
    tx = asyncio.run(drip("0xYourAddress", "optimism-sepolia"))
    print("tx:", tx)

Supported chains (pass as the *chain* argument to :func:`drip`)::

    ethereum-sepolia   arbitrum-sepolia   optimism-sepolia
    base-sepolia       polygon-amoy       zksync-sepolia
    starknet-sepolia   worldchain-sepolia monad-testnet
    shape-sepolia      lens-sepolia       abstract-testnet
    soneium-minato     crossfi-testnet    gensyn-testnet
    humanity-testnet   syndicate-risa     worldl3-devnet
    stable-testnet
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


async def drip(
    address: str,
    chain: str,
    *,
    headless: bool = False,
    timeout: float = 30.0,
) -> str | None:
    """Fund *address* on *chain* via the Alchemy faucet, with Chainstack fallback.

    When Alchemy's faucet returns HTTP 503 (insufficient faucet balance) and a
    Chainstack fallback exists for the chain, the request is retried against
    Chainstack automatically.

    Args:
        address: Wallet address to fund.
        chain: Alchemy chain slug, e.g. ``"optimism-sepolia"``.
            Must be a key in :data:`CHAINS`.
        headless: Run Chrome in headless mode (default: ``False``).
        timeout: Seconds to wait for Turnstile to solve (default: 30).

    Returns:
        Transaction hash string, or ``None`` if the API did not return one.

    Raises:
        ValueError: *chain* is not in :data:`CHAINS`.
        RateLimitError: Daily limit hit on all attempted providers.
        FaucetError: All providers failed or Turnstile timed out.
    """
    try:
        return await _alchemy_drip(address, chain, headless=headless, timeout=timeout)
    except InsufficientFaucetBalanceError:
        cs_chain = _CHAINSTACK_FALLBACK.get(chain)
        if cs_chain is None:
            raise
        return await _chainstack.drip(
            address, cs_chain, headless=headless, timeout=timeout
        )


__all__ = [
    "CHAINS",
    "FaucetError",
    "InsufficientFaucetBalanceError",
    "RateLimitError",
    "drip",
]
