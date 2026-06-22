"""
Chainlink testnet faucet via nodriver browser automation.

https://faucets.chain.link is a wallet-connect picker (no paste-address or POST
API), so we inject an EIP-1193 provider (announced over EIP-6963 as "Rabby
Wallet") bound to the target address, with reads proxied to a public RPC.
Claiming needs a signature, so a private key is required; it signs in Python
(:func:`_sign_pump`) and never enters the page.

Flow: connect → select the LINK card → Continue → solve Turnstile → Get tokens →
sign → poll result. SPA selectors are best-effort; needs a real Chrome.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass

from faucet.alchemy import (
    FaucetError,
    RateLimitError,
    _ANTI_FINGERPRINT,
    _wait_for_turnstile,
)
from faucet.rpc import public_rpc

# ---------------------------------------------------------------------------
# Chain registry — the testnets served by https://faucets.chain.link
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Network:
    """A faucet network: page, chain id, LINK token, and the keyless RPC (from
    :func:`faucet.rpc.public_rpc`, never an Infura URL since it is injected into
    the faucet page) backing the injected wallet's reads."""

    page_url: str
    chain_id: int
    link_token: str
    rpc_url: str

    @property
    def chain_hex(self) -> str:
        return hex(self.chain_id)


#: Base URL for all Chainlink faucet pages.
_CHAINLINK_HOST = "https://faucets.chain.link"

#: Chain slug → faucet network. Slugs mirror Alchemy's (``"ethereum-sepolia"``)
#: even when the faucet page path differs (``/sepolia``).
CHAINS: dict[str, _Network] = {
    "ethereum-sepolia": _Network(
        _CHAINLINK_HOST + "/sepolia",
        11155111,
        "0x779877A7B0D9E8603169DdbD7836e478b4624789",
        public_rpc("ethereum-sepolia"),
    ),
    "arbitrum-sepolia": _Network(
        _CHAINLINK_HOST + "/arbitrum-sepolia",
        421614,
        "0xb1D4538B4571d411F07960EF2838Ce337FE1E80E",
        public_rpc("arbitrum-sepolia"),
    ),
    "base-sepolia": _Network(
        _CHAINLINK_HOST + "/base-sepolia",
        84532,
        "0xE4aB69C077896252FAFBD49EFD26B5D171A32410",
        public_rpc("base-sepolia"),
    ),
    "optimism-sepolia": _Network(
        _CHAINLINK_HOST + "/optimism-sepolia",
        11155420,
        "0xE4aB69C077896252FAFBD49EFD26B5D171A32410",
        public_rpc("optimism-sepolia"),
    ),
    "avalanche-fuji": _Network(
        _CHAINLINK_HOST + "/fuji",
        43113,
        "0x0b9d5D9136855f6FEc3c0993feE6E9CE8a297846",
        public_rpc("avalanche-fuji"),
    ),
    "polygon-amoy": _Network(
        _CHAINLINK_HOST + "/polygon-amoy",
        80002,
        "0x0Fd9e8d3aF1aaee056EB9e802c3A762a667b1904",
        public_rpc("polygon-amoy"),
    ),
}

#: Convenience mapping chain slug → canonical LINK token address.
LINK_CONTRACTS: dict[str, str] = {slug: net.link_token for slug, net in CHAINS.items()}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def drip(
    address: str,
    chains: list[str] | None = None,
    *,
    private_key: str | None = None,
    rpc_url: str | None = None,
    headless: bool = False,
    timeout: float = 60.0,
    gap_seconds: float = 2.0,
) -> dict[str, tuple[str | None, str | None]]:
    """Fund *address* with LINK on *chains* (default: all of :data:`CHAINS`).

    Runs chains sequentially in one reused Chrome (they share host
    ``faucets.chain.link``, so the first Turnstile clearance carries over),
    spaced by *gap_seconds*. *private_key* (matching *address*) is required to
    sign the claim. Returns ``{chain: (status, error)}`` with one side ``None``.
    Raises ValueError (unknown chain) or FaucetError (nodriver missing).
    """
    targets = list(chains) if chains is not None else list(CHAINS)
    unknown = [c for c in targets if c not in CHAINS]
    if unknown:
        raise ValueError(
            f"Unknown chain(s) {', '.join(unknown)}. Supported: {', '.join(CHAINS)}"
        )
    try:
        import nodriver as uc
    except ImportError as exc:
        raise FaucetError("nodriver is required: pip install nodriver") from exc

    # A flaky Chrome launch must not abort the caller's other faucets — report
    # the failure per chain instead of raising out of the whole drip.
    try:
        browser = await uc.start(headless=headless, sandbox=not os.environ.get("CI"))
    except Exception as exc:  # noqa: BLE001 — surface as a per-chain error
        return {chain: (None, f"browser launch failed: {exc!r}") for chain in targets}

    results: dict[str, tuple[str | None, str | None]] = {}
    try:
        for i, chain in enumerate(targets):
            if i:
                await asyncio.sleep(gap_seconds)
            net = CHAINS[chain]
            try:
                tx = await _drip_via_browser(
                    net,
                    address,
                    slug=chain,
                    private_key=private_key,
                    rpc_url=rpc_url or net.rpc_url,
                    headless=headless,
                    timeout=timeout,
                    browser=browser,
                )
                results[chain] = (tx, None)
            except Exception as exc:  # noqa: BLE001 — report and move to next chain
                results[chain] = (None, repr(exc))
    finally:
        browser.stop()
    return results


