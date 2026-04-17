"""
Circle testnet USDC/EURC faucet — nodriver-based automation.

Drips 20 testnet USDC (or EURC) from https://faucet.circle.com/ using a real
Chrome instance that passes Google reCAPTCHA v3 automatically.

Rate limit: one request per (address, chain, token) pairing per 2 hours.

Automation flow:
  1. Navigate to https://faucet.circle.com/
  2. Select the target network from the dropdown.
  3. Optionally switch the currency toggle to EURC.
  4. Fill ``input[placeholder="Wallet address"]``.
  5. Click the "Send 20 USDC/EURC" button.
  6. Wait for the success banner.
"""

from __future__ import annotations

import asyncio

from faucet.alchemy import FaucetError, RateLimitError

_PAGE_URL = "https://faucet.circle.com/"

# Maps our slug → network display text in Circle's chain dropdown.
# Display names are exactly as shown on https://faucet.circle.com/
# Only chains we actively use (have USDC contracts or a common non-EVM need).
_NETWORK_TEXT: dict[str, str] = {
    # ── EVM (all have entries in USDC_CONTRACTS below) ───────────────────────
    "ethereum-sepolia": "Ethereum Sepolia",
    "arbitrum-sepolia": "Arbitrum Sepolia",
    "base-sepolia": "Base Sepolia",
    "optimism-sepolia": "OP Sepolia",
    "polygon-amoy": "Polygon PoS Amoy",
    "avalanche-fuji": "Avalanche Fuji",
    "linea-sepolia": "Linea Sepolia",
    "zksync-sepolia": "ZKsync Sepolia",
    "unichain-sepolia": "Unichain Sepolia",
    # ── Non-EVM ──────────────────────────────────────────────────────────────
    "solana-devnet": "Solana Devnet",
}

#: Chain slugs supported by this module.
CHAINS: set[str] = set(_NETWORK_TEXT)

# Testnet USDC contract addresses (EVM chains only).
# Source: https://developers.circle.com/wallets/monitored-tokens#usdc-token-ids
USDC_CONTRACTS: dict[str, str] = {
    "ethereum-sepolia": "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238",
    "arbitrum-sepolia": "0x75faf114eafb1BDbe2F0316DF893fd58CE46AA4d",
    "base-sepolia": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
    "optimism-sepolia": "0x5fd84259d66Cd46123540766Be93DFE6D43130D7",
    "polygon-amoy": "0x41E94Eb019C0762f9Bfcf9Fb1E58725BfB0e7582",
    "avalanche-fuji": "0x5425890298aed601595a70AB815c96711a31Bc65",
    "linea-sepolia": "0x176211869cA2b568f2A7D4EE941E073a821EE1ff",
    "zksync-sepolia": "0x1d17CBcF0D6D143135aE902365D2E5e2A16538D4",
    "unichain-sepolia": "0x31d0220469e10c4E71834a79b1f276d740d3768F",
}


async def drip(
    address: str,
    chain: str,
    *,
    token: str = "USDC",
    headless: bool = False,
    timeout: float = 60.0,
) -> None:
    """Drip 20 testnet *token* to *address* on *chain* via the Circle faucet.

    Args:
        address: Wallet address to fund.
        chain: Chain slug — must be a key in :data:`CHAINS`.
        token: ``"USDC"`` (default) or ``"EURC"``.
        headless: Run Chrome in headless mode (default: ``False``).
        timeout: Seconds to wait for the success banner (default: 60).

    Returns:
        ``None`` — Circle's faucet page does not surface a transaction hash.

    Raises:
        ValueError: *chain* is not in :data:`CHAINS` or *token* is invalid.
        RateLimitError: The 2-hour per-pairing rate limit has been hit.
        FaucetError: Network selection failed, address rejected, or timeout.
    """
    token = token.upper()
    if token not in ("USDC", "EURC"):
        raise ValueError(
            f"Circle faucet: unsupported token {token!r}. Use 'USDC' or 'EURC'."
        )
    if chain not in _NETWORK_TEXT:
        raise ValueError(
            f"Circle faucet: unknown chain {chain!r}. Supported: {', '.join(sorted(CHAINS))}"
        )
    await _drip_via_browser(
        _NETWORK_TEXT[chain], address, token=token, headless=headless, timeout=timeout
    )


