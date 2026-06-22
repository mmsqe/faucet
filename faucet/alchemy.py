"""
Alchemy testnet faucet — nodriver-based automation.

Uses an undetectable Chrome instance to solve the Cloudflare Turnstile widget
on the Alchemy faucet page, then POSTs to the faucet API.

API endpoint (from _next/static/chunks/0b_y0tnec2m-9.js):
  POST https://www.alchemy.com/api/faucets/<slug>/send
  Body: {"address": "...", "turnstileToken": "..."}
"""

from __future__ import annotations

import asyncio
import json
import os
import random

import aiohttp

# ---------------------------------------------------------------------------
# Chain registry — all chains supported by https://www.alchemy.com/faucets
# ---------------------------------------------------------------------------

_ALCHEMY_HOST = "https://www.alchemy.com"
_FAUCET_PAGE_URL_TEMPLATE = _ALCHEMY_HOST + "/faucets/{slug}"
_FAUCET_API_URL_TEMPLATE = _ALCHEMY_HOST + "/api/faucets/{slug}/send"

#: Alchemy faucet slugs — the ``chainId`` values accepted by the faucet API.
_SLUGS = (
    "ethereum-sepolia",
    "arbitrum-sepolia",
    "optimism-sepolia",
    "base-sepolia",
    "polygon-amoy",
    "zksync-sepolia",
    "worldchain-sepolia",
    "monad-testnet",
    "shape-sepolia",
    "lens-sepolia",
    "abstract-testnet",
    "soneium-minato",
    "crossfi-testnet",
    "gensyn-testnet",
    "humanity-testnet",
    "worldl3-devnet",
    "stable-testnet",
)

#: Mapping of Alchemy chain slug → faucet page URL.
CHAINS: dict[str, str] = {
    slug: _FAUCET_PAGE_URL_TEMPLATE.format(slug=slug) for slug in _SLUGS
}

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
    timeout: float = 60.0,
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
        timeout: Seconds to wait for Turnstile to solve (default 60).
            Includes a single page-reload retry if the first attempt stalls.

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
            _FAUCET_API_URL_TEMPLATE.format(slug=chain),
            json={"address": address, "turnstileToken": token},
            headers={"Content-Type": "application/json"},
        ) as resp:
            status = resp.status
            body = await resp.text()

    try:
        data = json.loads(body) if body else None
    except ValueError:
        raise FaucetError(
            f"Alchemy faucet returned non-JSON ({status}): {body[:300]!r}"
        ) from None

    if isinstance(data, dict) and "error" in data:
        if status == 429:
            raise RateLimitError(data["error"], reset_at=data.get("resetAt"))
        if status == 503:
            raise InsufficientFaucetBalanceError(
                f"Alchemy faucet error (503): {data['error']}"
            )
        raise FaucetError(f"Alchemy faucet error ({status}): {data['error']}")

    if not isinstance(data, dict):
        raise FaucetError(
            f"Alchemy faucet returned empty/unexpected body ({status}): {body[:300]!r}"
        )

    return data.get("transactionHash") or data.get("txHash")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Cloudflare anti-fingerprint, injected on new document before any page JS runs.
# Forces shadow roots open (Turnstile uses a closed one) so the DOM walker can
# reach the iframe, and hides automation tells so the challenge stays invisible
# instead of escalating to the interactive "Verify you are human" checkbox.
# Shared by chainlink.py and babylon.py.
_ANTI_FINGERPRINT = (
    "(() => {"
    "  const orig = Element.prototype.attachShadow;"
    "  Element.prototype.attachShadow = function(init) {"
    "    return orig.call(this, Object.assign({}, init, {mode: 'open'}));"
    "  };"
    "  try { Object.defineProperty(navigator, 'webdriver', {get: () => undefined, configurable: true}); } catch (e) {}"
    "  try { Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en'], configurable: true}); } catch (e) {}"
    "  try { Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5], configurable: true}); } catch (e) {}"
    "  try { window.chrome = window.chrome || {runtime: {}}; } catch (e) {}"
    "})();"
)


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

    # GitHub Actions runs as root, where Chrome refuses to launch with the
    # default sandbox. Pass sandbox=False under CI; keep it on locally.
    browser = await uc.start(headless=headless, sandbox=not os.environ.get("CI"))
    try:
        # Open a blank tab first so we can install the attachShadow override
        # *before* navigating — Cloudflare Turnstile uses a closed shadow root,
        # which forecloses our DOM walker. Forcing every shadow root to open
        # mode lets us reach the iframe.
        page = await browser.get("about:blank")
        await page.send(
            uc.cdp.page.add_script_to_evaluate_on_new_document(source=_ANTI_FINGERPRINT)
        )

        # Split timeout across two attempts — first round of the managed
        # challenge often hangs in "Verifying…" indefinitely; a fresh page
        # often rolls a passable challenge.
        per_attempt = max(45.0, timeout / 2)
        attempts = 2 if timeout >= 90 else 1
        last_err: Exception | None = None
        for attempt in range(attempts):
            try:
                return await _solve_once(page, page_url, address, timeout=per_attempt)
            except FaucetError as exc:
                last_err = exc
                if attempt == attempts - 1:
                    raise
        # Unreachable, but keeps the type checker happy.
        raise last_err if last_err else FaucetError("Turnstile failed")
    finally:
        browser.stop()


