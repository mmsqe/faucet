# faucet

Automated testnet faucet drips via [Alchemy](https://www.alchemy.com/faucets) and [Circle](https://faucet.circle.com/), with a [Chainstack](https://faucet.chainstack.com/) fallback.

Uses `nodriver` (undetectable Chrome) to solve Cloudflare Turnstile / reCAPTCHA — no API key required.  
The Chainstack path also supports a REST API key for instant, keyless drips.

## Supported chains

| Chain | Slug |
|-------|------|
| Ethereum Sepolia | `ethereum-sepolia` |
| OP Sepolia | `optimism-sepolia` |
| Base Sepolia | `base-sepolia` |
| zkSync Sepolia | `zksync-sepolia` |
| Arbitrum Sepolia | `arbitrum-sepolia` |
| Polygon Amoy | `polygon-amoy` |
| + more | see `faucet.CHAINS` |

USDC drips (via Circle) are supported on all chains in `faucet.USDC_CHAINS`.

## Quick start

```python
import asyncio
from faucet import drip, drip_usdc, sweep

# ETH drip
asyncio.run(drip("0xYourAddress", "optimism-sepolia"))

# USDC drip (20 USDC)
asyncio.run(drip_usdc("0xYourAddress", "base-sepolia"))

# Sweep all testnet ETH from a private key to another address
asyncio.run(sweep("0xPrivateKey", "0xDestinationAddress"))
```

## Sweep

`sweep(private_key, to_address)` sends the full ETH balance (minus gas) from the source wallet to `to_address` on every supported EVM testnet in parallel.  Chains with zero balance or balance below the gas cost are silently skipped.

```
Chains swept: ethereum-sepolia, optimism-sepolia, base-sepolia, zksync-sepolia,
              arbitrum-sepolia, polygon-amoy, avalanche-fuji, hyperliquid-testnet
```

## Environment variables

| Variable | Description |
|----------|-------------|
| `TESTNET_ADDRESS` | Destination wallet address (pytest fixtures + sweep target) |
| `TESTNET_PRIVATE_KEY` | Source wallet private key for `sweep` (must not control `TESTNET_ADDRESS`) |
| `CHAINSTACK_API_KEY` | Enables the fast REST path for Chainstack chains |
| `INFURA_KEY` | Infura project key — if set, Infura endpoints are used for Sepolia, OP Sepolia, Base Sepolia, zkSync Sepolia |
| `HL_TESTNET_RPC_URL` | Override Hyperliquid testnet RPC (default: `https://rpc.hyperliquid-testnet.xyz/evm`) |

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

# testnet tests (requires TESTNET_ADDRESS)
uv run pytest -m testnet
```
