# faucet

Automated testnet faucet drips via [Alchemy](https://www.alchemy.com/faucets), with a [Chainstack](https://faucet.chainstack.com/) fallback when Alchemy runs dry.

Uses `nodriver` (undetectable Chrome) to solve Cloudflare Turnstile — no API key required.  
The Chainstack path also supports a REST API key for keyless, instant drips.

## Supported chains

| Chain | Alchemy slug |
|-------|-------------|
| Ethereum Sepolia | `ethereum-sepolia` |
| OP Sepolia | `optimism-sepolia` |
| Base Sepolia | `base-sepolia` |
| zkSync Sepolia | `zksync-sepolia` |
| Arbitrum Sepolia | `arbitrum-sepolia` |
| Polygon Amoy | `polygon-amoy` |
| + 13 more | see `faucet.CHAINS` |

## Quick start

```python
import asyncio
from faucet import drip

tx = asyncio.run(drip("0xYourAddress", "optimism-sepolia"))
print("tx:", tx)
```

## Environment variables

| Variable | Description |
|----------|-------------|
| `CHAINSTACK_API_KEY` | Chainstack API key — enables the fast REST path for Chainstack chains |
| `TESTNET_PRIVATE_KEY` | Hex private key used by the pytest fixtures |
| `SEPOLIA_RPC_URL` | Override Sepolia RPC (default: `https://rpc.sepolia.org`) |
| `OP_SEPOLIA_RPC_URL` | Override OP Sepolia RPC (default: `https://sepolia.optimism.io`) |
| `BASE_SEPOLIA_RPC_URL` | Override Base Sepolia RPC (default: `https://sepolia.base.org`) |
| `ZKSYNC_SEPOLIA_RPC_URL` | Override zkSync Sepolia RPC (default: `https://sepolia.era.zksync.dev`) |

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
# unit tests only (no network)
uv run pytest -m live

# testnet tests (requires TESTNET_PRIVATE_KEY)
uv run pytest -m testnet
```
