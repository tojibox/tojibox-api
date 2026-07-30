"""
/api/oracle/parcels/{pin}               <- parcel details
/api/oracle/parcels/{pin}/history/peek  <- free preview
/api/oracle/parcels/{pin}/history       <- full history (x402 gated) + Togibox seal
/api/oracle/verify/{report_hash}        <- verify a report seal
"""
import hashlib
import json
import os
from datetime import datetime, timezone

from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import APIRouter, HTTPException

from store import get_parcel, get_parcel_history, get_parcel_history_peek, search_parcels_by_address
from chain import client as chain_client

router = APIRouter(tags=["parcels"])

# In-memory registry of issued report seals  { hash: seal_dict }.
# Fast local cache — same pattern as the ZoneProof/Hedera original, and the
# same known limitation (wiped on restart). Unlike the original, this is no
# longer the *only* source of truth: /verify/{hash} below also reads the
# TogiboxReportReceipt contract on GIWA as a durable fallback, since minting
# a receipt NFT already happens on every report generation (see
# _record_onchain()).
_REPORT_REGISTRY: dict[str, dict] = {}

ORACLE_PRIVATE_KEY = os.getenv("GIWA_ORACLE_PRIVATE_KEY", "")
ORACLE_ADDRESS     = os.getenv("GIWA_ORACLE_ADDRESS", "").lower()
ORACLE_ENS         = os.getenv("ORACLE_ENS", "")  # no ENS name registered for Togibox yet


def _sign_report(data: dict) -> dict:
    """Hash the report payload and sign it with the oracle ECDSA key.

    Plain ECDSA / EIP-191 signing via eth_account — chain-agnostic, kept
    unchanged from the ZoneProof original apart from renamed env vars.
    """
    generated_at = datetime.now(timezone.utc).isoformat()

    # Canonical payload — only stable fields so the hash is reproducible
    payload = {
        "pin":             data.get("parcel", {}).get("pin", ""),
        "site_address":    data.get("parcel", {}).get("site_address", ""),
        "total_petitions": data.get("total_petitions", 0),
        "on_chain_count":  data.get("on_chain_count", 0),
        "oracle_ens":      ORACLE_ENS,
        "oracle_address":  ORACLE_ADDRESS,
        "generated_at":    generated_at,
    }
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    report_hash  = "0x" + hashlib.sha256(payload_json.encode()).hexdigest()

    # ECDSA sign with oracle private key (Ethereum personal_sign / EIP-191)
    signature = ""
    if ORACLE_PRIVATE_KEY:
        msg       = encode_defunct(text=f"Togibox Report\n{report_hash}")
        signed    = Account.sign_message(msg, private_key=ORACLE_PRIVATE_KEY)
        signature = signed.signature.hex()
        if not signature.startswith("0x"):
            signature = "0x" + signature

    seal = {
        "report_hash":      report_hash,
        "oracle_signature": signature,
        "oracle_ens":       ORACLE_ENS,
        "oracle_address":   ORACLE_ADDRESS,
        "generated_at":     generated_at,
        "verify_url":       f"/verify/{report_hash}",
    }

    # Register so the verify endpoint can look it up
    _REPORT_REGISTRY[report_hash] = {**seal, "pin": payload["pin"], "site_address": payload["site_address"]}
    return seal


def _record_onchain(pin: str, seal: dict) -> dict:
    """
    Mint a TogiboxReportReceipt NFT on GIWA recording this report seal
    on-chain, calling chain/client.py directly in-process via web3.py.

    Replaces ZoneProof's _log_to_hedera(), which made two HTTP POSTs to a
    Node sidecar (oracle/hedera/service.mjs) wrapping the Hedera SDK for
    HCS + HTS. GIWA is plain EVM, so there's no sidecar here — one mint
    call covers what used to take an HCS audit entry + a separate HTS
    mint.

    Non-blocking / best-effort, same graceful-degradation behavior as the
    original: any chain hiccup is swallowed here so report generation
    never breaks because of it.
    """
    extras: dict = {}
    try:
        minted = chain_client.mint_receipt(
            pin=pin,
            report_hash=seal["report_hash"],
            oracle_address=seal["oracle_address"],
            generated_at=seal["generated_at"],
        )
        if minted:
            extras["nft_token_id"]  = minted.get("token_id")
            extras["nft_tx_hash"]   = minted.get("tx_hash")
            extras["nft_explorer"]  = minted.get("explorer_url")
    except Exception as exc:
        print(f"[parcels] WARNING: on-chain receipt mint failed (degrading gracefully): {exc}")
    return extras