async def _solve_once(
    page,
    page_url: str,
    address: str,
    *,
    timeout: float,
) -> str:
    """Single attempt: navigate, fill address, wait for the Turnstile token."""
    await page.get(page_url)
    await asyncio.sleep(5)  # let the page JS initialise the Turnstile widget

    # Wiggle the mouse so Turnstile's invisible-mode interaction scoring
    # sees pointer activity before the address-input click. Without any
    # pointer events, modern Turnstile scores low and escalates to the
    # interactive checkbox immediately.
    try:
        for _ in range(4):
            await page.mouse_move(
                random.uniform(200, 1000),
                random.uniform(200, 700),
                steps=random.randint(8, 16),
            )
            await asyncio.sleep(random.uniform(0.1, 0.25))
    except Exception:
        pass

    elem = await page.select("#wallet-address")
    await elem.click()  # focus triggers Turnstile initialisation
    await elem.send_keys(address)

    deadline = asyncio.get_event_loop().time() + timeout
    last_click = 0.0
    while True:
        now = asyncio.get_event_loop().time()
        if now > deadline:
            raise FaucetError(f"Turnstile did not solve within {timeout}s")
        await asyncio.sleep(1)
        token: str = await page.evaluate(
            'document.querySelector("input[name=\'cf-turnstile-response\']")?.value || ""'
        )
        if token:
            return token
        # If Turnstile escalated to interactive mode (visible "Verify you
        # are human" checkbox), invisible auto-solve will never fire.
        # Click into the widget iframe to satisfy the managed challenge,
        # retrying every 5s in case the widget re-rendered.
        if now - last_click > 5:
            if await _click_turnstile_checkbox(page):
                last_click = now


async def _click_turnstile_checkbox(page) -> bool:
    """Click the Turnstile "Verify you are human" checkbox if present.

    The iframe lives inside <template shadowrootmode="closed"> (declarative
    shadow DOM), so JS-side walks can't reach it — we use CDP DOM with
    pierce=True. Coordinates from getBoxModel are page-relative; mouse
    events need viewport-relative, so we subtract scroll offset.

    Cloudflare's checkbox sits on the left side of the widget; the right
    ~40% is the "Cloudflare / Privacy / Help" branding, which is NOT part
    of the hit-box. Center-click misses, so target the left side at a
    fixed offset from the iframe edge.
    """
    import nodriver as uc

    doc = await page.send(uc.cdp.dom.get_document(depth=-1, pierce=True))
    iframe_node = _find_turnstile_iframe(doc)
    if iframe_node is None:
        return False

    # Scroll the iframe into view so its viewport-relative coords are valid.
    try:
        await page.send(
            uc.cdp.dom.scroll_into_view_if_needed(
                backend_node_id=iframe_node.backend_node_id
            )
        )
    except Exception:
        pass
    await asyncio.sleep(0.2)

    try:
        box = await page.send(
            uc.cdp.dom.get_box_model(backend_node_id=iframe_node.backend_node_id)
        )
    except Exception:
        return False
    if box is None or not getattr(box, "content", None):
        return False

    # content quad: [x1,y1, x2,y1, x2,y2, x1,y2] — page-relative.
    quad = box.content
    # Checkbox is ~30px from the iframe's left edge, vertically centered.
    # Add small jitter so repeated retries don't hammer the exact same pixel.
    page_cx = quad[0] + 30 + random.uniform(-3, 3)
    page_cy = (quad[1] + quad[5]) / 2 + random.uniform(-2, 2)

    scroll = await page.evaluate(
        "JSON.stringify({x: window.scrollX || 0, y: window.scrollY || 0})",
        return_by_value=True,
    )
    try:
        s = json.loads(scroll) if isinstance(scroll, str) else {"x": 0, "y": 0}
    except Exception:
        s = {"x": 0, "y": 0}
    cx = page_cx - s.get("x", 0)
    cy = page_cy - s.get("y", 0)

    try:
        # Multi-segment human-like trail — Turnstile rejects teleport clicks
        # and short straight-line trails. Approach from a random offset.
        start_dx = random.uniform(-120, -60)
        start_dy = random.uniform(-80, 80)
        await page.mouse_move(cx + start_dx, cy + start_dy, steps=10)
        await asyncio.sleep(random.uniform(0.05, 0.12))
        await page.mouse_move(
            cx + start_dx / 2 + random.uniform(-10, 10),
            cy + start_dy / 2 + random.uniform(-5, 5),
            steps=12,
        )
        await asyncio.sleep(random.uniform(0.05, 0.12))
        await page.mouse_move(cx, cy, steps=14)
        await asyncio.sleep(random.uniform(0.08, 0.18))
        await page.send(
            uc.cdp.input_.dispatch_mouse_event(
                "mousePressed",
                x=cx,
                y=cy,
                button=uc.cdp.input_.MouseButton("left"),
                buttons=1,
                click_count=1,
            )
        )
        # Real human clicks have ~50–60ms between press and release.
        await asyncio.sleep(random.uniform(0.07, 0.14))
        await page.send(
            uc.cdp.input_.dispatch_mouse_event(
                "mouseReleased",
                x=cx,
                y=cy,
                button=uc.cdp.input_.MouseButton("left"),
                buttons=1,
                click_count=1,
            )
        )
    except Exception:
        return False
    return True


