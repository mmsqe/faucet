"""
Chainstack testnet faucet — API-first with nodriver fallback.

When ``CHAINSTACK_API_KEY`` is set the REST API is used directly (fast, no
browser required).  Without a key the automation falls back to driving the
Chainstack SPA with nodriver.

REST API (requires API key)::

    POST https://api.chainstack.com/v1/faucet/{chain}
    Authorization: Bearer <api_key>
    Body: {"address": "<wallet>"}

SPA automation flow (no API key)::

    1. Navigate to https://faucet.chainstack.com/
    2. Click the target network in the grid.
    3. Fill the wallet-address input.
    4. Wait for the invisible Turnstile widget to auto-solve.
    5. Click the submit button.
    6. Parse the transaction link from the success banner.
"""

from __future__ import annotations

import asyncio
import json
import os
import re

import aiohttp

from faucet.alchemy import FaucetError, InsufficientFaucetBalanceError, RateLimitError

_API_BASE_URL = "https://api.chainstack.com/v1/faucet"
_PAGE_URL = "https://faucet.chainstack.com/"

# Maps our slug → Chainstack REST API chain identifier.
# Source: https://github.com/chainstack/dev-portal/blob/main/openapi/faucet_api/get_tokens.json
# The REST API only supports these chains; others must use the SPA browser path.
_API_CHAIN_IDS: dict[str, str] = {
    "zksync-era-sepolia": "zksync-testnet",
    "ethereum-sepolia": "sepolia",
    "polygon-amoy": "amoy",
}

# Maps our slug → URL slug of the network card on https://faucet.chainstack.com/.
# Each network is a link to "/<url-slug>" — anchor selectors are more stable
# than visible-text matching as the labels are localized/relabeled often.
_NETWORK_URL: dict[str, str] = {
    "ethereum-sepolia": "sepolia-testnet-faucet",
    "base-sepolia": "base-testnet-faucet",
    "polygon-amoy": "amoy-faucet",
    "zksync-era-sepolia": "zksync-testnet-faucet",
    "hyperliquid-testnet": "hyperliquid-testnet-faucet",
}

#: Public set of chain slugs supported by this module.
CHAINS: set[str] = set(_NETWORK_URL)


async def drip(
    address: str,
    chain: str,
    *,
    api_key: str | None = None,
    headless: bool = False,
    timeout: float = 60.0,
) -> str | None:
    """Fund *address* on *chain* via the Chainstack faucet.

    Uses the REST API when *api_key* is set AND *chain* is in
    :data:`_API_CHAIN_IDS` (only 3 chains are exposed via REST).  Otherwise
    falls back to nodriver SPA automation, which covers all chains on
    https://faucet.chainstack.com/.

    Args:
        address: Wallet address to fund.
        chain: Chainstack chain slug — must be a key in :data:`CHAINS`.
        api_key: Chainstack API key.  Defaults to ``CHAINSTACK_API_KEY`` env var.
        headless: Run Chrome in headless mode (SPA path only, default: ``False``).
        timeout: Seconds to wait for Turnstile to solve (SPA path only, default: 60).

    Returns:
        Transaction hash, or ``None`` if the response did not include one.

    Raises:
        ValueError: *chain* is not in :data:`CHAINS`.
        InsufficientFaucetBalanceError: Chainstack faucet is dry.
        RateLimitError: Daily drip limit reached.
        FaucetError: Any other API or automation error.
    """
    if chain not in _NETWORK_URL:
        raise ValueError(
            f"Chainstack: unknown chain {chain!r}. Supported: {', '.join(sorted(CHAINS))}"
        )

    resolved_key = api_key or os.environ.get("CHAINSTACK_API_KEY", "")
    if resolved_key and chain in _API_CHAIN_IDS:
        return await _drip_via_api(address, chain, api_key=resolved_key)
    return await _drip_via_browser(
        _NETWORK_URL[chain], address, headless=headless, timeout=timeout
    )


# ---------------------------------------------------------------------------
# REST API path
# ---------------------------------------------------------------------------


async def _drip_via_api(address: str, chain: str, *, api_key: str) -> str | None:
    """POST to the Chainstack faucet REST API with Bearer-token auth."""
    api_chain = _API_CHAIN_IDS.get(chain, chain)
    url = f"{_API_BASE_URL}/{api_chain}"

    async with aiohttp.ClientSession() as session:
        async with session.post(
            url,
            json={"address": address},
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        ) as resp:
            text = await resp.text()

    if not text.strip():
        raise FaucetError(f"Chainstack API error ({resp.status}): empty response body")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise FaucetError(
            f"Chainstack API error ({resp.status}): non-JSON body: {text[:200]!r}"
        ) from exc

    if isinstance(data, dict) and "error" in data:
        msg = str(data["error"])
        if resp.status == 429:
            raise RateLimitError(msg)
        if (
            resp.status == 503
            or "insufficient" in msg.lower()
            or "balance" in msg.lower()
        ):
            raise InsufficientFaucetBalanceError(
                f"Chainstack API error ({resp.status}): {msg}"
            )
        raise FaucetError(f"Chainstack API error ({resp.status}): {msg}")

    if isinstance(data, dict):
        # API returns {"url": "https://explorer.../tx/0xabc..."} on success
        tx_url: str = data.get("url", "")
        if tx_url:
            m = re.search(r"/tx/(0x[0-9a-fA-F]{64})", tx_url)
            if m:
                return m.group(1)
        return data.get("transactionHash") or data.get("txHash") or None
    return None