# ---------------------------------------------------------------------------
# Injected EIP-1193 wallet shim
# ---------------------------------------------------------------------------


def _wallet_shim_source(
    address: str, chain_hex: str, rpc_url: str, *, can_sign: bool = False
) -> str:
    """JS injected on new document: an EIP-1193 provider for *address* (announced
    via EIP-6963 as Rabby Wallet), reads proxied to *rpc_url*. With *can_sign*,
    signing is bridged to Python (:func:`_sign_pump`); otherwise rejected."""
    # Substituted (not f-string) to keep the JS braces readable.
    bridge = (
        """
  window.__signQueue = window.__signQueue || {};
  window.__signResults = window.__signResults || {};
  window.__signSeq = window.__signSeq || 0;
  async function __bridgeSign(method, params) {
    const id = String(++window.__signSeq);
    window.__signQueue[id] = {method: method, params: params};
    return await new Promise((resolve, reject) => {
      const t0 = Date.now();
      const iv = setInterval(() => {
        const r = window.__signResults[id];
        if (r !== undefined) {
          clearInterval(iv);
          delete window.__signResults[id];
          delete window.__signQueue[id];
          if (r && r.error) reject(Object.assign(new Error(r.error), {code: 4001}));
          else resolve(r.result);
        } else if (Date.now() - t0 > 90000) {
          clearInterval(iv); delete window.__signQueue[id];
          reject(Object.assign(new Error("signature timeout"), {code: 4001}));
        }
      }, 80);
    });
  }
"""
        if can_sign
        else ""
    )
    if can_sign:
        sign_case = (
            'case "personal_sign":\n'
            '        case "eth_sign":\n'
            '        case "eth_signTypedData":\n'
            '        case "eth_signTypedData_v1":\n'
            '        case "eth_signTypedData_v3":\n'
            '        case "eth_signTypedData_v4":\n'
            "          return await __bridgeSign(method, params);\n"
            '        case "eth_sendTransaction":\n'
            '          throw Object.assign(new Error("sendTransaction not supported by faucet wallet shim"), {code: 4200});'
        )
    else:
        sign_case = (
            'case "personal_sign":\n'
            '        case "eth_sign":\n'
            '        case "eth_signTypedData":\n'
            '        case "eth_signTypedData_v3":\n'
            '        case "eth_signTypedData_v4":\n'
            '        case "eth_sendTransaction":\n'
            '          throw Object.assign(new Error("signing not supported by faucet wallet shim"), {code: 4200});'
        )
    return (
        """
(() => {
  const ADDR = "__ADDR__";
  const CHAIN_HEX = "__CHAIN__";
  const RPC = "__RPC__";
  let _id = 0;
  __SIGN_BRIDGE__
  async function passthrough(method, params) {
    const res = await fetch(RPC, {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({jsonrpc: "2.0", id: ++_id, method, params: params || []}),
    });
    const j = await res.json();
    if (j.error) throw Object.assign(new Error(j.error.message), {code: j.error.code});
    return j.result;
  }
  const listeners = {};
  const emit = (ev, data) => (listeners[ev] || []).forEach((cb) => { try { cb(data); } catch (e) {} });
  const provider = {
    isRabby: true,
    isMetaMask: true,
    _metamask: {isUnlocked: () => Promise.resolve(true)},
    chainId: CHAIN_HEX,
    selectedAddress: ADDR,
    networkVersion: String(parseInt(CHAIN_HEX, 16)),
    isConnected: () => true,
    request: async ({method, params}) => {
      switch (method) {
        case "eth_requestAccounts":
        case "eth_accounts":
          return [ADDR];
        case "eth_chainId":
          return CHAIN_HEX;
        case "net_version":
          return String(parseInt(CHAIN_HEX, 16));
        case "wallet_switchEthereumChain":
        case "wallet_addEthereumChain":
        case "wallet_registerOnboarding":
          return null;
        case "wallet_requestPermissions":
        case "wallet_getPermissions":
          return [{parentCapability: "eth_accounts"}];
        __SIGN_CASE__
        case "eth_subscribe":
        case "eth_unsubscribe":
          throw Object.assign(new Error("subscriptions not supported"), {code: 4200});
        default:
          return passthrough(method, params);
      }
    },
    on: (ev, cb) => { (listeners[ev] = listeners[ev] || []).push(cb); return provider; },
    removeListener: (ev, cb) => { listeners[ev] = (listeners[ev] || []).filter((f) => f !== cb); return provider; },
    enable: async () => [ADDR],
  };
  try {
    Object.defineProperty(window, "ethereum", {value: provider, configurable: true, writable: false});
  } catch (e) { window.ethereum = provider; }
  try { provider.providers = [provider]; } catch (e) {}
  // EIP-6963 multi-wallet discovery (web3modal/AppKit/wagmi).
  const info = {
    uuid: "00000000-0000-0000-0000-0000000000bb",
    name: "Rabby Wallet",
    icon: "data:image/svg+xml;base64,PHN2Zy8+",
    rdns: "io.rabby",
  };
  const detail = Object.freeze({info, provider});
  const announce = () =>
    window.dispatchEvent(new CustomEvent("eip6963:announceProvider", {detail}));
  window.addEventListener("eip6963:requestProvider", announce);
  // Re-announce for ~10s: a single document-start announce fires before AppKit's
  // listener attaches and is lost ("Could not detect io.rabby provider").
  announce();
  let _n = 0;
  const _iv = setInterval(() => { announce(); if (++_n > 40) clearInterval(_iv); }, 250);
  document.addEventListener("DOMContentLoaded", announce);
  window.addEventListener("load", announce);
  setTimeout(() => emit("connect", {chainId: CHAIN_HEX}), 0);
})();
""".replace("__SIGN_BRIDGE__", bridge)
        .replace("__SIGN_CASE__", sign_case)
        .replace("__ADDR__", address)
        .replace("__CHAIN__", chain_hex)
        .replace("__RPC__", rpc_url)
    )


