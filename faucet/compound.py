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

import asyncio
import logging

from web3 import AsyncWeb3

from faucet.alchemy import FaucetError
from faucet.rpc import SEPOLIA_RPC_URL

log = logging.getLogger(__name__)

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
) -> dict[str, tuple[str | None, str | None]]:
    """Drip multiple Compound III testnet tokens to *address* on Ethereum Sepolia.

    Sends all transactions sequentially from the same key so nonces never
    collide, then waits for all receipts in parallel.

    Args:
        address: Wallet to receive tokens. Must be the wallet controlled by
            *private_key* — the Fauceteer can only drip to ``msg.sender``.
        private_key: Private key of the wallet paying for gas (and receiving
            the drip).
        tokens: Token symbols to drip. Defaults to all supported tokens.
        rpc_url: Ethereum Sepolia RPC endpoint.

    Returns:
        ``{token: (tx_hash, None)}`` on success or ``{token: (None, error)}``
        on failure, for each requested token. The most common failure is
        ``RequestedTooFrequently`` — the 24h-per-token rate limit.
    """
    want = [t.upper() for t in (tokens or list(TOKENS))]
    unknown = [t for t in want if t not in TOKENS]
    if unknown:
        raise ValueError(f"unknown token(s): {unknown}. Supported: {sorted(TOKENS)}")

    w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(rpc_url))
    account = w3.eth.account.from_key(private_key)
    recipient = AsyncWeb3.to_checksum_address(address)
    if recipient != account.address:
        raise ValueError(
            f"address {recipient} does not match the private key's wallet "
            f"{account.address}; the Fauceteer can only drip to the signer"
        )
    fauceteer = w3.eth.contract(
        address=AsyncWeb3.to_checksum_address(_FAUCETEER_ADDRESS),
        abi=_FAUCETEER_ABI,
    )
    nonce = await w3.eth.get_transaction_count(account.address)
    log.info("dripping %d token(s) to %s (nonce=%d)", len(want), account.address, nonce)
    sent: dict[str, str] = {}
    errors: dict[str, str] = {}

    for token in want:
        token_addr = AsyncWeb3.to_checksum_address(TOKENS[token])
        try:
            tx = await fauceteer.functions.drip(token_addr).build_transaction(
                {"from": account.address, "nonce": nonce}
            )
            signed = account.sign_transaction(tx)
            tx_hash = await w3.eth.send_raw_transaction(signed.raw_transaction)
            log.info("%s sent  tx=%s (nonce=%d)", token, tx_hash.hex(), nonce)
            sent[token] = tx_hash.hex()
            nonce += 1
        except Exception as exc:
            reason = _reason(exc)
            log.warning("%s send failed: %s", token, reason)
            errors[token] = reason

    async def _wait(token: str, tx_hash: str) -> tuple[str, str | None, str | None]:
        log.debug("%s waiting for receipt tx=%s", token, tx_hash)
        try:
            receipt = await w3.eth.wait_for_transaction_receipt(
                tx_hash,
                timeout=120,
                poll_latency=5,
            )
            if receipt["status"] != 1:
                log.warning("%s reverted tx=%s", token, tx_hash)
                return token, None, f"reverted (tx={tx_hash})"
            log.info("%s confirmed block=%s", token, receipt["blockNumber"])
            return token, tx_hash, None
        except Exception as exc:
            reason = _reason(exc)
            log.warning("%s receipt error: %s", token, reason)
            return token, None, reason

    receipts = await asyncio.gather(*[_wait(t, h) for t, h in sent.items()])

    result: dict[str, tuple[str | None, str | None]] = {}
    for token, tx_hash, err in receipts:
        result[token] = (tx_hash, err)
    for token, err in errors.items():
        result[token] = (None, err)
    return result
