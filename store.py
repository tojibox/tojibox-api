"""
Data store for the Togibox oracle API.

Parcel/petition lookups (used by routes/parcels.py) query the live
Postgres DB directly — see db.py. The JSON files below remain as an
in-memory cache for routes/events.py, which reads the full change-event /
petition log on every request.

NOTE: these two JSON files (rezoning_petitions.json, change_events.json)
ideally would be read from Postgres too, same as the parcel/petition
lookups below. Ported as-is from ZoneProof's oracle/api/store.py, which
has this same split — not fixed here, kept behavior identical to the
source. Populate them by copying togibox-scraper's exported JSON here if
you want /events to serve real data; otherwise this degrades gracefully
to empty lists (see _load()).

Column names below (evm_tx_hash / evm_block on merkle_batches) match the
renamed schema used by togibox-scraper's migrations (renamed from
hedera_evm_tx_hash / hedera_evm_block in the ZoneProof/Hedera schema).
"""
import json
import os

from db import query as db_query

_BASE = os.path.dirname(__file__)  # togibox-api/


def _load(filename):
    path = os.path.join(_BASE, filename)
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[store] WARNING: could not load {filename}: {e}")
        return []


# ── Loaded once at import time — used by routes/events.py only ───────────────
_petitions_raw      = _load("rezoning_petitions.json")
_change_events_raw  = _load("change_events.json")

REZONING_PETITIONS = _petitions_raw

print(f"[store] Loaded  petitions={len(REZONING_PETITIONS):,}  "
      f"change_events={len(_change_events_raw):,}")


# ── Public API used by routes/parcels.py — backed by live Postgres ───────────

_COMMITTED_EVENT_TYPES = ("new_petition", "petition_status_change", "petition_vote_change")


def search_parcels_by_address(query: str, limit: int = 10):
    """Case-insensitive partial match on site_address."""
    rows = db_query(
        """
        SELECT pin, site_address, city, zipcode, owner, total_value_assd
        FROM parcels
        WHERE site_address ILIKE %s
        LIMIT %s
        """,
        (f"%{query.strip()}%", limit),
    )
    return rows


def get_parcel(pin: str):
    rows = db_query("SELECT * FROM parcels WHERE pin = %s LIMIT 1", (pin,))
    return rows[0] if rows else None


def get_parcel_history_peek(pin: str):
    """Free preview — returns only the petition count, no details."""
    if not db_query("SELECT 1 FROM parcels WHERE pin = %s LIMIT 1", (pin,)):
        return None

    petitions = db_query(
        "SELECT petition_number FROM rezoning_petitions WHERE %s = ANY(pins)", (pin,)
    )
    petition_numbers = [p["petition_number"] for p in petitions]

    on_chain = 0
    if petition_numbers:
        rows = db_query(
            """
            SELECT COUNT(DISTINCT petition_number) AS c
            FROM change_events
            WHERE petition_number = ANY(%s)
              AND committed_at IS NOT NULL
              AND event_type IN %s
            """,
            (petition_numbers, _COMMITTED_EVENT_TYPES),
        )
        on_chain = rows[0]["c"] if rows else 0

    return {"total_petitions": len(petition_numbers), "on_chain_count": on_chain}


def get_parcel_history(pin: str):
    parcel_rows = db_query("SELECT * FROM parcels WHERE pin = %s LIMIT 1", (pin,))
    if not parcel_rows:
        return None
    parcel = parcel_rows[0]

    # All petitions that list this PIN, most recent meeting first
    petitions = db_query(
        """
        SELECT petition_number, current_zoning, proposed_zoning, status, vote_result,
               action, meeting_date, meeting_type, address AS petition_address,
               legislation_url, file_number, first_seen_at
        FROM rezoning_petitions
        WHERE %s = ANY(pins)
        ORDER BY meeting_date DESC NULLS LAST
        """,
        (pin,),
    )
    petition_numbers = [p["petition_number"] for p in petitions]

    # Most-recent committed change_event per petition
    latest_events = {}
    if petition_numbers:
        rows = db_query(
            """
            SELECT DISTINCT ON (petition_number)
                petition_number, batch_id, committed_at, event_type, evm_snapshot_index
            FROM change_events
            WHERE petition_number = ANY(%s)
              AND committed_at IS NOT NULL
              AND event_type IN %s
            ORDER BY petition_number, committed_at DESC
            """,
            (petition_numbers, _COMMITTED_EVENT_TYPES),
        )
        latest_events = {r["petition_number"]: r for r in rows}

    # Join merkle_batches for GIWA TX details
    batch_ids = [str(e["batch_id"]) for e in latest_events.values() if e.get("batch_id")]
    batches = {}
    if batch_ids:
        rows = db_query(
            "SELECT batch_id, evm_tx_hash, evm_block FROM merkle_batches WHERE batch_id = ANY(%s::uuid[])",
            (batch_ids,),
        )
        batches = {str(r["batch_id"]): r for r in rows}

    results = []
    for p in petitions:
        row = {
            "petition_number":  p["petition_number"],
            "current_zoning":   p["current_zoning"],
            "proposed_zoning":  p["proposed_zoning"],
            "status":           p["status"],
            "vote_result":      p["vote_result"],
            "action":           p["action"],
            "meeting_date":     p["meeting_date"],
            "meeting_type":     p["meeting_type"],
            "petition_address": p["petition_address"],
            "legislation_url":  p["legislation_url"],
            "file_number":      p["file_number"],
            "first_seen_at":    p["first_seen_at"],
            # on-chain fields — populated below if change_event exists
            "batch_id":             None,
            "committed_at":         None,
            "event_type":           None,
            "evm_snapshot_index":   None,
            "evm_tx_hash":          None,
            "evm_block":            None,
        }

        latest = latest_events.get(p["petition_number"])
        if latest:
            row["batch_id"]           = latest["batch_id"]
            row["committed_at"]       = latest["committed_at"]
            row["event_type"]         = latest["event_type"]
            row["evm_snapshot_index"] = latest["evm_snapshot_index"]

            batch = batches.get(str(latest["batch_id"])) if latest.get("batch_id") else None
            if batch:
                row["evm_tx_hash"] = batch["evm_tx_hash"]
                row["evm_block"]   = batch["evm_block"]

        results.append(row)

    on_chain = [r for r in results if r["committed_at"]]

    return {
        "parcel":           parcel,
        "rezoning_history": results,
        "total_petitions":  len(results),
        "on_chain_count":   len(on_chain),
    }