# ---------------------------------------------------------------------------
# Browser automation
# ---------------------------------------------------------------------------

_SUCCESS_HINTS = (
    "on its way",
    "on the way",
    "is being processed",
    "request received",
    "tokens sent",
    "tokens are on",
    "successfully",
    "transaction submitted",
    "view on",
)
_RATE_LIMIT_HINTS = (
    "exceeded",
    "already",
    "try again",
    "24 hours",
    "rate limit",
    "limit reached",
)
# Narrow on purpose: the "require ≥1 LINK on mainnet" note and a failed ETH row
# must not read as a LINK failure. Success is checked before error.
_ERROR_HINTS = ("something went wrong", "failed")


class _Trace:
    """Phase stopwatch; logs ``[trace] <phase> +Xs`` to stderr when
    ``CHAINLINK_TRACE`` is set. No-op otherwise."""

    def __init__(self, label: str) -> None:
        self.on = bool(os.environ.get("CHAINLINK_TRACE"))
        self.label = label
        self.t = asyncio.get_event_loop().time() if self.on else 0.0

    def mark(self, phase: str) -> None:
        if not self.on:
            return
        now = asyncio.get_event_loop().time()
        print(
            f"[trace] {self.label} {phase} +{now - self.t:.1f}s",
            file=sys.stderr,
            flush=True,
        )
        self.t = now


# ---------------------------------------------------------------------------
# Claim-signature bridge — the shim queues requests on window.__signQueue; the
# pump signs them in Python and posts to window.__signResults for the shim's
# Promise to resolve.
# ---------------------------------------------------------------------------


