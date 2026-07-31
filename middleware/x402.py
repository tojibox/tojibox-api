"""
x402 payment middleware for the Tojibox Oracle API.

Flow:
  1. Client calls a protected endpoint (no X-Payment header)
  2. Middleware returns 402 with GIWA ETH payment instructions
  3. Client pays ETH to the oracle wallet on GIWA Sepolia, gets a tx hash
  4. Client encodes { txHash } as base64, retries with X-Payment header
  5. Middleware verifies the tx directly via GIWA's own JSON-RPC → serves response

GIWA is a plain OP-Stack EVM chain, so there's only ONE payment path here —
a standard EVM transaction verified via eth_getTransactionReceipt /
eth_getTransaction. This replaces ZoneProof's Hedera middleware, which had
to support two schemes (an EVM-tx-hash path for HashPack-via-window.ethereum,
and a native-Hedera-tx-id path for the MCP server) verified against the
Hedera testnet mirror node. Both collapse into the one path below.

Protected routes: /api/oracle/parcels/{pin}/history
"""

import re
import json
import base64
import time
import asyncio
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from web3 import Web3

GIWA_RPC_URL       = os.getenv("GIWA_RPC_URL", "https://sepolia-rpc.giwa.io/")
RECEIVER_ADDRESS   = os.getenv("GIWA_ORACLE_ADDRESS", "").lower()
PAYMENT_WEI        = int(os.getenv("X402_PRICE_WEI", "1000000000000000"))   # 0.001 ETH
MAX_TX_AGE_SECS    = 300   # payment must be within last 5 minutes

# GIWA's RPC can have a little propagation lag between "tx broadcast" and
# "tx visible to get_transaction_receipt" — a few short retries cover that.
# This is NOT the elaborate 5-attempt/mirror-node-indexing-lag workaround
# the Hedera version needed; GIWA's RPC is a normal, reliable OP-Stack node.
RPC_RETRY_ATTEMPTS   = 3
RPC_RETRY_DELAY_SECS = 2

w3 = Web3(Web3.HTTPProvider(GIWA_RPC_URL))

# Regex patterns for routes that require payment
PROTECTED_PATTERNS = [
    r"^/api/oracle/parcels/[^/]+/history$",
]

# In-memory replay-attack guard — TX hashes that have already been used.
# Same limitation as the original: wiped on restart, narrow reuse window.
# Not worth a DB table for an MVP — documented in README.
_used_tx_ids: set = set()


def _is_protected(path: str) -> bool:
    return any(re.match(p, path) for p in PROTECTED_PATTERNS)


def _payment_required_response(resource: str) -> JSONResponse:
    """Standard x402 response body."""
    return JSONResponse(
        status_code=402,
        content={
            "x402Version": 1,
            "error": None,
            "accepts": [
                {
                    "scheme":            "giwa-eth",
                    "network":           "giwa-sepolia",
                    "maxAmountRequired": str(PAYMENT_WEI),
                    "resource":          resource,
                    "description":       "Tojibox Oracle — parcel rezoning history",
                    "mimeType":          "application/json",
                    "payTo":             RECEIVER_ADDRESS,
                    "maxTimeoutSeconds": MAX_TX_AGE_SECS,
                }
            ],
        },
        headers={"X-402-Version": "1"},
    )


def _decode_payment_header(header: str) -> dict:
    # Add base64 padding if needed
    padding = 4 - (len(header) % 4)
    if padding != 4:
        header += "=" * padding
    raw = base64.b64decode(header).decode("utf-8")
    return json.loads(raw)


async def _verify_evm_payment(tx_hash: str) -> tuple:
    """Verify a GIWA Sepolia payment tx directly via GIWA's own JSON-RPC.

    web3.py's HTTPProvider is synchronous, so the blocking RPC calls are
    run via asyncio.to_thread to keep the middleware's dispatch async.
    """
    if not tx_hash or not tx_hash.startswith("0x"):
        return False, "Malformed or missing transaction hash"

    if tx_hash in _used_tx_ids:
        return False, "Transaction already used"

    last_error = "unknown"
    for attempt in range(RPC_RETRY_ATTEMPTS):
        try:
            receipt = await asyncio.to_thread(w3.eth.get_transaction_receipt, tx_hash)

            if receipt is None:
                # Not mined / not yet visible to the RPC node — retry briefly
                last_error = f"Transaction not yet mined (attempt {attempt + 1}/{RPC_RETRY_ATTEMPTS})"
                if attempt < RPC_RETRY_ATTEMPTS - 1:
                    await asyncio.sleep(RPC_RETRY_DELAY_SECS)
                continue

            # Check success (failed txs should not unlock content)
            if receipt.status != 1:
                return False, "Transaction reverted"

            tx = await asyncio.to_thread(w3.eth.get_transaction, tx_hash)

            # Check receiver matches the oracle wallet
            to_addr = (tx["to"] or "").lower()
            if to_addr != RECEIVER_ADDRESS:
                return False, f"Wrong receiver: got {to_addr!r}, expected {RECEIVER_ADDRESS!r}"

            # Amount is the tx's value field, in wei
            value = int(tx["value"])
            if value < PAYMENT_WEI:
                return False, f"Insufficient payment: {value} wei < {PAYMENT_WEI}"

            # Recency check via the block timestamp
            block = await asyncio.to_thread(w3.eth.get_block, receipt.blockNumber)
            if time.time() - block["timestamp"] > MAX_TX_AGE_SECS:
                return False, "Payment too old (max 5 minutes)"

            _used_tx_ids.add(tx_hash)
            return True, "ok"

        except Exception as exc:
            last_error = f"EVM verification error: {exc}"
            if attempt < RPC_RETRY_ATTEMPTS - 1:
                await asyncio.sleep(RPC_RETRY_DELAY_SECS)

    return False, last_error


class X402Middleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not _is_protected(request.url.path):
            return await call_next(request)

        x_payment = request.headers.get("X-Payment")

        if not x_payment:
            return _payment_required_response(request.url.path)

        try:
            payment = _decode_payment_header(x_payment)
            tx_hash = payment.get("txHash", "")
            print(f"[x402] incoming: keys={list(payment.keys())} txHash={tx_hash!r}")
        except Exception:
            return JSONResponse(
                status_code=402,
                content={"x402Version": 1, "error": "Malformed X-Payment header"},
            )

        ok, reason = await _verify_evm_payment(tx_hash)
        if not ok:
            return JSONResponse(
                status_code=402,
                content={"x402Version": 1, "error": reason},
                headers={"X-402-Version": "1"},
            )

        return await call_next(request)