# ---------------------------------------------------------------------------
# nodriver SPA path (no API key)
# ---------------------------------------------------------------------------


async def _drip_via_browser(
    network_url: str,
    address: str,
    *,
    headless: bool,
    timeout: float,
) -> str | None:
    """Drive the Chainstack faucet SPA with nodriver."""
    try:
        import nodriver as uc
    except ImportError as exc:
        raise FaucetError("nodriver is required: pip install nodriver") from exc

    browser = await uc.start(headless=headless, sandbox=not os.environ.get("CI"))
    try:
        page = await browser.get(_PAGE_URL)
        await asyncio.sleep(6)  # wait for React hydration + network grid to render

        # --- Step 1: click the target network's anchor in the grid ---
        # Match by href suffix instead of visible label — labels get localized
        # and relabeled, but the URL slug is the chain's stable identifier.
        clicked = await page.evaluate(f"""
            (() => {{
                const link = document.querySelector('a[href$={network_url!r}]') ||
                             document.querySelector('a[href*={network_url!r}]');
                if (link) {{ link.click(); return true; }}
                return false;
            }})()
        """)
        if not clicked:
            raise FaucetError(
                f"Chainstack: network with slug {network_url!r} not found on the faucet page. "
                "The page layout may have changed."
            )
        await asyncio.sleep(3)  # wait for address input to render

        # --- Step 2: fill the wallet address ---
        # The address field is the first non-readonly, non-search text input.
        addr_id = await page.evaluate("""
            (() => {
                const inputs = Array.from(
                    document.querySelectorAll('input[type="text"]:not([readonly])')
                );
                const addr = inputs.find(el => el.getAttribute('role') !== 'combobox');
                return addr ? addr.id || null : null;
            })()
        """)
        if addr_id:
            elem = await page.select(f"#{addr_id}")
        else:
            elem = await page.select("input[type='text']:not([role='combobox'])")
        if elem is None:
            raise FaucetError(
                "Chainstack: address input not found after network selection."
            )
        await elem.click()
        await elem.send_keys(address)
        # Blur triggers Turnstile initialisation on Chainstack.
        await page.evaluate("document.activeElement?.blur()")
        await asyncio.sleep(2)

        # --- Step 3: wait for Turnstile to auto-solve ---
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            if asyncio.get_event_loop().time() > deadline:
                raise FaucetError(
                    f"Chainstack: Turnstile did not solve within {timeout}s"
                )
            await asyncio.sleep(1)
            token = await page.evaluate(
                'document.querySelector("input[name=\'cf-turnstile-response\']")?.value || ""'
            )
            if token:
                break

        # --- Step 4: click the submit / Send button ---
        submitted = await page.evaluate("""
            (() => {
                const btn =
                    document.querySelector('button[type="submit"]') ||
                    document.querySelector('button.ant-btn-primary');
                if (btn) { btn.click(); return true; }
                return false;
            })()
        """)
        if not submitted:
            raise FaucetError("Chainstack: submit button not found.")
        await asyncio.sleep(4)  # wait for success/error banner

        # --- Step 5: check for error messages ---
        error_text: str = await page.evaluate("""
            (() => {
                const el = document.querySelector('.ant-alert-error, [class*="error"]');
                return el ? el.textContent : '';
            })()
        """)
        if error_text:
            lower = error_text.lower()
            if "insufficient" in lower or "empty" in lower or "balance" in lower:
                raise InsufficientFaucetBalanceError(
                    f"Chainstack faucet error: {error_text[:200]}"
                )
            if "rate" in lower or "limit" in lower or "24h" in lower:
                raise RateLimitError(error_text[:200])
            raise FaucetError(f"Chainstack faucet error: {error_text[:200]}")

        # --- Step 6: extract tx hash from success link ---
        tx_hash: str | None = await page.evaluate("""
            (() => {
                const links = Array.from(document.querySelectorAll('a[href*="/tx/"]'));
                for (const link of links) {
                    const m = link.href.match(/[/]tx[/](0x[0-9a-fA-F]{64})/);
                    if (m) return m[1];
                }
                return null;
            })()
        """)
        return tx_hash

    finally:
        browser.stop()