@router.get("/parcels/search")
def search_parcels(address: str = "", q: str = ""):
    """Search parcels by address fragment — free, no payment required."""
    query = address or q
    if not query:
        raise HTTPException(status_code=400, detail="Provide ?address=... or ?q=...")
    results = search_parcels_by_address(query)
    if not results:
        raise HTTPException(status_code=404, detail=f"No parcels found matching '{query}'")
    return {"results": results, "count": len(results)}


@router.get("/parcels/{pin}")
def get_parcel_detail(pin: str):
    parcel = get_parcel(pin)
    if not parcel:
        raise HTTPException(status_code=404, detail=f"Parcel {pin} not found")
    return parcel


@router.get("/parcels/{pin}/history/peek")
def get_parcel_history_peek_route(pin: str):
    result = get_parcel_history_peek(pin)
    if not result:
        raise HTTPException(status_code=404, detail=f"Parcel {pin} not found")
    return result


@router.get("/parcels/{pin}/history")
def get_parcel_history_route(pin: str):
    result = get_parcel_history(pin)
    if not result:
        raise HTTPException(status_code=404, detail=f"Parcel {pin} not found")
    seal = _sign_report(result)
    # Mint the on-chain report receipt (TogiboxReportReceipt NFT on GIWA)
    onchain_extras = _record_onchain(pin, seal)
    seal.update(onchain_extras)
    if seal["report_hash"] in _REPORT_REGISTRY:
        _REPORT_REGISTRY[seal["report_hash"]].update(onchain_extras)
    result["verification_seal"] = seal
    return result


@router.get("/verify/{report_hash}")
def verify_report(report_hash: str):
    """
    Verify a Togibox report seal — ECDSA signature + on-chain NFT receipt.

    Checks the in-memory registry first (fast path, populated since the
    API last restarted). If not found there, falls back to reading the
    TogiboxReportReceipt contract's public `reports` mapping directly on
    GIWA — this is the durability fix over the ZoneProof/Hedera original,
    whose verify endpoint only had the in-memory dict and would report
    "not found" for any report issued before the last restart.
    """
    seal = _REPORT_REGISTRY.get(report_hash)

    onchain_report = None
    if not seal:
        try:
            onchain_report = chain_client.get_report(report_hash)
        except Exception as exc:
            print(f"[parcels] WARNING: on-chain report lookup failed: {exc}")

        if not onchain_report:
            return {
                "valid":       False,
                "reason":      "Report hash not found in local registry or on-chain.",
                "report_hash": report_hash,
            }

        # Reconstruct a seal-shaped dict from chain state. There's no ECDSA
        # signature stored on-chain (only the hash/pin/oracle/timestamp are),
        # so signature re-verification is skipped below and authenticity
        # instead rests on onlyOracle having gated the mint.
        seal = {
            "oracle_ens":       ORACLE_ENS,
            "oracle_address":   (onchain_report.get("oracle_address") or ORACLE_ADDRESS),
            "pin":              onchain_report.get("pin", ""),
            "site_address":     "",
            "generated_at":     onchain_report.get("generated_at", ""),
            "oracle_signature": "",
            "nft_token_id":     onchain_report.get("token_id"),
        }

    # Re-verify ECDSA signature when we have one (local-registry path)
    valid = False
    if ORACLE_PRIVATE_KEY and seal.get("oracle_signature"):
        try:
            msg       = encode_defunct(text=f"Togibox Report\n{report_hash}")
            recovered = Account.recover_message(msg, signature=seal["oracle_signature"])
            valid     = recovered.lower() == ORACLE_ADDRESS
        except Exception:
            valid = False
    elif onchain_report is not None:
        # Chain-only fallback path: no signature to re-check, but the
        # receipt could only have been minted by the onlyOracle-gated
        # contract call, so its on-chain presence is itself the proof.
        valid = True

    resp: dict = {
        "valid":          valid,
        "report_hash":    report_hash,
        "oracle_ens":     seal["oracle_ens"],
        "oracle_address": seal["oracle_address"],
        "pin":            seal["pin"],
        "site_address":   seal["site_address"],
        "generated_at":   seal["generated_at"],
        "message":        "Authentic Togibox report" if valid else "Signature verification failed",
    }

    # On-chain NFT receipt
    if seal.get("nft_token_id") is not None:
        resp["nft_receipt"] = {
            "token_id":     seal.get("nft_token_id"),
            "tx_hash":      seal.get("nft_tx_hash"),
            "explorer_url": seal.get("nft_explorer"),
        }

    return resp