def _sign_request(account, method: str, params: list) -> str:
    """Sign a faucet ``personal_sign`` / ``eth_signTypedData*`` request with
    *account*, returning a 0x signature. The address and payload arrive in either
    order, so we pick the non-address param."""
    from eth_account.messages import encode_defunct, encode_typed_data

    m = (method or "").lower()
    params = params or []
    addr = account.address.lower()

    def is_addr(x) -> bool:
        return isinstance(x, str) and x.lower() == addr

    payload = next((p for p in params if not is_addr(p)), params[0] if params else "")

    if m in ("personal_sign", "eth_sign"):
        if isinstance(payload, str) and payload.startswith("0x") and _is_hex(payload):
            signable = encode_defunct(hexstr=payload)
        else:
            signable = encode_defunct(
                text=payload if isinstance(payload, str) else str(payload)
            )
    elif m.startswith("eth_signtypeddata"):
        td = json.loads(payload) if isinstance(payload, str) else payload
        signable = encode_typed_data(full_message=td)
    else:
        raise ValueError(f"unsupported sign method {method!r}")

    signed = account.sign_message(signable)
    return "0x" + bytes(signed.signature).hex()


def _is_hex(s: str) -> bool:
    body = s[2:] if s.startswith("0x") else s
    if len(body) % 2:
        return False
    try:
        int(body or "0", 16)
        return True
    except ValueError:
        return False


async def _safe_eval(page, js: str):
    try:
        return await page.evaluate(js, return_by_value=True)
    except Exception:
        return None


async def _sign_pump(page, account) -> None:
    """Poll the page's sign queue; sign each request and post the result back.
    Runs as a background task for the duration of a drip; cancelled when done."""
    trace = bool(os.environ.get("CHAINLINK_TRACE"))
    seen: set[str] = set()
    while True:
        await asyncio.sleep(0.15)
        raw = await _safe_eval(page, "JSON.stringify(window.__signQueue||{})")
        if not isinstance(raw, str) or raw in ("{}", ""):
            continue
        try:
            queue = json.loads(raw)
        except ValueError:
            continue
        for sid, req in queue.items():
            if sid in seen:
                continue
            seen.add(sid)
            method = (req or {}).get("method", "")
            params = (req or {}).get("params") or []
            if trace:
                print(
                    f"[trace] sign request {method} params={json.dumps(params)[:240]}",
                    file=sys.stderr,
                    flush=True,
                )
            try:
                result = _sign_request(account, method, params)
                payload = json.dumps({"result": result})
            except Exception as exc:  # noqa: BLE001 — surface as a rejected sign
                payload = json.dumps({"error": str(exc)[:140]})
                if trace:
                    print(f"[trace] sign FAILED: {exc!r}", file=sys.stderr, flush=True)
            await _safe_eval(
                page,
                "(()=>{window.__signResults=window.__signResults||{};"
                f"window.__signResults[{json.dumps(sid)}]={payload};return 1;}})()",
            )


async def _drip_via_browser(
    net: _Network,
    address: str,
    *,
    slug: str,
    private_key: str | None = None,
    rpc_url: str,
    headless: bool,
    timeout: float,
    browser=None,
) -> str | None:
    try:
        import nodriver as uc
    except ImportError as exc:
        raise FaucetError("nodriver is required: pip install nodriver") from exc

    account = None
    if private_key:
        from eth_account import Account

        account = Account.from_key(private_key)
        if account.address.lower() != address.lower():
            raise FaucetError(
                "Chainlink: private_key does not match the address being funded "
                f"({account.address} != {address})"
            )

    # Shared browser: work in a throwaway tab so the Cloudflare clearance carries
    # over; own the lifecycle only if we started it.
    own_browser = browser is None
    if own_browser:
        browser = await uc.start(headless=headless, sandbox=not os.environ.get("CI"))
    page = None
    pump = None
    try:
        page = await browser.get("about:blank", new_tab=not own_browser)
        shim = _wallet_shim_source(
            address, net.chain_hex, rpc_url, can_sign=account is not None
        )
        # Inject the wallet shim + anti-fingerprint script before any page JS runs.
        await page.send(
            uc.cdp.page.add_script_to_evaluate_on_new_document(source=_ANTI_FINGERPRINT)
        )
        await page.send(uc.cdp.page.add_script_to_evaluate_on_new_document(source=shim))

        _t = _Trace(net.page_url)
        await page.get(net.page_url)
        await asyncio.sleep(0.5)  # tiny settle; the connect poll absorbs slow hydration
        _t.mark("load")

        # The on-new-document hook can miss on some SPA navigations — re-install
        # if the provider isn't live.
        await _ensure_wallet_shim(page, shim)
        # Service the wallet's signature requests once the provider is live.
        if account is not None:
            pump = asyncio.create_task(_sign_pump(page, account))
        await _connect_wallet(page)
        _t.mark("connect")
        # Select this chain's LINK card and submit; the sign pump answers the
        # claim's signature request.
        await _select_and_submit(page, slug)
        _t.mark("submit")
        await _wait_for_turnstile(page, timeout=min(timeout, 45.0))
        _t.mark("turnstile")
        result = await _await_result(page)
        _t.mark("result")
        return result
    finally:
        if pump is not None:
            pump.cancel()
            try:
                await pump
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if own_browser:
            browser.stop()
        elif page is not None:
            try:
                await page.close()
            except Exception:
                pass


