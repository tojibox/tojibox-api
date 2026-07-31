"""
Data store for the Togibox oracle API — backed entirely by the live
Postgres DB (see db.py). No JSON-file caching.

This DB is shared with a separate, already-submitted Hedera-based
version of this project (same Supabase project, same parcels/
rezoning_petitions/change_events tables). change_events therefore has
TWO independent sets of "has this been committed on-chain" columns:
  - committed_at / batch_id / evm_snapshot_index   — that project's Hedera
    pipeline; read-only from here, never written by Togibox.
  - giwa_committed_at / giwa_batch_id / giwa_evm_snapshot_index — this
    project's GIWA pipeline (see migrations/009_add_giwa_columns.sql and
    togibox-scraper/pipeline/processor.mjs, which is the only thing that
    writes these). Every query below that means "committed on-chain"
    filters on the giwa_ columns, not the shared ones.

merkle_batches is also shared, but each pipeline always inserts a fresh
batch_id (UUID) per commit, so there's no row-level conflict there — only
evm_tx_hash / evm_block (new, GIWA-specific) vs. hedera_evm_tx_hash /
hedera_evm_block (untouched) distinguish which pipeline created a row.
"""
from db import query as db_query

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
              AND giwa_committed_at IS NOT NULL
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

    # Most-recent GIWA-committed change_event per petition
    latest_events = {}
    if petition_numbers:
        rows = db_query(
            """
            SELECT DISTINCT ON (petition_number)
                petition_number,
                giwa_batch_id           AS batch_id,
                giwa_committed_at       AS committed_at,
                event_type,
                giwa_evm_snapshot_index AS evm_snapshot_index
            FROM change_events
            WHERE petition_number = ANY(%s)
              AND giwa_committed_at IS NOT NULL
              AND event_type IN %s
            ORDER BY petition_number, giwa_committed_at DESC
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


# ── Change-event feed used by routes/events.py — backed by live Postgres ─────
# (the pipeline's GET /pending-events call is what actually drives commits,
# so this has to reflect real, current DB state — not a point-in-time export)

_PENDING_EVENT_TYPES = ("new_petition", "petition_status_change", "petition_vote_change")


def get_pending_events(limit: int = 500):
    """Events not yet committed to GIWA, oldest first — what the pipeline commits next."""
    rows = db_query(
        """
        SELECT ce.id, ce.event_type, ce.county_id, ce.pin, ce.petition_number,
               ce.changed_fields, ce.before_state, ce.after_state, ce.detected_at,
               rp.current_zoning, rp.proposed_zoning, rp.status AS petition_status,
               rp.meeting_date, rp.pins AS affected_pins, rp.address AS petition_address
        FROM change_events ce
        LEFT JOIN rezoning_petitions rp ON rp.petition_number = ce.petition_number
        WHERE ce.giwa_committed_at IS NULL
          AND ce.event_type IN %s
        ORDER BY ce.detected_at ASC
        LIMIT %s
        """,
        (_PENDING_EVENT_TYPES, limit),
    )
    return rows


def list_events(event_type: str = None, committed: bool = None, limit: int = 100, offset: int = 0):
    """Full change-event log, newest first. `committed` filters on GIWA commit status."""
    where  = ["1=1"]
    params = []

    if event_type:
        where.append("event_type = %s")
        params.append(event_type)
    if committed is True:
        where.append("giwa_committed_at IS NOT NULL")
    elif committed is False:
        where.append("giwa_committed_at IS NULL")

    where_sql = " AND ".join(where)

    total_rows = db_query(f"SELECT COUNT(*) AS c FROM change_events WHERE {where_sql}", params)
    total = total_rows[0]["c"] if total_rows else 0

    rows = db_query(
        f"""
        SELECT id, event_type, county_id, pin, petition_number, changed_fields,
               before_state, after_state, detected_at, merkle_leaf_hash,
               giwa_batch_id AS batch_id, giwa_committed_at AS committed_at,
               giwa_evm_snapshot_index AS evm_snapshot_index
        FROM change_events
        WHERE {where_sql}
        ORDER BY detected_at DESC
        LIMIT %s OFFSET %s
        """,
        params + [limit, offset],
    )
    return total, rows
