"""Shared transaction-nonce allocation for faucets that sign with one key.

Faucets dripping concurrently from the same wallet would each call
``get_transaction_count`` independently, read the same value, and collide. A
shared :class:`NonceManager` hands out strictly sequential nonces instead.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from web3 import AsyncWeb3


class NonceManager:
    """Hands out sequential nonces for one account across async tasks.

    The first :meth:`reserve` reads the account's transaction count from the
    chain; later calls increment locally. ``reserve`` holds a lock for the
    caller's ``async with`` block and advances the nonce only if that block
    exits without raising, so a failed send leaves the nonce free for the next
    caller.
    """

    def __init__(self, w3: AsyncWeb3, address: str) -> None:
        self._w3 = w3
        self._address = AsyncWeb3.to_checksum_address(address)
        self._next: int | None = None
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def reserve(self) -> AsyncIterator[int]:
        """Yield the next nonce, advancing it only if the block succeeds."""
        async with self._lock:
            if self._next is None:
                self._next = await self._w3.eth.get_transaction_count(self._address)
            yield self._next
            # Reached only if the caller's block did not raise — a failed send
            # must not consume a nonce.
            self._next += 1
