"""
Sweep native tokens, USDC and Compound III testnet tokens to a recipient.

For every EVM chain in :data:`faucet.rpc.EVM_CHAINS`:
  - Native token: send full balance minus gas cost.
  - USDC (where a contract exists): transfer full balance if native covers gas.
  - Compound III tokens (Ethereum Sepolia only): transfer the full balance of
    each Fauceteer token (USDC, COMP, WBTC, cbETH).

Uses EIP-1559 transactions where supported (baseFeePerGas available), falling
back to legacy gasPrice otherwise.

Usage::

    import asyncio, os
    from faucet.sweep import sweep

    asyncio.run(sweep(
        private_key=os.environ["TESTNET_PRIVATE_KEY"],
        to_address=os.environ["TESTNET_ADDRESS"],
    ))
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from typing import cast

from eth_typing import ChecksumAddress
from web3 import AsyncWeb3
from web3.middleware import ExtraDataToPOAMiddleware
from web3.types import TxParams

from faucet.aave import _DECIMALS as _AAVE_DECIMALS
from faucet.aave import TOKENS as _AAVE_TOKENS
from faucet.circle import USDC_CONTRACTS
from faucet.compound import DECIMALS as _COMPOUND_DECIMALS
from faucet.compound import TOKENS as _COMPOUND_TOKENS
from faucet.rpc import EVM_CHAINS

_ERC20_GAS_LIMIT = 100_000
# Ethereum Sepolia does the most work per chain (native + USDC + Compound
# sweeps), so give the per-chain watchdog more headroom.
_CHAIN_TIMEOUT = 120.0
_RPC_REQUEST_TIMEOUT = 10

# On Ethereum Sepolia, leave gas behind for the next Aave faucet run, which
# mints each supported token via on-chain calls (~150k gas each) from this key.
_AAVE_GAS_RESERVE: dict[str, int] = {"ethereum-sepolia": len(_AAVE_TOKENS) * 150_000}

# On Ethereum Sepolia, reserve gas for the Compound III token sweep — one
# ERC-20 transfer per token, run after the native sweep drains the balance.
_COMPOUND_GAS_RESERVE: dict[str, int] = {
    "ethereum-sepolia": len(_COMPOUND_TOKENS) * _ERC20_GAS_LIMIT
}

_ERC20_TRANSFER_ABI = [
    {
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]


@dataclass
class SweepResult:
    chain: str
    token: str
    tx_hash: str
    value: int  # wei for native, smallest unit for ERC-20
    decimals: int = 18  # decimals of `token`, for formatting `value`


async def _build_tx_params(w3: AsyncWeb3, gas_limit: int) -> dict:
    """Return gas params, preferring EIP-1559 when baseFeePerGas is available."""
    latest = await w3.eth.get_block("latest")
    base_fee = getattr(latest, "baseFeePerGas", None)
    if base_fee is not None:
        try:
            priority_fee = await w3.eth.max_priority_fee
        except Exception:
            priority_fee = AsyncWeb3.to_wei(1, "gwei")
        max_fee = base_fee * 2 + priority_fee
        return {
            "gas": gas_limit,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": priority_fee,
        }
    gas_price = await w3.eth.gas_price
    return {"gas": gas_limit, "gasPrice": gas_price}


async def _sweep_chain(
    chain: str,
    rpc_url: str,
    poa: bool,
    symbol: str,
    private_key: str,
    to_address: str,
) -> list[SweepResult]:

    provider = AsyncWeb3.AsyncHTTPProvider(
        rpc_url, request_kwargs={"timeout": _RPC_REQUEST_TIMEOUT}
    )
    w3 = AsyncWeb3(provider)
    if poa:
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    account = w3.eth.account.from_key(private_key)
    sender: ChecksumAddress = AsyncWeb3.to_checksum_address(account.address)
    checksum_to: ChecksumAddress = AsyncWeb3.to_checksum_address(to_address)
    results: list[SweepResult] = []
    try:
        # ── Native token ────────────────────────────────────────────────────
        balance_before = await w3.eth.get_balance(sender)
        print(f"  [{chain}] {symbol} before: {balance_before / 10**18:.6f}")

        if balance_before == 0:
            print(f"  [{chain}] {symbol} skip: zero balance")
        else:
            gas_limit = await w3.eth.estimate_gas(
                {"from": sender, "to": checksum_to, "value": AsyncWeb3.to_wei(1, "wei")}
            )
            gas_params = await _build_tx_params(w3, gas_limit)
            gas_price_eff = gas_params.get(
                "maxFeePerGas", gas_params.get("gasPrice", 0)
            )
            # 3x buffer: covers base-fee fluctuations between estimate and
            # broadcast, plus OP-stack L1 data fees that `estimate_gas` omits
            # but the node still debits at execution (seen on soneium-minato,
            # where a 2x reserve missed by ~6 gwei).
            native_gas_cost = gas_price_eff * gas_limit * 3

            # Reserve ERC-20 gas if this chain has a USDC contract
            usdc_reserve = 0
            if USDC_CONTRACTS.get(chain):
                erc20_gas_params = await _build_tx_params(w3, _ERC20_GAS_LIMIT)
                erc20_price_eff = erc20_gas_params.get(
                    "maxFeePerGas", erc20_gas_params.get("gasPrice", 0)
                )
                usdc_reserve = erc20_price_eff * _ERC20_GAS_LIMIT * 2

            aave_reserve = 0
            aave_gas = _AAVE_GAS_RESERVE.get(chain)
            if aave_gas:
                aave_gas_params = await _build_tx_params(w3, aave_gas)
                aave_price_eff = aave_gas_params.get(
                    "maxFeePerGas", aave_gas_params.get("gasPrice", 0)
                )
                aave_reserve = aave_price_eff * aave_gas * 2

            compound_reserve = 0
            compound_gas = _COMPOUND_GAS_RESERVE.get(chain)
            if compound_gas:
                compound_gas_params = await _build_tx_params(w3, compound_gas)
                compound_price_eff = compound_gas_params.get(
                    "maxFeePerGas", compound_gas_params.get("gasPrice", 0)
                )
                compound_reserve = compound_price_eff * compound_gas * 2

            total_reserve = (
                native_gas_cost + usdc_reserve + aave_reserve + compound_reserve
            )
            if balance_before <= total_reserve:
                print(f"  [{chain}] {symbol} skip: balance below gas cost")
            else:
                value = balance_before - total_reserve
                nonce = await w3.eth.get_transaction_count(sender)
                tx = {
                    "to": checksum_to,
                    "value": value,
                    "nonce": nonce,
                    "chainId": await w3.eth.chain_id,
                    **gas_params,
                }
                signed = account.sign_transaction(tx)
                tx_hash = await w3.eth.send_raw_transaction(signed.raw_transaction)
                receipt = await w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
                status = "ok" if receipt["status"] == 1 else "FAILED"
                balance_after = await w3.eth.get_balance(sender)
                print(
                    f"  [{chain}] {symbol} sent {value / 10**18:.6f} → {checksum_to}  tx={tx_hash.hex()}  {status}"
                )
                print(f"  [{chain}] {symbol} after:  {balance_after / 10**18:.6f}")
                results.append(
                    SweepResult(
                        chain=chain, token=symbol, tx_hash=tx_hash.hex(), value=value
                    )
                )

        # ── USDC ────────────────────────────────────────────────────────────
        try:
            results.extend(
                await _sweep_usdc(chain, w3, sender, checksum_to, account, symbol)
            )
        except Exception as exc:
            print(f"  [{chain}] USDC error: {exc}")

        # ── Compound III testnet tokens ─────────────────────────────────────
        try:
            results.extend(
                await _sweep_compound(chain, w3, sender, checksum_to, account, symbol)
            )
        except Exception as exc:
            print(f"  [{chain}] Compound error: {exc}")

        # ── Aave V3 testnet tokens ─────────────────────────────────────────
        try:
            results.extend(
                await _sweep_aave(chain, w3, sender, checksum_to, account, symbol)
            )
        except Exception as exc:
            print(f"  [{chain}] Aave error: {exc}")
    finally:
        try:
            await asyncio.wait_for(provider.disconnect(), timeout=5)
        except Exception:
            pass
    return results


async def _sweep_usdc(
    chain: str,
    w3: AsyncWeb3,
    sender: ChecksumAddress,
    checksum_to: ChecksumAddress,
    account,
    native_symbol: str,
) -> list[SweepResult]:
    results: list[SweepResult] = []
    usdc_address = USDC_CONTRACTS.get(chain)
    if not usdc_address:
        return results

    usdc = w3.eth.contract(
        address=AsyncWeb3.to_checksum_address(usdc_address), abi=_ERC20_TRANSFER_ABI
    )
    usdc_balance = await usdc.functions.balanceOf(sender).call()
    print(f"  [{chain}] USDC before: {usdc_balance / 10**6:.6f}")

    if usdc_balance == 0:
        print(f"  [{chain}] USDC skip: zero balance")
        return results

    # Re-check native balance for ERC-20 gas (may have changed after native sweep)
    native_now = await w3.eth.get_balance(sender)
    erc20_gas_params = await _build_tx_params(w3, _ERC20_GAS_LIMIT)
    gas_price_eff = erc20_gas_params.get(
        "maxFeePerGas", erc20_gas_params.get("gasPrice", 0)
    )
    erc20_gas_cost = gas_price_eff * _ERC20_GAS_LIMIT * 2

    if native_now < erc20_gas_cost:
        print(
            f"  [{chain}] USDC skip: insufficient {native_symbol} for gas ({native_now / 10**18:.8f} < {erc20_gas_cost / 10**18:.8f})"
        )
        return results

    nonce = await w3.eth.get_transaction_count(sender)
    tx: TxParams = await usdc.functions.transfer(
        checksum_to, usdc_balance
    ).build_transaction(
        cast(
            TxParams,
            {
                "from": sender,
                "nonce": nonce,
                "chainId": await w3.eth.chain_id,
                **erc20_gas_params,
            },
        )
    )
    signed = account.sign_transaction(tx)
    tx_hash = await w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = await w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    status = "ok" if receipt["status"] == 1 else "FAILED"
    usdc_after = await usdc.functions.balanceOf(sender).call()
    print(
        f"  [{chain}] USDC sent {usdc_balance / 10**6:.6f} → {checksum_to}  tx={tx_hash.hex()}  {status}"
    )
    print(f"  [{chain}] USDC after:  {usdc_after / 10**6:.6f}")
    results.append(
        SweepResult(
            chain=chain,
            token="USDC",
            tx_hash=tx_hash.hex(),
            value=usdc_balance,
            decimals=6,
        )
    )

    return results


async def _sweep_compound(
    chain: str,
    w3: AsyncWeb3,
    sender: ChecksumAddress,
    checksum_to: ChecksumAddress,
    account,
    native_symbol: str,
) -> list[SweepResult]:
    """Sweep Compound III testnet ERC-20s — Ethereum Sepolia only.

    Transfers the full balance of each Fauceteer token (USDC, COMP, WBTC,
    cbETH) one tx at a time, skipping tokens with a zero balance.
    """
    results: list[SweepResult] = []
    if chain not in _COMPOUND_GAS_RESERVE:
        return results

    # Compound's Fauceteer drips USDC from the same contract Circle uses on
    # Ethereum Sepolia; _sweep_usdc already drained it, so skip here.
    circle_usdc = (USDC_CONTRACTS.get(chain) or "").lower()
    chain_id = await w3.eth.chain_id
    for symbol, token_address in _COMPOUND_TOKENS.items():
        if token_address.lower() == circle_usdc:
            continue
        decimals = _COMPOUND_DECIMALS[symbol]
        token = w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(token_address),
            abi=_ERC20_TRANSFER_ABI,
        )
        balance = await token.functions.balanceOf(sender).call()
        print(f"  [{chain}] {symbol} before: {balance / 10**decimals:.6f}")
        if balance == 0:
            print(f"  [{chain}] {symbol} skip: zero balance")
            continue

        native_now = await w3.eth.get_balance(sender)
        gas_params = await _build_tx_params(w3, _ERC20_GAS_LIMIT)
        gas_price_eff = gas_params.get("maxFeePerGas", gas_params.get("gasPrice", 0))
        gas_cost = gas_price_eff * _ERC20_GAS_LIMIT * 2
        if native_now < gas_cost:
            print(
                f"  [{chain}] {symbol} skip: insufficient {native_symbol} for gas "
                f"({native_now / 10**18:.8f} < {gas_cost / 10**18:.8f})"
            )
            continue

        nonce = await w3.eth.get_transaction_count(sender)
        tx: TxParams = await token.functions.transfer(
            checksum_to, balance
        ).build_transaction(
            cast(
                TxParams,
                {
                    "from": sender,
                    "nonce": nonce,
                    "chainId": chain_id,
                    **gas_params,
                },
            )
        )
        signed = account.sign_transaction(tx)
        tx_hash = await w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = await w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        status = "ok" if receipt["status"] == 1 else "FAILED"
        balance_after = await token.functions.balanceOf(sender).call()
        print(
            f"  [{chain}] {symbol} sent {balance / 10**decimals:.6f} → {checksum_to}"
            f"  tx={tx_hash.hex()}  {status}"
        )
        print(f"  [{chain}] {symbol} after:  {balance_after / 10**decimals:.6f}")
        results.append(
            SweepResult(
                chain=chain,
                token=symbol,
                tx_hash=tx_hash.hex(),
                value=balance,
                decimals=decimals,
            )
        )
    return results


async def _sweep_aave(
    chain: str,
    w3: AsyncWeb3,
    sender: ChecksumAddress,
    checksum_to: ChecksumAddress,
    account,
    native_symbol: str,
) -> list[SweepResult]:
    """Sweep Aave V3 testnet ERC-20s — Ethereum Sepolia only.

    Transfers the full balance of each Aave-minted token (DAI, USDC, USDT,
    WBTC, LINK, AAVE) one tx at a time, skipping tokens with a zero balance.
    """
    results: list[SweepResult] = []
    if chain not in _AAVE_GAS_RESERVE:
        return results

    chain_id = await w3.eth.chain_id
    for symbol, token_address in _AAVE_TOKENS.items():
        decimals = _AAVE_DECIMALS[symbol]
        token = w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(token_address),
            abi=_ERC20_TRANSFER_ABI,
        )
        balance = await token.functions.balanceOf(sender).call()
        print(f"  [{chain}] {symbol} before: {balance / 10**decimals:.6f}")
        if balance == 0:
            print(f"  [{chain}] {symbol} skip: zero balance")
            continue

        native_now = await w3.eth.get_balance(sender)
        gas_params = await _build_tx_params(w3, _ERC20_GAS_LIMIT)
        gas_price_eff = gas_params.get("maxFeePerGas", gas_params.get("gasPrice", 0))
        gas_cost = gas_price_eff * _ERC20_GAS_LIMIT * 2
        if native_now < gas_cost:
            print(
                f"  [{chain}] {symbol} skip: insufficient {native_symbol} for gas "
                f"({native_now / 10**18:.8f} < {gas_cost / 10**18:.8f})"
            )
            continue

        nonce = await w3.eth.get_transaction_count(sender)
        tx: TxParams = await token.functions.transfer(
            checksum_to, balance
        ).build_transaction(
            cast(
                TxParams,
                {
                    "from": sender,
                    "nonce": nonce,
                    "chainId": chain_id,
                    **gas_params,
                },
            )
        )
        signed = account.sign_transaction(tx)
        tx_hash = await w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = await w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        status = "ok" if receipt["status"] == 1 else "FAILED"
        balance_after = await token.functions.balanceOf(sender).call()
        print(
            f"  [{chain}] {symbol} sent {balance / 10**decimals:.6f} → {checksum_to}"
            f"  tx={tx_hash.hex()}  {status}"
        )
        print(f"  [{chain}] {symbol} after:  {balance_after / 10**decimals:.6f}")
        results.append(
            SweepResult(
                chain=chain,
                token=symbol,
                tx_hash=tx_hash.hex(),
                value=balance,
                decimals=decimals,
            )
        )
    return results


async def sweep(
    private_key: str,
    to_address: str,
    chains: list[str] | None = None,
) -> list[SweepResult]:
    """Sweep native, USDC and Compound III tokens from *private_key* to *to_address*.

    Args:
        private_key: Hex private key of the source wallet (with or without ``0x``).
        to_address: Destination wallet address.
        chains: Chain slugs to sweep.  Defaults to all chains in :data:`faucet.rpc.EVM_CHAINS`.

    Returns:
        List of :class:`SweepResult` for each successful transfer.
    """
    target_chains = [c for c in (chains or list(EVM_CHAINS)) if c in EVM_CHAINS]
    for c in chains or []:
        if c not in EVM_CHAINS:
            print(f"  [{c}] unknown chain, skip")

    async def _run(chain: str) -> list[SweepResult]:
        rpc_url, poa, symbol = EVM_CHAINS[chain]
        try:
            return await asyncio.wait_for(
                _sweep_chain(chain, rpc_url, poa, symbol, private_key, to_address),
                timeout=_CHAIN_TIMEOUT,
            )
        except asyncio.TimeoutError:
            print(f"  [{chain}] timed out after {_CHAIN_TIMEOUT:.0f}s, skip")
        except Exception as exc:
            print(f"  [{chain}] error: {exc}")
        return []

    batches = await asyncio.gather(*(_run(c) for c in target_chains))
    return [r for batch in batches for r in batch]