async def _drip_via_browser(
    network_text: str,
    address: str,
    *,
    token: str,
    headless: bool,
    timeout: float,
) -> None:
    """Drive the Circle faucet SPA with nodriver."""
    try:
        import nodriver as uc
    except ImportError as exc:
        raise FaucetError("nodriver is required: pip install nodriver") from exc

    from faucet.alchemy import _start_browser

    browser = await _start_browser(uc, headless=headless)
    try:
        page = await browser.get(_PAGE_URL)
        await asyncio.sleep(5)  # wait for React hydration and reCAPTCHA to load

        # --- Step 1: select the target network ---
        # Circle uses Downshift for its chain dropdown.  The toggle button has
        # aria-haspopup="listbox" and an id ending in "-toggle-button".
        # Options are <span role="option"> with a .select-label[title] inner span.
        opened = await page.evaluate("""
            (() => {
                const trigger =
                    document.querySelector('[aria-haspopup="listbox"]') ||
                    document.querySelector('[role="combobox"]') ||
                    document.querySelector('[id$="-toggle-button"]');
                if (trigger) { trigger.click(); return true; }
                return false;
            })()
        """)
        if not opened:
            raise FaucetError("Circle faucet: network dropdown trigger not found.")
        await asyncio.sleep(1)

        # network_text is injected as a JS string literal using double quotes so
        # names containing spaces/parens (e.g. "Polygon PoS Amoy") are safe.
        _raw = await page.evaluate(f"""
            (() => {{
                const target = "{network_text}";
                // Primary: exact title match on .select-label span.
                const label = document.querySelector(
                    '.select-label[title="' + target + '"]'
                );
                if (label) {{
                    const opt = label.closest('[role="option"]');
                    if (opt) {{ opt.click(); return 'ok'; }}
                }}
                // Fallback: case-insensitive text content scan.
                const needle = target.toLowerCase();
                const options = Array.from(document.querySelectorAll('[role="option"]'));
                const match = options.find(
                    el => el.textContent.trim().toLowerCase().includes(needle)
                );
                if (match) {{ match.click(); return 'ok'; }}
                return options
                    .map(el => el.querySelector('.select-label')?.textContent.trim())
                    .filter(Boolean).join('||');
            }})()
        """)
        result = str(_raw) if _raw is not None else ""
        if result != "ok":
            available = result or "(none found — selectors may need updating)"
            raise FaucetError(
                f"Circle faucet: network {network_text!r} not found in dropdown. "
                f"Available options: {available}"
            )
        await asyncio.sleep(1)

        # --- Step 2: switch to EURC if requested (default is USDC) ---
        if token == "EURC":
            switched = await page.evaluate("""
                (() => {
                    const btns = Array.from(document.querySelectorAll('button'));
                    const eurc = btns.find(b => b.textContent.trim() === 'EURC');
                    if (eurc) { eurc.click(); return true; }
                    return false;
                })()
            """)
            if not switched:
                raise FaucetError("Circle faucet: EURC toggle button not found.")
            await asyncio.sleep(1)

        # --- Step 3: fill the wallet address ---
        elem = await page.select('input[placeholder="Wallet address"]')
        if elem is None:
            raise FaucetError("Circle faucet: wallet address input not found.")
        await elem.click()
        await elem.send_keys(address)

        # --- Step 4: click the Send button ---
        # Button text is "Send 20 USDC" or "Send 20 EURC".
        sent = await page.evaluate(f"""
            (() => {{
                const btns = Array.from(document.querySelectorAll('button'));
                const send = btns.find(b => b.textContent.includes('Send') && b.textContent.includes({token!r}));
                if (send) {{ send.click(); return true; }}
                return false;
            }})()
        """)
        if not sent:
            raise FaucetError(f"Circle faucet: 'Send ... {token}' button not found.")

        # --- Step 5: wait for success or error banner ---
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            if asyncio.get_event_loop().time() > deadline:
                raise FaucetError(
                    f"Circle faucet: no response within {timeout}s after submit."
                )
            await asyncio.sleep(1)

            result = await page.evaluate("""
                (() => {
                    // Success banner
                    const page = document.body.textContent || '';
                    if (page.includes('Tokens sent')) return 'ok';
                    if (page.includes('Limit exceeded') || page.includes('limit exceeded')) return 'rate_limit';
                    if (page.includes('Something went wrong')) return 'error';
                    return '';
                })()
            """)
            if result == "ok":
                return
            if result == "rate_limit":
                raise RateLimitError(
                    f"Circle faucet: rate limit hit for {address} on {network_text} "
                    "(one request per pairing per 2 hours)."
                )
            if result == "error":
                raise FaucetError("Circle faucet: 'Something went wrong'.")

    finally:
        browser.stop()
