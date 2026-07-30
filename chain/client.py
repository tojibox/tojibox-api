"""
GIWA on-chain client for the Togibox Oracle API.

Thin web3.py wrapper around TogiboxReportReceipt.sol (see
contracts/src/TogiboxReportReceipt.sol) so routes/parcels.py can mint a
report receipt and read it back directly from GIWA state, in-process — no
sidecar process, unlike the Hedera version this replaces
(oracle/hedera/service.mjs, a Node process wrapping @hashgraph/sdk).

Env vars (see .env.example):
  GIWA_RPC_URL                     — https://sepolia-rpc.giwa.io/
  GIWA_CHAIN_ID                    — 91342
  GIWA_EXPLORER_URL                — https://sepolia-explorer.giwa.io
  GIWA_ORACLE_PRIVATE_KEY          — signs mintReceipt() calls
  GIWA_ORACLE_ADDRESS              — default recipient of minted receipts
  TOGIBOX_REPORT_RECEIPT_ADDRESS   — deployed contract address
"""
import json
import os
from pathlib import Path

from web3 import Web3

GIWA_RPC_URL      = os.getenv("GIWA_RPC_URL", "https://sepolia-rpc.giwa.io/")
GIWA_EXPLORER_URL = os.getenv("GIWA_EXPLORER_URL", "https://sepolia-explorer.giwa.io")
CHAIN_ID          = int(os.getenv("GIWA_CHAIN_ID", "91342"))

ORACLE_PRIVATE_KEY       = os.getenv("GIWA_ORACLE_PRIVATE_KEY", "")
ORACLE_ADDRESS            = os.getenv("GIWA_ORACLE_ADDRESS", "")
RECEIPT_CONTRACT_ADDRESS = os.getenv("TOGIBOX_REPORT_RECEIPT_ADDRESS", "")

_ABI_PATH = Path(__file__).parent / "abi" / "TogiboxReportReceipt.json"
with open(_ABI_PATH) as _f:
    RECEIPT_ABI = json.load(_f)

w3 = Web3(Web3.HTTPProvider(GIWA_RPC_URL))

_contract = None
if RECEIPT_CONTRACT_ADDRESS:
    _contract = w3.eth.contract(
        address=Web3.to_checksum_address(RECEIPT_CONTRACT_ADDRESS),
        abi=RECEIPT_ABI,
    )


def _get_contract():
    if _contract is None:
        raise RuntimeError(
            "TOGIBOX_REPORT_RECEIPT_ADDRESS is not configured — deploy "
            "contracts/src/TogiboxReportReceipt.sol and set the address in .env"
        )
    return _contract


def _to_bytes32(report_hash: str) -> bytes:
    h = report_hash[2:] if report_hash.startswith("0x") else report_hash
    return bytes.fromhex(h)


def mint_receipt(pin: str, report_hash: str, oracle_address: str, generated_at: str) -> dict:
    """
    Mint a TogiboxReportReceipt NFT recording this report on-chain.

    Returns {"token_id": int, "tx_hash": str, "explorer_url": str}.

    Raises RuntimeError / web3 exceptions on misconfiguration or a reverted
    tx — callers (routes/parcels.py _record_onchain()) treat this as
    best-effort and catch exceptions so a chain hiccup never blocks report
    generation, same graceful-degradation behavior as the ZoneProof/Hedera
    original.
    """
    if not ORACLE_PRIVATE_KEY:
        raise RuntimeError("GIWA_ORACLE_PRIVATE_KEY is not set")

    contract = _get_contract()
    to_addr  = Web3.to_checksum_address(oracle_address or ORACLE_ADDRESS)
    account  = w3.eth.account.from_key(ORACLE_PRIVATE_KEY)

    tx = contract.functions.mintReceipt(
        to_addr, _to_bytes32(report_hash), pin, generated_at
    ).build_transaction({
        "from":     account.address,
        "nonce":    w3.eth.get_transaction_count(account.address),
        "chainId":  CHAIN_ID,
    })

    signed_tx = w3.eth.account.sign_transaction(tx, private_key=ORACLE_PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

    if receipt.status != 1:
        raise RuntimeError(f"mintReceipt transaction reverted: {tx_hash.hex()}")

    token_id = None
    try:
        logs = contract.events.ReportMinted().process_receipt(receipt)
        if logs:
            token_id = logs[0]["args"]["tokenId"]
    except Exception:
        pass

    tx_hash_hex = tx_hash.hex()
    if not tx_hash_hex.startswith("0x"):
        tx_hash_hex = "0x" + tx_hash_hex

    return {
        "token_id":     token_id,
        "tx_hash":      tx_hash_hex,
        "explorer_url": f"{GIWA_EXPLORER_URL}/tx/{tx_hash_hex}",
    }


def get_report(report_hash: str):
    """
    Read the on-chain Report struct for a given report hash from
    TogiboxReportReceipt's public `reports` mapping.

    Returns a dict {report_hash, pin, oracle_address, generated_at,
    token_id} or None if unset (all-zero struct) or the contract isn't
    configured.
    """
    if _contract is None:
        return None

    contract = _get_contract()
    hash_bytes = _to_bytes32(report_hash)

    stored_hash, pin, oracle_addr, generated_at = contract.functions.reports(hash_bytes).call()

    if stored_hash == b"\x00" * 32 or not pin:
        return None

    token_id = contract.functions.reportToTokenId(hash_bytes).call()

    return {
        "report_hash":    report_hash,
        "pin":            pin,
        "oracle_address": oracle_addr,
        "generated_at":   generated_at,
        "token_id":       token_id,
    }
