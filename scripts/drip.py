"""Drip native tokens and USDC on all supported testnet chains."""

import asyncio
import gc
import logging
import os
import sys
from typing import Any

from faucet import CHAINS, USDC_CHAINS, drip, drip_usdc
from faucet import aave as _aave
from faucet import chainstack as _chainstack
from faucet.sweep import sweep

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
logging.getLogger("nodriver").setLevel(logging.WARNING)
logging.getLogger("uc").setLevel(logging.WARNING)

address = os.environ.get("TESTNET_ADDRESS", "")
if not address:
    sys.exit("TESTNET_ADDRESS is not set")

private_key = os.environ.get("TESTNET_PRIVATE_KEY", "")
sweep_to = os.environ.get("SWEEP_TO_ADDRESS", "")

_ALL_NATIVE_CHAINS: list[str] = sorted(
    set(CHAINS.keys()) | (_chainstack.CHAINS - set(CHAINS.keys()))
)

# Circle USDC faucet only supports EVM chains (no solana-devnet etc.)
_USDC_EVM_CHAINS: list[str] = [c for c in USDC_CHAINS if not c.startswith("solana")]

_sem = asyncio.Semaphore(3)
# Circle's faucet (faucet.circle.com) is fronted by Cloudflare and 1015's
# the runner IP when hit in parallel. Serialize Circle drips with a delay.
_circle_sem = asyncio.Semaphore(1)
_CIRCLE_GAP_SECONDS = 8.0


async def _drip_native(chain: str) -> tuple[str, str | None, str | None]:
    async with _sem:
        try:
            tx = await drip(address, chain)
            return chain, tx, None
        except Exception as exc:
            return chain, None, repr(exc)


async def _drip_usdc_chain(chain: str) -> tuple[str, str | None]:
    async with _circle_sem:
        try:
            await drip_usdc(address, chain)
            return chain, None
        except Exception as exc:
            return chain, repr(exc)
        finally:
            await asyncio.sleep(_CIRCLE_GAP_SECONDS)


async def main() -> None:
    do_native = True
    do_usdc = True
    do_aave = bool(private_key)
    print(
        f"Funding {address} on {len(_ALL_NATIVE_CHAINS)} native chains, "
        f"{len(_USDC_EVM_CHAINS)} USDC chains"
        + (f", and {len(_aave.TOKENS)} Aave tokens" if do_aave else "")
        + "\n"
    )

    gather_fns: dict[str, Any] = {}
    if do_native:
        gather_fns["native"] = asyncio.gather(
            *[_drip_native(c) for c in _ALL_NATIVE_CHAINS]
        )
    if do_usdc:
        gather_fns["usdc"] = asyncio.gather(
            *[_drip_usdc_chain(c) for c in _USDC_EVM_CHAINS]
        )
    if do_aave:
        gather_fns["aave"] = _aave.drip_all(address, private_key)

    results = dict(zip(gather_fns, await asyncio.gather(*gather_fns.values())))

    if do_native:
        print("Native tokens:")
        for chain, tx, err in results["native"]:
            if err:
                print(f"  {chain}: ERROR — {err}")
            else:
                print(f"  {chain}: tx={tx}")

    if do_usdc:
        print("\nUSDC:")
        for chain, err in results["usdc"]:
            if err:
                print(f"  {chain}: ERROR — {err}")
            else:
                print(f"  {chain}: ok")

    aave_result: dict = results.get("aave", {})
    if aave_result:
        print("\nAave (Ethereum Sepolia):")
        for token, (tx_hash, err) in aave_result.items():
            if err:
                print(f"  {token}: ERROR — {err}")
            else:
                print(f"  {token}: tx={tx_hash}")


async def _sweep() -> None:
    if not private_key or not sweep_to:
        return
    from web3 import Web3

    sender = Web3().eth.account.from_key(private_key).address
    if sender.lower() == sweep_to.lower():
        print("\nSweep skipped: sender == recipient")
        return
    print(f"\nSweeping {sender} → {sweep_to}")
    results = await sweep(private_key, sweep_to)
    if results:
        for r in results:
            decimals = 6 if r.token == "USDC" else 18
            print(
                f"  {r.chain}: {r.value / 10**decimals:.6f} {r.token}  tx={r.tx_hash}"
            )
    else:
        print("  Nothing to sweep.")


loop = asyncio.new_event_loop()
try:
    loop.run_until_complete(main())
    loop.run_until_complete(_sweep())
    gc.collect()
    loop.run_until_complete(asyncio.sleep(0.25))
finally:
    loop.close()
