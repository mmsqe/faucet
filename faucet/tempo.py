"""
Tempo testnet faucet — the ``tempo_fundAddress`` JSON-RPC method.

A first-party faucet exposed by the public testnet node: no captcha, wallet, or
signature. One POST mints all four testnet TIP-20 stablecoins (pathUSD,
AlphaUSD, BetaUSD, ThetaUSD) and returns one transaction hash per token.

The node is behind Cloudflare, which 403s a default User-Agent — so requests
send a browser one (see :data:`_HEADERS`).

Docs: https://tempo.xyz/developers/docs/quickstart/faucet
"""

from __future__ import annotations

import json
import os

import aiohttp
from web3 import AsyncWeb3

from faucet.alchemy import FaucetError, RateLimitError

#: Tempo Moderato testnet chain ID (0xa5bf).
CHAIN_ID = 42431

#: Chain slug → public JSON-RPC endpoint. ``tempo-testnet`` is an alias host for
#: the same Moderato chain.
_CHAIN_RPC: dict[str, str] = {
    "tempo-moderato": "https://rpc.moderato.tempo.xyz",
    "tempo-testnet": "https://rpc.testnet.tempo.xyz",
}

#: Public set of chain slugs supported by this module.
CHAINS: set[str] = set(_CHAIN_RPC)

#: TIP-20 testnet stablecoins the faucet mints (symbol → contract address).
TOKENS: dict[str, str] = {
    "pathUSD": "0x20C0000000000000000000000000000000000000",
    "AlphaUSD": "0x20C0000000000000000000000000000000000001",
    "BetaUSD": "0x20C0000000000000000000000000000000000002",
    "ThetaUSD": "0x20C0000000000000000000000000000000000003",
}

_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    # Cloudflare 403s the default aiohttp User-Agent; pin a browser string.
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
}


def _rpc_url(chain: str) -> str:
    """RPC endpoint for *chain*, honoring the ``TEMPO_RPC_URL`` env override.
    Raises ``ValueError`` for an unknown chain."""
    if chain not in _CHAIN_RPC:
        raise ValueError(
            f"Tempo: unknown chain {chain!r}. Supported: {', '.join(sorted(CHAINS))}"
        )
    return os.environ.get("TEMPO_RPC_URL", _CHAIN_RPC[chain])


async def drip(
    address: str,
    chain: str = "tempo-moderato",
    *,
    rpc_url: str | None = None,
) -> list[str]:
    """Fund *address* with all four Tempo testnet stablecoins via
    ``tempo_fundAddress``; returns one transaction hash per token.

    *chain* must be a key in :data:`CHAINS`. *rpc_url* overrides the endpoint
    (default: ``TEMPO_RPC_URL`` env var, else the built-in URL for *chain*).

    Raises :class:`RateLimitError` on a faucet rate limit, :class:`FaucetError`
    on any other RPC/transport error, and ``ValueError`` for an unknown *chain*.
    """
    url = rpc_url or _rpc_url(chain)
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tempo_fundAddress",
        "params": [AsyncWeb3.to_checksum_address(address)],
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=_HEADERS) as resp:
            text = await resp.text()

    try:
        data = json.loads(text)
    except ValueError as exc:
        raise FaucetError(f"Tempo RPC error ({resp.status}): {text[:200]!r}") from exc

    err = data.get("error")
    if err:
        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        if any(k in msg.lower() for k in ("rate", "limit", "too many")):
            raise RateLimitError(f"Tempo faucet rate limit: {msg}")
        raise FaucetError(f"Tempo faucet error: {msg}")

    return [h for h in (data.get("result") or []) if h]


__all__ = ["CHAIN_ID", "CHAINS", "TOKENS", "drip"]
