# faucet

Automated testnet faucet drips via [Alchemy](https://www.alchemy.com/faucets), [Circle](https://faucet.circle.com/), [Chainstack](https://faucet.chainstack.com/), and [Aave](https://gho.aave.com/faucet/).

Uses `nodriver` (undetectable Chrome) to solve Cloudflare Turnstile / reCAPTCHA — no API key required for browser-based paths. The Chainstack path also accepts a REST API key for instant, keyless drips.

## Supported tokens

| Token | Provider | Chains |
|-------|----------|--------|
| Native ETH / HYPE | Alchemy + Chainstack | 21 chains — see `faucet.CHAINS` |
| USDC / EURC | Circle | 9 EVM chains + Solana Devnet — see `faucet.USDC_CHAINS` |
| GHO, DAI, USDC, USDT, WBTC, WETH, LINK, AAVE | Aave V3 | Ethereum Sepolia |

## Quick start

```python
import asyncio
from faucet import drip, drip_usdc, sweep
from faucet import aave

# Native ETH drip
asyncio.run(drip("0xYourAddress", "optimism-sepolia"))

# USDC drip (20 USDC)
asyncio.run(drip_usdc("0xYourAddress", "base-sepolia"))

# Aave testnet tokens (requires private key for gas)
asyncio.run(aave.drip("0xYourAddress", "0xPrivateKey", "GHO"))

# Sweep all testnet ETH + USDC from a wallet to another address
asyncio.run(sweep("0xPrivateKey", "0xDestinationAddress"))
```

## Scripts

### `scripts/drip.py` — fund all chains at once

Drips native tokens (all Alchemy + Chainstack chains), USDC (all Circle chains), and Aave tokens (Ethereum Sepolia) in parallel, with at most 3 browser windows open at a time.

```bash
TESTNET_ADDRESS=0x... uv run python scripts/drip.py

# Also mint Aave tokens (requires ETH for gas)
TESTNET_ADDRESS=0x... TESTNET_PRIVATE_KEY=0x... uv run python scripts/drip.py
```

### `scripts/sweep.py` — sweep all chains

Sends the full balance (native + USDC) from a source wallet to `TESTNET_ADDRESS` across every supported EVM testnet in parallel.

```bash
TESTNET_PRIVATE_KEY=0x... TESTNET_ADDRESS=0x... uv run python scripts/sweep.py
```

## Environment variables

| Variable | Description |
|----------|-------------|
| `TESTNET_ADDRESS` | Destination wallet address |
| `TESTNET_PRIVATE_KEY` | Source wallet key for `sweep` and Aave gas |
| `CHAINSTACK_API_KEY` | Enables the fast REST path for Chainstack chains |
| `INFURA_KEY` | Infura project key — used for Sepolia RPC endpoints |
| `HL_TESTNET_RPC_URL` | Override Hyperliquid testnet RPC |

## Fallback behaviour

```
drip(address, chain)
  └─ Alchemy faucet
       └─ 503 Insufficient balance → Chainstack fallback
            ├─ CHAINSTACK_API_KEY set → REST API (fast, no browser)
            └─ no key → nodriver SPA automation
```

## Running tests

```bash
uv sync

# unit / config tests (no network)
uv run pytest -m live
```