def _find_turnstile_iframe(node):
    """Walk a CDP DOM tree (with pierce=True) and return the Cloudflare
    Turnstile iframe node, or None.

    Traverses children, shadowRoots, contentDocument, templateContent — all
    the relationships CDP exposes when pierce=True is set on getDocument.
    """
    if node is None:
        return None

    if (getattr(node, "node_name", "") or "").lower() == "iframe":
        attrs = getattr(node, "attributes", []) or []
        # attributes is a flat [name, value, name, value, ...] list.
        attr_map = {attrs[i]: attrs[i + 1] for i in range(0, len(attrs) - 1, 2)}
        src = (attr_map.get("src") or "").lower()
        title = (attr_map.get("title") or "").lower()
        if (
            "challenges.cloudflare.com" in src
            or "cdn-cgi/challenge-platform" in src
            or "turnstile" in src
            or "cloudflare" in title
            or "challenge" in title
        ):
            return node

    for child_attr in (
        "children",
        "shadow_roots",
        "pseudo_elements",
    ):
        for child in getattr(node, child_attr, None) or []:
            found = _find_turnstile_iframe(child)
            if found is not None:
                return found

    for single_attr in ("content_document", "template_content"):
        child = getattr(node, single_attr, None)
        if child is not None:
            found = _find_turnstile_iframe(child)
            if found is not None:
                return found

    return None


async def _turnstile_token(page) -> str:
    """The current Turnstile response token, or "" if not yet solved."""
    try:
        return await page.evaluate(
            'document.querySelector("input[name=\'cf-turnstile-response\']")?.value || ""'
        )
    except Exception:
        return ""


async def _has_turnstile(page) -> bool:
    """Whether a Turnstile widget (input, container, or iframe) is on the page."""
    try:
        return bool(
            await page.evaluate(
                "!!document.querySelector(\"input[name='cf-turnstile-response'], .cf-turnstile, iframe[src*='challenges.cloudflare.com']\")"
            )
        )
    except Exception:
        return False


async def _wait_for_turnstile(page, *, timeout: float) -> None:
    """Wait for the Turnstile token, clicking the checkbox if it escalates. Returns
    early when no widget is present — the captcha may gate the send, not the load.
    Shared by chainlink.py and babylon.py."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    grace = loop.time() + 2  # no widget within 2s → assume it isn't up front
    last_click = 0.0
    while True:
        now = loop.time()
        if await _turnstile_token(page):
            return
        present = await _has_turnstile(page)
        if not present and now > grace:
            return
        if now > deadline:
            if not present:
                return
            raise FaucetError(f"Turnstile did not solve within {timeout}s")
        if present and now - last_click > 5:
            if await _click_turnstile_checkbox(page):
                last_click = now
        await asyncio.sleep(0.5)
