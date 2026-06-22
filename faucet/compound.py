"""Compound III (Comet) Ethereum Sepolia testnet faucet.

Calls ``drip(token)`` on Compound's *Fauceteer* contract, which sends 0.01% of
its balance of that token to ``msg.sender``.  The caller pays gas, so a private
key is required and the recipient is always the signing wallet.  Each
(caller, token) pair is rate-limited to one drip per 24 hours.

Supported tokens (Ethereum Sepolia only): USDC, COMP, WBTC, cbETH.  WETH and
wstETH are Comet collateral but cannot be dripped — they are wrapped tokens the
Fauceteer holds none of; get them by wrapping / staking ETH instead.
"""

from __future__ import annotations

from faucet import _erc20
from faucet.alchemy import FaucetError
from faucet.nonce import NonceManager
from faucet.rpc import SEPOLIA_RPC_URL

# Compound III Fauceteer on Ethereum Sepolia
_FAUCETEER_ADDRESS = "0x68793eA49297eB75DFB4610B68e076D2A5c7646C"

# Test token contract addresses on Ethereum Sepolia (held by the Fauceteer).
# Keys are upper-cased so token lookups can be case-insensitive.
TOKENS: dict[str, str] = {
    "USDC": "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238",
    "COMP": "0xA6c8D1c55951e8AC44a0EaA959Be5Fd21cc07531",
    "WBTC": "0xa035b9e130F2B1AedC733eEFb1C67Ba4c503491F",
    "CBETH": "0xb9fa8F5eC3Da13B508F462243Ad0555B46E028df",
}

# Decimals for each test token — used to format balances when sweeping.
DECIMALS: dict[str, int] = {
    "USDC": 6,
    "COMP": 18,
    "WBTC": 8,
    "CBETH": 18,
}

_FAUCETEER_ABI = [
    {
        "inputs": [{"name": "token", "type": "address"}],
        "name": "drip",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]

# The Fauceteer's custom errors, keyed by 4-byte selector. web3 surfaces revert
# data from gas estimation as a raw selector (it has no contract ABI on that
# path), so we translate it back into a readable reason ourselves.
_FAUCETEER_ERRORS: dict[str, str] = {
    "0xa3281672": "BalanceTooLow (Fauceteer holds none of this token)",
    "0xc5c0d8f6": "RequestedTooFrequently (already dripped in the last 24h)",
    "0x90b8ec18": "TransferFailed",
}


def _reason(exc: Exception) -> str:
    """Translate a web3 revert exception into a readable reason when recognised."""
    text = str(exc)
    for selector, reason in _FAUCETEER_ERRORS.items():
        if selector in text:
            return reason
    return repr(exc)


async def drip(
    address: str,
    private_key: str,
    token: str = "USDC",
    *,
    rpc_url: str = SEPOLIA_RPC_URL,
) -> str:
    """Drip a single Compound III testnet *token* to *address* on Ethereum Sepolia.

    *address* must be the wallet controlled by *private_key*: the Fauceteer
    always sends to ``msg.sender``.
    """
    results = await drip_all(address, private_key, tokens=[token], rpc_url=rpc_url)
    tx, err = results[token.upper()]
    if err:
        raise FaucetError(err)
    return tx  # type: ignore[return-value]


async def drip_all(
    address: str,
    private_key: str,
    tokens: list[str] | None = None,
    *,
    rpc_url: str = SEPOLIA_RPC_URL,
    nonce_manager: NonceManager | None = None,
) -> dict[str, tuple[str | None, str | None]]:
    """Drip Compound III testnet *tokens* (default: all) to *address* on Ethereum
    Sepolia. *address* must be the wallet controlled by *private_key* — the
    Fauceteer only drips to ``msg.sender``.

    Pass a shared *nonce_manager* — the same instance given to
    :func:`faucet.aave.drip_all` — when dripping from one key concurrently so the
    two never reuse a nonce. Returns ``{token: (tx_hash, None)}`` on success or
    ``{token: (None, error)}`` per token; the common failure is
    ``RequestedTooFrequently`` (the 24h-per-token rate limit).
    """
    return await _erc20.drip_all(
        address,
        private_key,
        tokens,
        rpc_url=rpc_url,
        nonce_manager=nonce_manager,
        token_addresses=TOKENS,
        contract_address=_FAUCETEER_ADDRESS,
        contract_abi=_FAUCETEER_ABI,
        make_call=lambda fauceteer, token, token_addr, to: fauceteer.functions.drip(
            token_addr
        ),
        reason=_reason,
        require_signer_is_recipient=True,
    )