async def _connect_wallet(page) -> None:
    """Open the connect modal, accept the ToS gate, and pick the injected wallet."""
    loop = asyncio.get_event_loop()
    # Click Connect as soon as it renders (the faucet hydrates slowly).
    if not await _poll_click(
        page, ("Connect wallet", "Connect Wallet", "Connect"), timeout=25
    ):
        raise FaucetError("Chainlink: connect-wallet button not found")
    # Wait for the modal, then accept ToS (wallet buttons are disabled until then).
    await _wait_present(page, ("I accept", "Terms of Service", "Rabby"), timeout=12)
    await _accept_tos(page)
    # Pick the injected wallet, re-announcing each round (AppKit's listener
    # attaches late).
    deadline = loop.time() + 20
    while True:
        # Our shim is "Rabby Wallet"; web3modal may label it "Browser Wallet"/"Injected".
        if await _click_first_text(
            page, ("Rabby Wallet", "Rabby", "Browser Wallet", "Injected")
        ):
            return
        if loop.time() > deadline:
            raise FaucetError("Chainlink: wallet option not found in connect modal")
        # Not there yet — re-announce for AppKit's late listener and back off.
        await _announce_wallet(page)
        await asyncio.sleep(0.8)


async def _accept_tos(page) -> bool:
    """Tick the ToS checkbox so the wallet buttons enable — clicks the real input
    (for React's onChange), falling back to the "I accept" label. No-op if checked."""
    checked_js = "!!document.querySelector(\"input[type='checkbox']\")?.checked"
    try:
        if await page.evaluate(checked_js):
            return True
    except Exception:
        pass
    try:
        box = await page.select("input[type=checkbox]")
        if box is not None:
            await box.click()
    except Exception:
        pass
    try:
        if not await page.evaluate(checked_js):
            await _click_first_text(
                page, ("I accept the Chainlink Foundation", "I accept")
            )
        return bool(await page.evaluate(checked_js))
    except Exception:
        return False


async def _select_and_submit(page, slug: str) -> None:
    """Drive the multi-asset picker: Clear all (drop the faucet's pre-selected
    assets to drip exactly LINK), select this chain's LINK card (test id
    ``faucet_card_<slug>_link``), Continue, solve the drawer Turnstile, Get tokens."""
    # Clear any default selection (best-effort; absent when nothing is selected).
    await _click_text(page, "clear all")
    await asyncio.sleep(0.4)
    if not await _select_link_card(page, slug):
        raise FaucetError(f"Chainlink: LINK faucet card for {slug!r} not selectable")
    if not await _poll_click_text(page, "continue", timeout=15):
        raise FaucetError("Chainlink: 'Continue' button not found")
    # The claim drawer opens with a Turnstile gating the dispense — solve it first.
    await _wait_for_turnstile(page, timeout=30.0)
    if not await _poll_click_text(page, "get tokens", timeout=20):
        raise FaucetError("Chainlink: 'Get tokens' button not found")


# Click the first enabled button/role=button whose text contains *want* (lowercased).
_CLICK_TEXT_JS = (
    "(() => { const w = __W__;"
    " const b = [...document.querySelectorAll('button, [role=button]')].find("
    "  (x) => !(x.disabled || x.getAttribute('aria-disabled') === 'true')"
    "    && (x.innerText || '').trim().toLowerCase().includes(w));"
    " if (!b) return false; b.click(); return true; })()"
)


async def _click_text(page, want: str) -> bool:
    try:
        return bool(
            await page.evaluate(
                _CLICK_TEXT_JS.replace("__W__", json.dumps(want)), return_by_value=True
            )
        )
    except Exception:
        return False


async def _poll_click_text(page, want: str, *, timeout: float) -> bool:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        if await _click_text(page, want):
            return True
        if loop.time() > deadline:
            return False
        await asyncio.sleep(0.6)


