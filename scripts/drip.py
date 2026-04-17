"""Drip native tokens and USDC on all supported testnet chains."""

import asyncio
import gc
import os
import sys

from dotenv import load_dotenv

from faucet import CHAINS, USDC_CHAINS, drip, drip_usdc
from faucet import chainstack as _chainstack

load_dotenv()

address = os.environ.get("TESTNET_ADDRESS", "")
if not address:
    sys.exit("TESTNET_ADDRESS is not set")

_ALL_NATIVE_CHAINS: list[str] = sorted(
    set(CHAINS.keys()) | (_chainstack.CHAINS - set(CHAINS.keys()))
)

# Circle USDC faucet only supports EVM chains (no solana-devnet etc.)
_USDC_EVM_CHAINS: list[str] = [c for c in USDC_CHAINS if not c.startswith("solana")]

_sem = asyncio.Semaphore(3)


async def _drip_native(chain: str) -> tuple[str, str | None, str | None]:
    async with _sem:
        try:
            tx = await drip(address, chain)
            return chain, tx, None
        except Exception as exc:
            return chain, None, str(exc)


async def _drip_usdc_chain(chain: str) -> tuple[str, str | None]:
    async with _sem:
        try:
            await drip_usdc(address, chain)
            return chain, None
        except Exception as exc:
            return chain, str(exc)


async def main() -> None:
    print(
        f"Funding {address} on {len(_ALL_NATIVE_CHAINS)} native chains "
        f"and {len(_USDC_EVM_CHAINS)} USDC chains\n"
    )

    native_results, usdc_results = await asyncio.gather(
        asyncio.gather(*[_drip_native(c) for c in _ALL_NATIVE_CHAINS]),
        asyncio.gather(*[_drip_usdc_chain(c) for c in _USDC_EVM_CHAINS]),
    )

    print("Native tokens:")
    for chain, tx, err in native_results:
        if err:
            print(f"  {chain}: ERROR — {err}")
        else:
            print(f"  {chain}: tx={tx}")

    print("\nUSDC:")
    for chain, err in usdc_results:
        if err:
            print(f"  {chain}: ERROR — {err}")
        else:
            print(f"  {chain}: ok")


loop = asyncio.new_event_loop()
try:
    loop.run_until_complete(main())
    gc.collect()
    loop.run_until_complete(asyncio.sleep(0.25))
finally:
    loop.close()
