"""
Alchemy testnet faucet automation.

Supports all 19 chains listed on https://www.alchemy.com/faucets.
Uses ``nodriver`` (undetectable Chrome) to solve Cloudflare Turnstile and
submit the faucet request — no API key or manual token required.

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

from faucet.alchemy import CHAINS, FaucetError, RateLimitError, drip

__all__ = ["CHAINS", "FaucetError", "RateLimitError", "drip"]