async def _select_link_card(page, slug: str) -> bool:
    """Ensure the LINK faucet card for *slug* is selected (Clear all dropped the
    default). Polls until its badge reads selected, clicking it if not."""
    sel = (
        "(() => { const c = document.querySelector("
        f"'button[data-testid=\"faucet_card_{slug}_link\"]');"
        " if (!c) return 'NO_CARD';"
        " return ((c.className||'').includes('selected')"
        " || !!c.querySelector('[data-testid=\"badge_selected\"]')) ? 'SEL' : 'UNSEL'; })()"
    )
    click = (
        "(() => { const c = document.querySelector("
        f"'button[data-testid=\"faucet_card_{slug}_link\"]');"
        " if (!c) return false; c.click(); return true; })()"
    )
    loop = asyncio.get_event_loop()
    deadline = loop.time() + 15
    while loop.time() < deadline:
        state = await _safe_eval(page, sel)
        if state == "SEL":
            return True
        if state == "UNSEL":
            await _safe_eval(page, click)
        await asyncio.sleep(0.5)
    return False


async def _await_result(page, *, timeout: float = 60.0) -> str | None:
    """Poll the page text for a success / rate-limit / error signal (returns as
    soon as one appears; the ceiling only bites when none does)."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(1)
        text = (await _body_text(page)).lower()
        if any(h in text for h in _RATE_LIMIT_HINTS):
            raise RateLimitError(f"Chainlink faucet rate-limited: {_snippet(text)}")
        if any(h in text for h in _SUCCESS_HINTS):
            return await _explorer_link(page) or "request submitted"
        if any(h in text for h in _ERROR_HINTS):
            raise FaucetError(f"Chainlink faucet error: {_snippet(text)}")
    return None  # submitted, no recognisable confirmation text


# ---------------------------------------------------------------------------
# Small DOM helpers
# ---------------------------------------------------------------------------

_SHIM_PRESENT = "!!(window.ethereum && window.ethereum.isRabby)"


async def _ensure_wallet_shim(page, shim: str) -> None:
    """Verify the injected provider is live, re-installing the shim if the
    on-new-document hook missed; raise if still absent."""
    try:
        if await page.evaluate(_SHIM_PRESENT):
            return
    except Exception:
        pass
    try:
        await page.evaluate(shim)
    except Exception as exc:
        raise FaucetError(f"Chainlink: wallet shim failed to install: {exc}") from exc
    try:
        present = bool(await page.evaluate(_SHIM_PRESENT))
    except Exception:
        present = False
    if not present:
        raise FaucetError("Chainlink: injected wallet not present after install")


async def _announce_wallet(page) -> None:
    """Dispatch an EIP-6963 ``requestProvider`` so the shim re-announces and
    AppKit's late listener picks up the ``io.rabby`` provider."""
    try:
        await page.evaluate(
            "window.dispatchEvent(new CustomEvent('eip6963:requestProvider'))"
        )
    except Exception:
        pass


async def _click_first_text(page, candidates: tuple[str, ...]) -> bool:
    """Click the first element whose text matches any of *candidates*. Returns
    whether something was clicked."""
    for text in candidates:
        try:
            elem = await page.find(text, best_match=True)
        except Exception:
            elem = None
        if elem is not None:
            try:
                await elem.click()
                return True
            except Exception:
                continue
    return False


async def _poll_click(page, candidates: tuple[str, ...], *, timeout: float) -> bool:
    """Click the first matching candidate as soon as it renders, polling up to
    *timeout*. Adaptive: returns immediately on a fast load, waits out a slow one."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        if await _click_first_text(page, candidates):
            return True
        if loop.time() > deadline:
            return False
        await asyncio.sleep(1)


async def _wait_present(page, candidates: tuple[str, ...], *, timeout: float) -> bool:
    """Return once any *candidate* text is on the page (without clicking), polling
    up to *timeout*."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        for text in candidates:
            try:
                if await page.find(text, best_match=True) is not None:
                    return True
            except Exception:
                pass
        if loop.time() > deadline:
            return False
        await asyncio.sleep(0.7)


async def _body_text(page) -> str:
    try:
        return await page.evaluate("document.body?.innerText || ''") or ""
    except Exception:
        return ""


async def _explorer_link(page) -> str | None:
    try:
        return (
            await page.evaluate(
                "(document.querySelector(\"a[href*='etherscan'], a[href*='explorer'], a[href*='scan']\")?.href) || ''"
            )
            or None
        )
    except Exception:
        return None


def _snippet(text: str, limit: int = 160) -> str:
    flat = " ".join(text.split())
    return flat[:limit]
