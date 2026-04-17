"""
Alchemy testnet faucet — nodriver-based automation.

Uses an undetectable Chrome instance to solve the Cloudflare Turnstile widget
on the Alchemy faucet page, then POSTs to the faucet API.

API endpoint discovered from browser network traffic:
  POST https://www.alchemy.com/api/faucet/send
  Body: {"address": "...", "chainId": "<slug>", "turnstileToken": "..."}
"""

from __future__ import annotations

import asyncio
import os

import aiohttp

# ---------------------------------------------------------------------------
# Chain registry — all chains supported by https://www.alchemy.com/faucets
# ---------------------------------------------------------------------------

#: Mapping of Alchemy chain slug → faucet page URL.
#: Keys are the ``chainId`` values accepted by the faucet API.
CHAINS: dict[str, str] = {
    "ethereum-sepolia": "https://www.alchemy.com/faucets/ethereum-sepolia",
    "arbitrum-sepolia": "https://www.alchemy.com/faucets/arbitrum-sepolia",
    "optimism-sepolia": "https://www.alchemy.com/faucets/optimism-sepolia",
    "base-sepolia": "https://www.alchemy.com/faucets/base-sepolia",
    "polygon-amoy": "https://www.alchemy.com/faucets/polygon-amoy",
    "zksync-sepolia": "https://www.alchemy.com/faucets/zksync-sepolia",
    "starknet-sepolia": "https://www.alchemy.com/faucets/starknet-sepolia",
    "worldchain-sepolia": "https://www.alchemy.com/faucets/worldchain-sepolia",
    "monad-testnet": "https://www.alchemy.com/faucets/monad-testnet",
    "shape-sepolia": "https://www.alchemy.com/faucets/shape-sepolia",
    "lens-sepolia": "https://www.alchemy.com/faucets/lens-sepolia",
    "abstract-testnet": "https://www.alchemy.com/faucets/abstract-testnet",
    "soneium-minato": "https://www.alchemy.com/faucets/soneium-minato",
    "crossfi-testnet": "https://www.alchemy.com/faucets/crossfi-testnet",
    "gensyn-testnet": "https://www.alchemy.com/faucets/gensyn-testnet",
    "humanity-testnet": "https://www.alchemy.com/faucets/humanity-testnet",
    "syndicate-risa": "https://www.alchemy.com/faucets/syndicate-risa",
    "worldl3-devnet": "https://www.alchemy.com/faucets/worldl3-devnet",
    "stable-testnet": "https://www.alchemy.com/faucets/stable-testnet",
}

_FAUCET_API_URL = "https://www.alchemy.com/api/faucet/send"

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class FaucetError(Exception):
    """Raised when the faucet API returns an error."""


class RateLimitError(FaucetError):
    """Raised when the daily rate limit has been hit (HTTP 429).

    Attributes:
        reset_at: ISO-8601 timestamp when the limit resets, if provided
            by the API.
    """

    def __init__(self, message: str, reset_at: str | None = None) -> None:
        super().__init__(message)
        self.reset_at = reset_at


class InsufficientFaucetBalanceError(FaucetError):
    """Raised when the faucet itself has run dry (HTTP 503)."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def drip(
    address: str,
    chain: str,
    *,
    headless: bool = False,
    timeout: float = 30.0,
) -> str | None:
    """Fund *address* on *chain* via the Alchemy faucet.

    Opens a real Chrome window (undetectable by Cloudflare Turnstile), fills
    the wallet address, waits for the invisible Turnstile widget to auto-solve,
    then POSTs the token to the faucet API.

    Args:
        address: Wallet address to fund.
        chain: Alchemy chain slug, e.g. ``"optimism-sepolia"``.
            Must be a key in :data:`CHAINS`.
        headless: Run Chrome in headless mode.  ``False`` (default) is more
            reliable — Turnstile solves faster with a visible window.
        timeout: Seconds to wait for Turnstile to solve (default 30).

    Returns:
        Transaction hash string, or ``None`` if the API did not return one.

    Raises:
        ValueError: *chain* is not in :data:`CHAINS`.
        RateLimitError: Daily limit hit (1 drip per 24 h per address).
        FaucetError: Any other API or Turnstile error.
    """
    if chain not in CHAINS:
        raise ValueError(f"Unknown chain {chain!r}. Supported: {', '.join(CHAINS)}")

    token = await _get_turnstile_token(
        CHAINS[chain], address, headless=headless, timeout=timeout
    )

    async with aiohttp.ClientSession() as session:
        async with session.post(
            _FAUCET_API_URL,
            json={"address": address, "chainId": chain, "turnstileToken": token},
            headers={"Content-Type": "application/json"},
        ) as resp:
            data = await resp.json(content_type=None)

    if isinstance(data, dict) and "error" in data:
        if resp.status == 429:
            raise RateLimitError(data["error"], reset_at=data.get("resetAt"))
        if resp.status == 503:
            raise InsufficientFaucetBalanceError(
                f"Alchemy faucet error (503): {data['error']}"
            )
        raise FaucetError(f"Alchemy faucet error ({resp.status}): {data['error']}")

    if isinstance(data, dict):
        return data.get("transactionHash") or data.get("txHash")
    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _start_browser(uc, *, headless: bool = True):
    """Launch a nodriver browser that works in CI.

    nodriver's own launcher pipes Chrome's stdout/stderr, which can stall
    Chrome before the DevTools port opens.  In CI we start Chrome ourselves
    with DEVNULL streams, wait for it to be ready, then hand the running
    process to nodriver via connect-existing mode.
    """
    if not os.environ.get("CI"):
        return await uc.start(headless=headless)

    import tempfile
    from nodriver.core.config import find_chrome_executable
    from nodriver.core.util import free_port

    chrome_path = os.environ.get("CHROME_PATH") or find_chrome_executable()
    port = free_port()
    user_data_dir = tempfile.mkdtemp(prefix="uc_")

    proc = await asyncio.create_subprocess_exec(
        chrome_path,
        f"--remote-debugging-port={port}",
        "--remote-debugging-host=127.0.0.1",
        "--remote-allow-origins=*",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--headless=new",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--disable-features=IsolateOrigins,site-per-process",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    await asyncio.sleep(3)  # wait for DevTools port to be ready

    browser = await uc.start(host="127.0.0.1", port=port)
    browser._process = proc
    browser._process_pid = proc.pid
    return browser


async def _get_turnstile_token(
    page_url: str,
    address: str,
    *,
    headless: bool,
    timeout: float,
) -> str:
    """Open the faucet page with nodriver, fill the address, return the token."""
    try:
        import nodriver as uc
    except ImportError as exc:
        raise FaucetError("nodriver is required: pip install nodriver") from exc

    browser = await _start_browser(uc, headless=headless)
    try:
        page = await browser.get(page_url)
        await asyncio.sleep(5)  # let the page JS initialise the Turnstile widget

        elem = await page.select("#wallet-address")
        await elem.click()  # focus triggers Turnstile initialisation
        await elem.send_keys(address)

        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            if asyncio.get_event_loop().time() > deadline:
                raise FaucetError(f"Turnstile did not solve within {timeout}s")
            await asyncio.sleep(1)
            token: str = await page.evaluate(
                'document.querySelector("input[name=\'cf-turnstile-response\']")?.value || ""'
            )
            if token:
                return token
    finally:
        browser.stop()
