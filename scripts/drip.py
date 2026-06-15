"""Drip native tokens and USDC on all supported testnet chains."""

import asyncio
import gc
import logging
import os
import sys
from typing import Any

from web3 import AsyncWeb3

from faucet import CHAINS, LINK_CHAINS, USDC_CHAINS, drip, drip_link_all, drip_usdc
from faucet import aave as _aave
from faucet import babylon as _babylon
from faucet import chainstack as _chainstack
from faucet import compound as _compound
from faucet.nonce import NonceManager
from faucet.rpc import SEPOLIA_RPC_URL
from faucet.sweep import sweep

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
logging.getLogger("nodriver").setLevel(logging.WARNING)
logging.getLogger("uc").setLevel(logging.WARNING)

address = os.environ.get("TESTNET_ADDRESS", "")
if not address:
    sys.exit("TESTNET_ADDRESS is not set")

private_key = os.environ.get("TESTNET_PRIVATE_KEY", "")
sweep_to = os.environ.get("SWEEP_TO_ADDRESS", "")
# Babylon signet BTC address (taproot/P2TR); BTC drip is skipped when unset.
babylon_btc_address = os.environ.get("BABYLON_BTC_ADDRESS", "")

_ALL_NATIVE_CHAINS: list[str] = sorted(
    set(CHAINS.keys()) | (_chainstack.CHAINS - set(CHAINS.keys()))
)

# Circle USDC faucet only supports EVM chains (no solana-devnet etc.)
_USDC_EVM_CHAINS: list[str] = [c for c in USDC_CHAINS if not c.startswith("solana")]

_LINK_CHAINS: list[str] = list(LINK_CHAINS)

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


async def _drip_link_all() -> list[tuple[str, str | None, str | None]]:
    """Drip LINK across all chains in one browser: every faucet page shares the
    host faucets.chain.link, so the first chain's Cloudflare clearance carries to
    the rest — one Chrome, one captcha — instead of a fresh browser + captcha per
    chain. The redesigned faucet requires a claim signature, so the private key
    (matching ``address``) is required. Returns ``(chain, tx, err)`` rows."""
    res = await drip_link_all(address, _LINK_CHAINS, private_key=private_key)
    return [(chain, tx, err) for chain, (tx, err) in res.items()]


async def _drip_babylon() -> list[tuple[str, str | None, str | None]]:
    """Drip Babylon TBV assets one at a time (shared rate-limited faucet):
    EVM tokens to ``address``, then signet BTC if ``BABYLON_BTC_ADDRESS`` is set."""
    out: list[tuple[str, str | None, str | None]] = []
    for token in sorted(_babylon.EVM_TOKENS):
        try:
            out.append((token, await _babylon.drip(address, token), None))
        except Exception as exc:  # noqa: BLE001 — report and continue
            out.append((token, None, repr(exc)))
    if babylon_btc_address:
        try:
            out.append(("BTC", await _babylon.drip_btc(babylon_btc_address), None))
        except Exception as exc:  # noqa: BLE001 — report and continue
            out.append(("BTC", None, repr(exc)))
    return out


async def main() -> None:
    # Skip native drip in CI: runner IPs lose Cloudflare Turnstile.
    do_native = not os.environ.get("CI")
    do_usdc = True
    do_aave = bool(private_key)
    do_compound = True
    do_bb = not os.environ.get("CI")
    do_link = not os.environ.get("CI")
    parts = []
    if do_native:
        parts.append(f"{len(_ALL_NATIVE_CHAINS)} native chains")
    if do_usdc:
        parts.append(f"{len(_USDC_EVM_CHAINS)} USDC chains")
    if do_link:
        parts.append(f"{len(_LINK_CHAINS)} LINK chains")
    if do_aave:
        parts.append(f"{len(_aave.TOKENS)} Aave tokens")
    if do_compound:
        parts.append(f"{len(_compound.TOKENS)} Compound tokens")
    if do_bb:
        bb_count = len(_babylon.EVM_TOKENS) + (1 if babylon_btc_address else 0)
        parts.append(f"{bb_count} Babylon TBV assets")
    print(f"Funding {address} on {', '.join(parts)}\n")

    gather_fns: dict[str, Any] = {}
    if do_native:
        gather_fns["native"] = asyncio.gather(
            *[_drip_native(c) for c in _ALL_NATIVE_CHAINS]
        )
    if do_usdc:
        gather_fns["usdc"] = asyncio.gather(
            *[_drip_usdc_chain(c) for c in _USDC_EVM_CHAINS]
        )
    if do_link:
        gather_fns["link"] = _drip_link_all()
    # Aave and Compound both mint on Ethereum Sepolia from the same key; share
    # one nonce manager so their concurrent transactions never reuse a nonce.
    sepolia_nonces: NonceManager | None = None
    if do_aave or do_compound:
        sepolia_w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(SEPOLIA_RPC_URL))
        sepolia_nonces = NonceManager(sepolia_w3, address)
    if do_aave:
        gather_fns["aave"] = _aave.drip_all(
            address, private_key, nonce_manager=sepolia_nonces
        )
    if do_compound:
        gather_fns["compound"] = _compound.drip_all(
            address, private_key, nonce_manager=sepolia_nonces
        )
    if do_bb:
        gather_fns["babylon"] = _drip_babylon()

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

    if do_link:
        print("\nLINK:")
        for chain, tx, err in results["link"]:
            if err:
                print(f"  {chain}: ERROR — {err}")
            else:
                print(f"  {chain}: tx={tx}")

    aave_result: dict = results.get("aave", {})
    if aave_result:
        print("\nAave (Ethereum Sepolia):")
        for token, (tx_hash, err) in aave_result.items():
            if err:
                print(f"  {token}: ERROR — {err}")
            else:
                print(f"  {token}: tx={tx_hash}")

    compound_result: dict = results.get("compound", {})
    if compound_result:
        print("\nCompound III (Ethereum Sepolia):")
        for token, (tx_hash, err) in compound_result.items():
            if err:
                print(f"  {token}: ERROR — {err}")
            else:
                print(f"  {token}: tx={tx_hash}")

    babylon_result: list = results.get("babylon", [])
    if babylon_result:
        print("\nBabylon TBV (Sepolia + signet):")
        for token, tx, err in babylon_result:
            if err:
                print(f"  {token}: ERROR — {err}")
            else:
                print(f"  {token}: tx={tx}")


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
            print(
                f"  {r.chain}: {r.value / 10**r.decimals:.6f} {r.token}  tx={r.tx_hash}"
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
