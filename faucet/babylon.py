"""Babylon Trustless Bitcoin Vaults (TBV) testnet faucet.

Funds the TBV × Aave v4 testnet: Sepolia gas ETH and test ERC-20s
(``ETH``/``USDC``/``USDT``/``WBTC``) via :func:`drip`, and signet BTC via
:func:`drip_btc`::

    asyncio.run(babylon.drip("0xYourEvmAddress", "USDC"))   # Sepolia token
    asyncio.run(babylon.drip_btc("tb1p...yoursignetaddr"))  # signet BTC

The faucet is Turnstile-gated, so we drive its page with nodriver. It is
geo-restricted and rate-limited (2 drips/day per IP): run it from a permitted
network, not in CI.
"""

from __future__ import annotations

import asyncio
import json
import os

from faucet.alchemy import FaucetError, InsufficientFaucetBalanceError, RateLimitError

_PAGE_URL = "https://tbv-faucet.testnet.babylonlabs.io/"
_POLL_INTERVAL = 1.0

#: EVM test assets dispensed by ``/api/drip`` (gas + borrowable/collateral ERC-20s).
EVM_TOKENS: set[str] = {"ETH", "USDC", "USDT", "WBTC"}

#: Default per-drip amounts (human units), aligned with the faucet UI defaults.
_DEFAULT_EVM_AMOUNT: dict[str, float] = {
    "ETH": 0.05,
    "USDC": 100,
    "USDT": 100,
    "WBTC": 0.1,
}
_DEFAULT_BTC_AMOUNT: float = 0.001


async def drip(
    recipient: str,
    token: str = "ETH",
    *,
    amount: float | None = None,
    headless: bool = False,
    timeout: float = 90.0,
) -> str | None:
    """Drip an EVM asset (gas ETH or a test ERC-20 in :data:`EVM_TOKENS`) to
    *recipient* on Sepolia; *amount* defaults per token. Returns the tx hash."""
    token = token.upper()
    if token not in EVM_TOKENS:
        raise ValueError(
            f"Babylon: unknown token {token!r}. Supported: {', '.join(sorted(EVM_TOKENS))}"
        )
    amt = amount if amount is not None else _DEFAULT_EVM_AMOUNT[token]
    return await _drip(recipient, token, amt, headless=headless, timeout=timeout)


async def drip_btc(
    recipient: str,
    *,
    amount: float | None = None,
    headless: bool = False,
    timeout: float = 90.0,
) -> str | None:
    """Drip signet BTC to *recipient* (a signet taproot/P2TR address). Returns
    the signet txid."""
    amt = amount if amount is not None else _DEFAULT_BTC_AMOUNT
    return await _drip(recipient, None, amt, headless=headless, timeout=timeout)


# ---------------------------------------------------------------------------
# nodriver SPA path — fill the real form, click submit, capture the API result
# ---------------------------------------------------------------------------

# Capture the /api/drip(-btc) response up front so parsing does not depend on
# the page's own success/error UI.
_INSTALL_INTERCEPTOR = """
() => {
    if (window.__faucetHooked) return;
    window.__faucetHooked = true;
    window.__faucetResult = null;
    const orig = window.fetch;
    window.fetch = async (...args) => {
        const url = (args[0] && args[0].url) || String(args[0]);
        const resp = await orig(...args);
        if (url.includes("/api/drip")) {
            const body = await resp.clone().text().catch(() => "");
            window.__faucetResult = JSON.stringify({httpStatus: resp.status, body});
        }
        return resp;
    };
}
"""

# Set a React-controlled input's value so the framework registers the change.
_SET_INPUT = """
(selector, value) => {
    const el = document.querySelector(selector);
    if (!el) return false;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(el, value);
    el.dispatchEvent(new Event("input", {bubbles: true}));
    el.dispatchEvent(new Event("change", {bubbles: true}));
    return true;
}
"""

# Click the first visible element whose trimmed text exactly equals *label*.
_CLICK_TEXT = """
(label) => {
    for (const el of document.querySelectorAll("button, [role='tab'], div, span")) {
        if ((el.textContent || "").trim() === label && el.offsetParent !== null) {
            el.click();
            return true;
        }
    }
    return false;
}
"""

