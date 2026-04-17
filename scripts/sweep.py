"""Sweep all testnet native tokens and USDC from TESTNET_PRIVATE_KEY to TESTNET_ADDRESS."""

import asyncio
import os
import sys

from dotenv import load_dotenv
from web3 import Web3
from faucet.sweep import sweep

load_dotenv()


private_key = os.environ.get("TESTNET_PRIVATE_KEY", "")
to_address = os.environ.get("TESTNET_ADDRESS", "")

if not private_key:
    sys.exit("TESTNET_PRIVATE_KEY is not set")
if not to_address:
    sys.exit("TESTNET_ADDRESS is not set")

sender = Web3().eth.account.from_key(private_key).address
if sender.lower() == to_address.lower():
    sys.exit(
        f"TESTNET_PRIVATE_KEY address ({sender}) is the same as TESTNET_ADDRESS — nothing to sweep"
    )

print(f"Sweeping from {sender} → {to_address}")

results = asyncio.run(sweep(private_key, to_address))

if results:
    print(f"\nSwept across {len(results)} chain(s):")
    for r in results:
        decimals = 6 if r.token == "USDC" else 18
        print(f"  {r.chain}: {r.value / 10**decimals:.6f} {r.token}  tx={r.tx_hash}")
else:
    print("\nNothing to sweep.")
