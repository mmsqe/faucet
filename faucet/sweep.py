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
# USDC sweep cycles of native gas to reserve. CI runs daily and skips the
# native drip; local runs refill native ~monthly.
_USDC_SWEEP_CYCLES = 30
# Ethereum Sepolia does the most work per chain (native + USDC + Compound
# sweeps), so give the per-chain watchdog more headroom.
_CHAIN_TIMEOUT = 120.0
_RPC_REQUEST_TIMEOUT = 10

# On Ethereum Sepolia, leave gas behind for both the post-mint Aave sweep
# (this cycle: one ERC-20 transfer per token) AND the next Aave faucet run
# (which mints each supported token via on-chain calls, ~150k gas each).
# Without the sweep half, freshly-minted tokens get stuck because mint cost
# eats the reserve before the Aave sweep runs.
_AAVE_GAS_RESERVE: dict[str, int] = {
    "ethereum-sepolia": len(_AAVE_TOKENS) * (150_000 + _ERC20_GAS_LIMIT)
}

# Same shape for Compound III: reserve covers both the post-drip sweep (one
# ERC-20 transfer per token) and the next Fauceteer drip cycle.
_COMPOUND_GAS_RESERVE: dict[str, int] = {
    "ethereum-sepolia": len(_COMPOUND_TOKENS) * (_ERC20_GAS_LIMIT + _ERC20_GAS_LIMIT)
}

# OP-stack L1 data fee oracle. estimate_gas omits this fee; the sequencer
# still debits it, so without reserving it the broadcast fails with
# "insufficient funds for gas * price + value" (seen on soneium-minato).
_OP_GAS_PRICE_ORACLE = "0x420000000000000000000000000000000000000F"
_OP_GAS_PRICE_ORACLE_ABI = [
    {
        "inputs": [{"name": "_data", "type": "bytes"}],
        "name": "getL1Fee",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }
]


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


def _effective_gas_price(gas_params: dict) -> int:
    """The per-gas price from :func:`_build_tx_params`, EIP-1559 or legacy."""
    return gas_params.get("maxFeePerGas", gas_params.get("gasPrice", 0))


async def _gas_reserve(w3: AsyncWeb3, gas_limit: int, multiplier: int) -> int:
    """Native cost of one *gas_limit* tx times *multiplier*, at current prices."""
    gas_params = await _build_tx_params(w3, gas_limit)
    return _effective_gas_price(gas_params) * gas_limit * multiplier


