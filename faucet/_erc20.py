"""Shared orchestration for ERC-20 testnet faucets that claim via a contract call.

aave.drip_all and compound.drip_all differ only in the contract call —
``mint(token, to, amount)`` vs ``drip(token)``. This factors out the rest:
one tx per token from a shared nonce, then receipts gathered in parallel.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from web3 import AsyncWeb3

from faucet.nonce import NonceManager

log = logging.getLogger(__name__)

#: Builds the (unsent) contract call for one token, given the contract object,
#: the token symbol, its checksummed address, and the checksummed recipient.
MakeCall = Callable[[Any, str, str, str], Any]


async def drip_all(
    address: str,
    private_key: str,
    tokens: list[str] | None,
    *,
    rpc_url: str,
    nonce_manager: NonceManager | None,
    token_addresses: dict[str, str],
    contract_address: str,
    contract_abi: list,
    make_call: MakeCall,
    reason: Callable[[Exception], str] = repr,
    require_signer_is_recipient: bool = False,
    log_verb: str = "dripping",
) -> dict[str, tuple[str | None, str | None]]:
    """Claim each of *tokens* from a faucet contract, one transaction per token.

    *make_call* ``(contract, symbol, token_addr, recipient) -> ContractFunction``
    builds the per-token call; *reason* maps an exception to an error string;
    *require_signer_is_recipient* rejects *address* unless it is the signer's own
    wallet (faucets that only dispense to ``msg.sender``).

    Returns ``{symbol: (tx_hash, None)}`` on success or ``{symbol: (None, error)}``
    on failure, for each requested token.
    """
    want = [t.upper() for t in (tokens or list(token_addresses))]
    unknown = [t for t in want if t not in token_addresses]
    if unknown:
        raise ValueError(
            f"unknown token(s): {unknown}. Supported: {sorted(token_addresses)}"
        )

    w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(rpc_url))
    account = w3.eth.account.from_key(private_key)
    recipient = AsyncWeb3.to_checksum_address(address)
    if require_signer_is_recipient and recipient != account.address:
        raise ValueError(
            f"address {recipient} does not match the private key's wallet "
            f"{account.address}; the faucet can only dispense to the signer"
        )
    contract = w3.eth.contract(
        address=AsyncWeb3.to_checksum_address(contract_address), abi=contract_abi
    )
    nonces = nonce_manager or NonceManager(w3, account.address)
    log.info("%s %d token(s) to %s", log_verb, len(want), account.address)

    sent: dict[str, str] = {}
    errors: dict[str, str] = {}
    for token in want:
        token_addr = AsyncWeb3.to_checksum_address(token_addresses[token])
        try:
            async with nonces.reserve() as nonce:
                call = make_call(contract, token, token_addr, recipient)
                tx = await call.build_transaction(
                    {"from": account.address, "nonce": nonce}
                )
                signed = account.sign_transaction(tx)
                tx_hash = await w3.eth.send_raw_transaction(signed.raw_transaction)
                log.info("%s sent  tx=%s (nonce=%d)", token, tx_hash.hex(), nonce)
                sent[token] = tx_hash.hex()
        except Exception as exc:
            msg = reason(exc)
            log.warning("%s send failed: %s", token, msg)
            errors[token] = msg

    async def _wait(token: str, tx_hash: str) -> tuple[str, str | None, str | None]:
        log.debug("%s waiting for receipt tx=%s", token, tx_hash)
        try:
            receipt = await w3.eth.wait_for_transaction_receipt(
                tx_hash, timeout=120, poll_latency=5
            )
            if receipt["status"] != 1:
                log.warning("%s reverted tx=%s", token, tx_hash)
                return token, None, f"reverted (tx={tx_hash})"
            log.info("%s confirmed block=%s", token, receipt["blockNumber"])
            return token, tx_hash, None
        except Exception as exc:
            msg = reason(exc)
            log.warning("%s receipt error: %s", token, msg)
            return token, None, msg

    receipts = await asyncio.gather(*[_wait(t, h) for t, h in sent.items()])

    result: dict[str, tuple[str | None, str | None]] = {}
    for token, tx_hash, err in receipts:
        result[token] = (tx_hash, err)
    for token, err in errors.items():
        result[token] = (None, err)
    return result
