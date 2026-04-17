"""Unit-style tests for the faucet package (no network calls).

Run with::

    pytest -m live tests/test_faucet_live.py
"""

from __future__ import annotations

import pytest

from faucet import (
    CHAINS,
    USDC_CHAINS,
    FaucetError,
    InsufficientFaucetBalanceError,
    RateLimitError,
    drip,
    drip_usdc,
)


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
        import asyncio

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
        import asyncio

        with pytest.raises(ValueError, match="unknown chain"):
            asyncio.run(
                drip_usdc("0x0000000000000000000000000000000000000001", "unknown-chain")
            )

    def test_drip_usdc_raises_on_unknown_token(self):
        import asyncio

        with pytest.raises(ValueError, match="unsupported token"):
            asyncio.run(
                drip_usdc(
                    "0x0000000000000000000000000000000000000001",
                    "base-sepolia",
                    token="DAI",
                )
            )