async def _op_stack_l1_fee(
    w3: AsyncWeb3,
    account,
    to: ChecksumAddress,
    value: int,
    chain_id: int,
    gas_params: dict,
) -> int:
    """Return the OP-stack L1 data fee for a sweep tx, or 0 if not OP-stack.

    Uses a placeholder ``nonce=0`` over the actual ``value`` so the signed RLP
    size handed to ``getL1Fee`` is within a few bytes of the broadcast tx;
    ``value`` should be an upper bound (e.g. the wallet's full balance) so the
    returned fee covers the slightly-smaller real broadcast.
    """
    tx = {
        "to": to,
        "value": value,
        "nonce": 0,
        "chainId": chain_id,
        **gas_params,
    }
    signed = account.sign_transaction(tx)
    oracle = w3.eth.contract(
        address=AsyncWeb3.to_checksum_address(_OP_GAS_PRICE_ORACLE),
        abi=_OP_GAS_PRICE_ORACLE_ABI,
    )
    try:
        return await oracle.functions.getL1Fee(signed.raw_transaction).call()
    except Exception:
        # No GasPriceOracle predeploy at this address — not an OP-stack chain.
        return 0


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
            # 3x buffer: covers base-fee fluctuations between estimate and
            # broadcast, plus OP-stack L1 data fees that `estimate_gas` omits
            # but the node still debits at execution (seen on soneium-minato,
            # where a 2x reserve missed by ~6 gwei).
            native_gas_cost = _effective_gas_price(gas_params) * gas_limit * 3

            # Reserve native gas to sweep USDC for _USDC_SWEEP_CYCLES runs;
            # see that constant for why it's not just 1.
            usdc_reserve = 0
            if USDC_CONTRACTS.get(chain):
                usdc_reserve = await _gas_reserve(
                    w3, _ERC20_GAS_LIMIT, _USDC_SWEEP_CYCLES
                )

            aave_reserve = 0
            aave_gas = _AAVE_GAS_RESERVE.get(chain)
            if aave_gas:
                aave_reserve = await _gas_reserve(w3, aave_gas, 2)

            compound_reserve = 0
            compound_gas = _COMPOUND_GAS_RESERVE.get(chain)
            if compound_gas:
                compound_reserve = await _gas_reserve(w3, compound_gas, 2)

            # OP-stack L1 data fee: the sequencer debits this on top of L2 gas
            # at execution; ``estimate_gas`` doesn't include it. Pass full
            # balance as the placeholder value to upper-bound the RLP size.
            chain_id = await w3.eth.chain_id
            l1_fee_reserve = await _op_stack_l1_fee(
                w3, account, checksum_to, balance_before, chain_id, gas_params
            )

            total_reserve = (
                native_gas_cost
                + usdc_reserve
                + aave_reserve
                + compound_reserve
                + l1_fee_reserve
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
                    "chainId": chain_id,
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
        usdc_address = USDC_CONTRACTS.get(chain)
        if usdc_address:
            try:
                results.extend(
                    await _sweep_tokens(
                        chain,
                        w3,
                        sender,
                        checksum_to,
                        account,
                        symbol,
                        {"USDC": usdc_address},
                        {"USDC": 6},
                    )
                )
            except Exception as exc:
                print(f"  [{chain}] USDC error: {exc}")

        # ── Compound III (Ethereum Sepolia) ─────────────────────────────────
        # Skip the USDC contract: the Fauceteer drips the same one Circle uses,
        # which the USDC sweep above already drained.
        if chain in _COMPOUND_GAS_RESERVE:
            circle_usdc = (usdc_address or "").lower()
            try:
                results.extend(
                    await _sweep_tokens(
                        chain,
                        w3,
                        sender,
                        checksum_to,
                        account,
                        symbol,
                        _COMPOUND_TOKENS,
                        _COMPOUND_DECIMALS,
                        skip_addresses=frozenset({circle_usdc})
                        if circle_usdc
                        else frozenset(),
                    )
                )
            except Exception as exc:
                print(f"  [{chain}] Compound error: {exc}")

        # ── Aave V3 (Ethereum Sepolia) ──────────────────────────────────────
        if chain in _AAVE_GAS_RESERVE:
            try:
                results.extend(
                    await _sweep_tokens(
                        chain,
                        w3,
                        sender,
                        checksum_to,
                        account,
                        symbol,
                        _AAVE_TOKENS,
                        _AAVE_DECIMALS,
                    )
                )
            except Exception as exc:
                print(f"  [{chain}] Aave error: {exc}")
    finally:
        try:
            await asyncio.wait_for(provider.disconnect(), timeout=5)
        except Exception:
            pass
    return results


async def _sweep_tokens(
    chain: str,
    w3: AsyncWeb3,
    sender: ChecksumAddress,
    checksum_to: ChecksumAddress,
    account,
    native_symbol: str,
    tokens: dict[str, str],
    decimals: dict[str, int],
    *,
    skip_addresses: frozenset[str] = frozenset(),
) -> list[SweepResult]:
    """Transfer each ERC-20's full balance to *checksum_to*, one tx per token.

    Skips a token with a zero balance, a lower-cased address in *skip_addresses*,
    or when native balance can't cover gas (re-read per token).
    """
    results: list[SweepResult] = []
    chain_id = await w3.eth.chain_id
    for symbol, token_address in tokens.items():
        if token_address.lower() in skip_addresses:
            continue
        dec = decimals[symbol]
        token = w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(token_address),
            abi=_ERC20_TRANSFER_ABI,
        )
        balance = await token.functions.balanceOf(sender).call()
        print(f"  [{chain}] {symbol} before: {balance / 10**dec:.6f}")
        if balance == 0:
            print(f"  [{chain}] {symbol} skip: zero balance")
            continue

        native_now = await w3.eth.get_balance(sender)
        gas_params = await _build_tx_params(w3, _ERC20_GAS_LIMIT)
        gas_cost = _effective_gas_price(gas_params) * _ERC20_GAS_LIMIT * 2
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
            f"  [{chain}] {symbol} sent {balance / 10**dec:.6f} → {checksum_to}"
            f"  tx={tx_hash.hex()}  {status}"
        )
        print(f"  [{chain}] {symbol} after:  {balance_after / 10**dec:.6f}")
        results.append(
            SweepResult(
                chain=chain,
                token=symbol,
                tx_hash=tx_hash.hex(),
                value=balance,
                decimals=dec,
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
