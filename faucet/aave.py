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

from faucet import _erc20
from faucet.alchemy import FaucetError
from faucet.nonce import NonceManager
from faucet.rpc import SEPOLIA_RPC_URL

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
    nonce_manager: NonceManager | None = None,
) -> dict[str, tuple[str | None, str | None]]:
    """Mint Aave V3 testnet *tokens* (default: all) to *address* on Ethereum Sepolia.

    Pass a shared *nonce_manager* — the same instance given to
    :func:`faucet.compound.drip_all` — when minting from one key concurrently so
    the two never reuse a nonce. Returns ``{token: (tx_hash, None)}`` on success
    or ``{token: (None, error)}`` per requested token.
    """
    return await _erc20.drip_all(
        address,
        private_key,
        tokens,
        rpc_url=rpc_url,
        nonce_manager=nonce_manager,
        token_addresses=TOKENS,
        contract_address=_FAUCET_ADDRESS,
        contract_abi=_FAUCET_ABI,
        make_call=lambda faucet, token, token_addr, to: faucet.functions.mint(
            token_addr, to, _MINT_AMOUNT[token]
        ),
        log_verb="minting",
    )
