"""Aave V3 Ethereum Sepolia testnet faucet — mints tokens via on-chain call.

Unlike the other faucet providers, Aave's faucet is a smart contract: the
caller pays gas and calls ``mint(token, to, amount)`` on a permissionless
contract.  A private key is required.

Supported tokens (Ethereum Sepolia only):
    GHO, DAI, USDC, USDT, WBTC, LINK, AAVE

Note: WETH is excluded — it is a wrapped token (deposit ETH to get WETH) and
cannot be minted via the faucet contract.  GHO may fail with
``FACILITATOR_BUCKET_CAPACITY_EXCEEDED`` when the protocol bucket is full;
this resolves automatically over time.
"""

from __future__ import annotations

import asyncio
import logging

from web3 import AsyncWeb3

from faucet.alchemy import FaucetError
from faucet.rpc import SEPOLIA_RPC_URL

log = logging.getLogger(__name__)

_FAUCET_ADDRESS = "0xC959483DBa39aa9E78757139af0e9a2EDEb3f42D"

# Token contract addresses on Ethereum Sepolia
TOKENS: dict[str, str] = {
    # "GHO": "0xc4bF5CbDaBE595361438F8c6a187bDc330539c60",
    "DAI": "0xFF34B3d4Aee8ddCd6F9AFFFB6Fe49bD371b8a357",
    "USDC": "0x94a9D9AC8a22534E3FaCa9F4e7F2E2cf85d5E4C8",
    "USDT": "0xaA8E23Fb1079EA71e0a56F48a2aA51851D8433D0",
    "WBTC": "0x29f2D40B0605204364af54EC677bD022dA425d03",
    "LINK": "0xf8Fb3713D459D7C1018BD0A49D19b4C44290EBE5",
    "AAVE": "0x88541670E55cC00bEEFD87eB59EDd1b7C511AC9a",
}

_DECIMALS: dict[str, int] = {
    # "GHO": 18,
    "DAI": 18,
    "USDC": 6,
    "USDT": 6,
    "WBTC": 8,
    "LINK": 18,
    "AAVE": 18,
}

# Max tokens to mint per call (contract enforces its own ceiling)
_MINT_AMOUNT: dict[str, int] = {t: 10_000 * 10 ** _DECIMALS[t] for t in TOKENS}

_FAUCET_ABI = [
    {
        "inputs": [
            {"name": "token", "type": "address"},
            {"name": "to", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "mint",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]


async def drip(
    address: str,
    private_key: str,
    token: str = "GHO",
    *,
    rpc_url: str = SEPOLIA_RPC_URL,
) -> str:
    """Mint a single Aave V3 testnet *token* to *address* on Ethereum Sepolia."""
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
    """Mint multiple Aave V3 testnet tokens to *address* on Ethereum Sepolia.

    Sends all transactions sequentially from the same key so nonces never
    collide, then waits for all receipts in parallel.

    Args:
        address: Wallet address to receive tokens.
        private_key: Private key of the wallet paying for gas.
        tokens: Token symbols to mint. Defaults to all supported tokens.
        rpc_url: Ethereum Sepolia RPC endpoint.

    Returns:
        ``{token: (tx_hash, None)}`` on success or ``{token: (None, error)}``
        on failure, for each requested token.
    """
    want = [t.upper() for t in (tokens or list(TOKENS))]
    unknown = [t for t in want if t not in TOKENS]
    if unknown:
        raise ValueError(f"unknown token(s): {unknown}. Supported: {sorted(TOKENS)}")

    w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(rpc_url))
    account = w3.eth.account.from_key(private_key)
    to = AsyncWeb3.to_checksum_address(address)
    faucet = w3.eth.contract(
        address=AsyncWeb3.to_checksum_address(_FAUCET_ADDRESS),
        abi=_FAUCET_ABI,
    )
    nonce = await w3.eth.get_transaction_count(account.address)
    log.info(
        "minting %d token(s) from %s (nonce=%d)", len(want), account.address, nonce
    )
    sent: dict[str, str] = {}
    errors: dict[str, str] = {}

    for token in want:
        token_addr = AsyncWeb3.to_checksum_address(TOKENS[token])
        try:
            tx = await faucet.functions.mint(
                token_addr, to, _MINT_AMOUNT[token]
            ).build_transaction({"from": account.address, "nonce": nonce})
            signed = account.sign_transaction(tx)
            tx_hash = await w3.eth.send_raw_transaction(signed.raw_transaction)
            log.info("%s sent  tx=%s (nonce=%d)", token, tx_hash.hex(), nonce)
            sent[token] = tx_hash.hex()
            nonce += 1
        except Exception as exc:
            log.warning("%s send failed: %r", token, exc)
            errors[token] = repr(exc)

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
            log.warning("%s receipt error: %r", token, exc)
            return token, None, repr(exc)

    receipts = await asyncio.gather(*[_wait(t, h) for t, h in sent.items()])

    result: dict[str, tuple[str | None, str | None]] = {}
    for token, tx_hash, err in receipts:
        result[token] = (tx_hash, err)
    for token, err in errors.items():
        result[token] = (None, err)
    return result
