"""Tests for the faucet package.

Run with::

    pytest -m live tests/test_faucet_live.py
"""

from __future__ import annotations

import pytest
from eth_account import Account
from web3 import AsyncHTTPProvider, AsyncWeb3

from faucet import (
    CHAINS,
    USDC_CHAINS,
    FaucetError,
    InsufficientFaucetBalanceError,
    RateLimitError,
    drip,
    drip_usdc,
    tempo,
)
import asyncio


@pytest.mark.live
class TestFaucetConfig:
    """Verify the CHAINS registry and error handling without network calls."""

    def test_chains_includes_ethereum_sepolia(self):
        assert "ethereum-sepolia" in CHAINS

    def test_chains_includes_optimism_sepolia(self):
        assert "optimism-sepolia" in CHAINS

    def test_chains_includes_base_sepolia(self):
        assert "base-sepolia" in CHAINS

    def test_chains_includes_zksync_sepolia(self):
        assert "zksync-sepolia" in CHAINS

    def test_faucet_error_hierarchy(self):
        assert issubclass(RateLimitError, FaucetError)
        assert issubclass(InsufficientFaucetBalanceError, FaucetError)

    def test_drip_raises_on_unknown_chain(self):

        with pytest.raises(ValueError, match="Unknown chain"):
            asyncio.run(
                drip("0x0000000000000000000000000000000000000001", "unknown-chain")
            )

    def test_chainstack_chains_includes_hyperliquid_testnet(self):
        from faucet import chainstack

        assert "hyperliquid-testnet" in chainstack.CHAINS

    def test_usdc_chains_includes_key_chains(self):
        for chain in (
            "ethereum-sepolia",
            "arbitrum-sepolia",
            "base-sepolia",
            "optimism-sepolia",
            "polygon-amoy",
            "avalanche-fuji",
            "linea-sepolia",
            "zksync-sepolia",
            "unichain-sepolia",
            "solana-devnet",
        ):
            assert chain in USDC_CHAINS, f"{chain!r} missing from USDC_CHAINS"

    def test_drip_usdc_raises_on_unknown_chain(self):

        with pytest.raises(ValueError, match="unknown chain"):
            asyncio.run(
                drip_usdc("0x0000000000000000000000000000000000000001", "unknown-chain")
            )

    def test_drip_usdc_raises_on_unknown_token(self):

        with pytest.raises(ValueError, match="unsupported token"):
            asyncio.run(
                drip_usdc(
                    "0x0000000000000000000000000000000000000001",
                    "base-sepolia",
                    token="DAI",
                )
            )


@pytest.mark.live
class TestTempoConfig:
    def test_chain(self):
        assert tempo.CHAINS == {"tempo-moderato", "tempo-testnet"}
        assert tempo.CHAIN_ID == 42431
        # StdTokens.sol addresses: 0x20c0…0000 through …0003.
        assert tempo.TOKENS["pathUSD"].lower().startswith("0x20c00000")
        with pytest.raises(ValueError, match="unknown chain"):
            asyncio.run(
                tempo.drip(
                    "0x0000000000000000000000000000000000000001", "unknown-chain"
                )
            )

    async def test_drip_mints_exactly_tokens(self):
        """Calls the public faucet: its successful mints are exactly TOKENS."""
        hashes = await tempo.drip(Account.create().address)
        w3 = AsyncWeb3(
            AsyncHTTPProvider(
                tempo._rpc_url("tempo-moderato"),
                request_kwargs={"headers": tempo._HEADERS},
            )
        )
        receipts = [await w3.eth.wait_for_transaction_receipt(h) for h in hashes]
        minted = {r["to"] for r in receipts if r["status"] == 1}
        assert minted == set(tempo.TOKENS.values())