# Click the submit button (label "Drip <amount> <symbol>" once valid); returns
# "clicked", or "no-submit"/"disabled" while the form/captcha is not ready.
_CLICK_SUBMIT = """
() => {
    const btn = [...document.querySelectorAll("button")]
        .find(b => (b.textContent || "").trim().startsWith("Drip "));
    if (!btn) return "no-submit";
    if (btn.disabled || btn.className.includes("cursor-not-allowed")) return "disabled";
    btn.click();
    return "clicked";
}
"""


async def _call_js(page, fn: str, *args):
    """Invoke a JS arrow-function source *fn* with JSON-encoded *args*."""
    arglist = ", ".join(json.dumps(a) for a in args)
    return await page.evaluate(f"({fn})({arglist})")


async def _poll(action, ok, *, timeout: float, on_timeout):
    """Call async *action* every :data:`_POLL_INTERVAL` until ``ok(result)``;
    raise :class:`FaucetError` (``on_timeout(result)``) past *timeout*."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        result = await action()
        if ok(result):
            return result
        if asyncio.get_event_loop().time() > deadline:
            raise FaucetError(on_timeout(result))
        await asyncio.sleep(_POLL_INTERVAL)


async def _drip(
    recipient: str,
    token: str | None,
    amount: float,
    *,
    headless: bool,
    timeout: float,
) -> str | None:
    """Drive the faucet SPA (``token=None`` → signet BTC) and return the txid."""
    try:
        import nodriver as uc
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise FaucetError("nodriver is required: pip install nodriver") from exc

    browser = await uc.start(headless=headless, sandbox=not os.environ.get("CI"))
    try:
        page = await browser.get(_PAGE_URL)
        await asyncio.sleep(6)  # React hydration
        await _call_js(page, _INSTALL_INTERCEPTOR)

        await _call_js(page, _CLICK_TEXT, "BTC Signet" if token is None else "Sepolia")
        await asyncio.sleep(1)

        addr_selector = (
            "input[placeholder^='tb1p']"
            if token is None
            else "input[placeholder^='0x']"
        )
        if not await _call_js(page, _SET_INPUT, addr_selector, recipient):
            raise FaucetError(
                "Babylon: recipient input not found (page layout may have changed)"
            )

        if token is not None:
            await _call_js(page, _CLICK_TEXT, token)
            await asyncio.sleep(0.5)
        await _call_js(page, _SET_INPUT, 'input[placeholder="0.00"]', str(amount))
        await asyncio.sleep(1)

        # Click submit once it enables (Turnstile may arm a little late).
        await _poll(
            lambda: _call_js(page, _CLICK_SUBMIT),
            lambda state: state == "clicked",
            timeout=timeout,
            on_timeout=lambda state: (
                f"Babylon: submit stayed {state!r} for {timeout:.0f}s (captcha/rate limit?)"
            ),
        )
        raw = await _poll(
            lambda: page.evaluate("window.__faucetResult"),
            bool,
            timeout=timeout,
            on_timeout=lambda _: (
                f"Babylon: no faucet API response within {timeout:.0f}s"
            ),
        )
        return _parse_response(raw)
    finally:
        browser.stop()


def _parse_response(raw: str) -> str | None:
    """Parse an intercepted ``{httpStatus, body}`` result into a txid, mapping
    faucet errors to the shared exception types."""
    outer = json.loads(raw)
    status, body = outer.get("httpStatus"), outer.get("body") or ""
    try:
        data = json.loads(body)
    except ValueError:
        raise FaucetError(
            f"Babylon: non-JSON faucet response ({status}): {body[:200]!r}"
        )

    error = data.get("error") if isinstance(data, dict) else None
    if error:
        lower = str(error).lower()
        if status == 429 or any(w in lower for w in ("rate", "limit", "24h")):
            raise RateLimitError(str(error))
        if any(w in lower for w in ("insufficient", "balance", "empty")):
            raise InsufficientFaucetBalanceError(str(error))
        raise FaucetError(f"Babylon faucet error ({status}): {error}")

    return data.get("txid") or data.get("txHash") or data.get("transactionHash")
