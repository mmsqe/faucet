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

from faucet.alchemy import (
    FaucetError,
    InsufficientFaucetBalanceError,
    RateLimitError,
    _ANTI_FINGERPRINT,
    _click_turnstile_checkbox,
)

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


async def _captcha_pending(page) -> bool:
    """Whether the faucet is showing its "Please complete the CAPTCHA
    verification" toast — i.e. the last Drip click was rejected for a missing
    Turnstile token."""
    try:
        return bool(
            await page.evaluate(
                "(document.body.innerText || '').toLowerCase().includes('complete the captcha')"
            )
        )
    except Exception:
        return False


async def _solve_and_submit(page, *, timeout: float) -> str:
    """Click the Turnstile checkbox, submit, and re-solve+resubmit whenever the
    captcha toast reappears, until the faucet API responds; raise past *timeout*.

    Babylon keeps the Turnstile token in React state (no readable
    ``cf-turnstile-response`` input) and validates it client-side, so we can't
    poll a token. The checkbox iframe lives in a closed shadow root, so we click
    it via :func:`_click_turnstile_checkbox` (CDP, ``pierce=True``) and treat the
    absence of the captcha toast after a submit as success.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    solve_now = True  # solve before the first submit
    submitted = False
    while True:
        raw = await page.evaluate("window.__faucetResult")
        if raw:
            return raw
        if solve_now:
            await _click_turnstile_checkbox(page)
            await asyncio.sleep(4)  # let Cloudflare verify the click
            solve_now = False
            submitted = False  # the new token warrants a fresh submit
        if not submitted:
            await _call_js(page, _CLICK_SUBMIT)
            submitted = True
        if loop.time() > deadline:
            raise FaucetError(f"Babylon: no faucet API response within {timeout:.0f}s")
        await asyncio.sleep(_POLL_INTERVAL)
        # A captcha toast means the Drip click was rejected — re-solve and retry.
        if await _captcha_pending(page):
            solve_now = True


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
        # Install the anti-fingerprint on a blank tab *before* navigating, so the
        # faucet's Turnstile stays invisible instead of escalating to the manual
        # "Verify you are human" checkbox (which would stall the Drip button).
        page = await browser.get("about:blank")
        await page.send(
            uc.cdp.page.add_script_to_evaluate_on_new_document(source=_ANTI_FINGERPRINT)
        )
        await page.get(_PAGE_URL)
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

        # Solve Turnstile and submit. Cloudflare escalates to the visible
        # "Verify you are human" checkbox here, so click it (via CDP, which
        # pierces its closed shadow root) before each Drip attempt and re-solve
        # whenever the faucet's "complete the CAPTCHA" toast reappears.
        raw = await _solve_and_submit(page, timeout=timeout)
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
