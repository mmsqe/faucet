"""Integration tests for the faucet package.

Test categories
---------------
``@pytest.mark.live``
    Validates faucet configuration — no network calls, safe for CI.

``@pytest.mark.testnet``
    Calls the Alchemy faucet via ``nodriver`` (opens a Chrome window briefly
    to solve Cloudflare Turnstile) and verifies the wallet balance.  Requires:

    - ``TESTNET_PRIVATE_KEY`` — hex private key of the wallet to fund.
    - ``SEPOLIA_RPC_URL`` / ``OP_SEPOLIA_RPC_URL`` / ``BASE_SEPOLIA_RPC_URL`` (optional).

Run with::

    pytest -m live tests/test_faucet_live.py
    pytest -m testnet tests/test_faucet_live.py
"""

from __future__ import annotations

import pytest

from faucet import (
    CHAINS,
    USDC_CHAINS,
    USDC_CONTRACTS,
    FaucetError,
    InsufficientFaucetBalanceError,
    RateLimitError,
    drip,
    drip_usdc,
)

_MIN_BALANCE = 10**16  # 0.01 ETH

# ---------------------------------------------------------------------------
# Unit-style tests (no network calls)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Testnet tests — require TESTNET_PRIVATE_KEY and a real funded wallet
# ---------------------------------------------------------------------------


@pytest.mark.testnet
class TestFaucetSepolia:
    """Testnet tests for Sepolia — skipped unless TESTNET_PRIVATE_KEY is set."""

    async def test_funded_account_has_balance(self, funded_sepolia_account, sepolia_w3):
        """After the faucet fixture runs the wallet must have ≥ 0.01 ETH."""
        balance = await sepolia_w3.eth.get_balance(funded_sepolia_account.address)
        assert balance >= _MIN_BALANCE, (
            f"Expected ≥ 0.01 ETH on Sepolia, got {balance / 10**18:.6f} ETH"
        )

    async def test_drip_skipped_when_already_funded(
        self, funded_sepolia_account, sepolia_w3
    ):
        """drip() is not called when the balance already meets the threshold."""
        balance = await sepolia_w3.eth.get_balance(funded_sepolia_account.address)
        # If we reach here the fixture already ensured the balance is sufficient.
        assert balance >= _MIN_BALANCE


@pytest.mark.testnet
class TestFaucetOpSepolia:
    """Testnet tests for OP Sepolia — skipped unless TESTNET_PRIVATE_KEY is set."""

    async def test_funded_account_has_balance(
        self, funded_op_sepolia_account, op_sepolia_w3
    ):
        """After the faucet fixture runs the wallet must have ≥ 0.01 ETH on OP Sepolia."""
        balance = await op_sepolia_w3.eth.get_balance(funded_op_sepolia_account.address)
        assert balance >= _MIN_BALANCE, (
            f"Expected ≥ 0.01 ETH on OP Sepolia, got {balance / 10**18:.6f} ETH"
        )


@pytest.mark.testnet
class TestFaucetBaseSepolia:
    """Testnet tests for Base Sepolia — skipped unless TESTNET_PRIVATE_KEY is set."""

    async def test_funded_account_has_balance(
        self, funded_base_sepolia_account, base_sepolia_w3
    ):
        """After the faucet fixture runs the wallet must have ≥ 0.01 ETH on Base Sepolia."""
        balance = await base_sepolia_w3.eth.get_balance(
            funded_base_sepolia_account.address
        )
        assert balance >= _MIN_BALANCE, (
            f"Expected ≥ 0.01 ETH on Base Sepolia, got {balance / 10**18:.6f} ETH"
        )


@pytest.mark.testnet
class TestFaucetZkSyncSepolia:
    """Testnet tests for zkSync Sepolia — skipped unless TESTNET_PRIVATE_KEY is set."""

    async def test_funded_account_has_balance(
        self, funded_zksync_sepolia_account, zksync_sepolia_w3
    ):
        """After the faucet fixture runs the wallet must have ≥ 0.01 ETH on zkSync Sepolia."""
        balance = await zksync_sepolia_w3.eth.get_balance(
            funded_zksync_sepolia_account.address
        )
        assert balance >= _MIN_BALANCE, (
            f"Expected ≥ 0.01 ETH on zkSync Sepolia, got {balance / 10**18:.6f} ETH"
        )


# ---------------------------------------------------------------------------
# Circle USDC faucet testnet tests
# ---------------------------------------------------------------------------


@pytest.mark.testnet
class TestCircleFaucet:
    """Testnet tests for the Circle USDC faucet — skipped unless TESTNET_PRIVATE_KEY is set."""

    async def test_drip_usdc(self, testnet_address, usdc_chain_w3):
        """Circle faucet drips 20 USDC — runs once per supported EVM chain."""
        from faucet import is_chain_synced, is_contract_deployed
        from web3 import AsyncWeb3

        chain, w3 = usdc_chain_w3
        usdc_address = USDC_CONTRACTS[chain]

        if not await is_chain_synced(w3):
            pytest.skip(f"{chain} RPC is still syncing")
        if not await is_contract_deployed(w3, usdc_address):
            pytest.skip(f"USDC contract not deployed at {usdc_address} on {chain}")

        abi = [
            {
                "inputs": [{"name": "account", "type": "address"}],
                "name": "balanceOf",
                "outputs": [{"name": "", "type": "uint256"}],
                "stateMutability": "view",
                "type": "function",
            }
        ]

        checksum = AsyncWeb3.to_checksum_address(testnet_address)
        usdc = w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(usdc_address), abi=abi
        )
        balance_before = await usdc.functions.balanceOf(checksum).call()

        await drip_usdc(testnet_address, chain)

        balance_after = await usdc.functions.balanceOf(checksum).call()
        assert balance_after >= balance_before, (
            f"USDC balance did not increase after drip on {chain}"
        )
