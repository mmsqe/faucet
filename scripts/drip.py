"""Drip native tokens and USDC on all supported testnet chains."""

import asyncio
import contextlib
import gc
import logging
import os
import sys

from web3 import AsyncWeb3

from faucet import (
    CHAINS,
    LINK_CHAINS,
    USDC_CHAINS,
    RateLimitError,
    drip,
    drip_link_all,
    drip_usdc,
)
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
    try:
        res = await drip_link_all(address, _LINK_CHAINS, private_key=private_key)
    except Exception as exc:  # noqa: BLE001 — keep the other faucets running
        return [(chain, None, repr(exc)) for chain in _LINK_CHAINS]
    return [(chain, tx, err) for chain, (tx, err) in res.items()]


async def _drip_babylon() -> list[tuple[str, str | None, str | None]]:
    """Drip Babylon TBV assets one at a time (shared rate-limited faucet):
    EVM tokens to ``address``, then signet BTC if ``BABYLON_BTC_ADDRESS`` is set.

    The faucet caps drips at 2/day/IP across the EVM tokens, so once one
    rate-limits the rest will too — skip them (each would otherwise cost a fresh
    browser launch + the full submit timeout) and go straight to the separate
    signet-BTC faucet."""
    out: list[tuple[str, str | None, str | None]] = []
    tokens = sorted(_babylon.EVM_TOKENS)
    for i, token in enumerate(tokens):
        try:
            out.append((token, await _babylon.drip(address, token), None))
        except RateLimitError as exc:
            out.append((token, None, repr(exc)))
            out.extend(
                (skipped, None, "skipped (Babylon daily rate limit reached)")
                for skipped in tokens[i + 1 :]
            )
            break
        except Exception as exc:  # noqa: BLE001 — report and continue
            out.append((token, None, repr(exc)))
    if babylon_btc_address:
        try:
            out.append(("BTC", await _babylon.drip_btc(babylon_btc_address), None))
        except Exception as exc:  # noqa: BLE001 — report and continue
            out.append(("BTC", None, repr(exc)))
    return out


async def _emit(coro, render) -> None:
    """Await *coro*, then print ``render(result)`` as a single block the moment
    it resolves — so a fast branch reports without waiting on the slow ones.
    One print per section keeps concurrent branches from interleaving mid-block.
    A falsy render result (empty branch) prints nothing."""
    block = render(await coro)
    if block:
        print(f"\n{block}")


def _render_native(rows) -> str:
    lines = ["Native tokens:"]
    for chain, tx, err in rows:
        lines.append(f"  {chain}: ERROR — {err}" if err else f"  {chain}: tx={tx}")
    return "\n".join(lines)


def _render_usdc(rows) -> str:
    lines = ["USDC:"]
    for chain, err in rows:
        lines.append(f"  {chain}: ERROR — {err}" if err else f"  {chain}: ok")
    return "\n".join(lines)


def _render_link(rows) -> str:
    lines = ["LINK:"]
    for chain, tx, err in rows:
        lines.append(f"  {chain}: ERROR — {err}" if err else f"  {chain}: tx={tx}")
    return "\n".join(lines)


def _render_tokens(title: str, result: dict) -> str:
    if not result:
        return ""
    lines = [f"{title}:"]
    for token, (tx_hash, err) in result.items():
        lines.append(f"  {token}: ERROR — {err}" if err else f"  {token}: tx={tx_hash}")
    return "\n".join(lines)


def _render_babylon(rows) -> str:
    if not rows:
        return ""
    lines = ["Babylon TBV (Sepolia + signet):"]
    for token, tx, err in rows:
        lines.append(f"  {token}: ERROR — {err}" if err else f"  {token}: tx={tx}")
    return "\n".join(lines)


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

    # Each branch prints its own section the moment it finishes (see _emit), so
    # fast branches (Aave/Compound, ~30s) report immediately instead of waiting
    # on the slow browser branches (native/LINK/Babylon, several minutes).
    tasks = []
    if do_native:
        tasks.append(
            _emit(
                asyncio.gather(*[_drip_native(c) for c in _ALL_NATIVE_CHAINS]),
                _render_native,
            )
        )
    if do_usdc:
        tasks.append(
            _emit(
                asyncio.gather(*[_drip_usdc_chain(c) for c in _USDC_EVM_CHAINS]),
                _render_usdc,
            )
        )
    if do_link:
        tasks.append(_emit(_drip_link_all(), _render_link))
    # Aave and Compound both mint on Ethereum Sepolia from the same key; share
    # one nonce manager so their concurrent transactions never reuse a nonce.
    sepolia_nonces: NonceManager | None = None
    if do_aave or do_compound:
        sepolia_w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(SEPOLIA_RPC_URL))
        sepolia_nonces = NonceManager(sepolia_w3, address)
    if do_aave:
        tasks.append(
            _emit(
                _aave.drip_all(address, private_key, nonce_manager=sepolia_nonces),
                lambda r: _render_tokens("Aave (Ethereum Sepolia)", r),
            )
        )
    if do_compound:
        tasks.append(
            _emit(
                _compound.drip_all(address, private_key, nonce_manager=sepolia_nonces),
                lambda r: _render_tokens("Compound III (Ethereum Sepolia)", r),
            )
        )
    if do_bb:
        tasks.append(_emit(_drip_babylon(), _render_babylon))

    await asyncio.gather(*tasks)


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
asyncio.set_event_loop(loop)
try:
    loop.run_until_complete(main())
    loop.run_until_complete(_sweep())
    gc.collect()
    loop.run_until_complete(asyncio.sleep(0.25))
finally:
    pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
    for task in pending:
        task.cancel()
    if pending:
        with contextlib.suppress(Exception):
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    with contextlib.suppress(Exception):
        loop.run_until_complete(loop.shutdown_asyncgens())
    with contextlib.suppress(Exception):
        loop.run_until_complete(loop.shutdown_default_executor())
    loop.close()
