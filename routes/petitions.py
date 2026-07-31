"""
/api/oracle/petitions              <- list petitions
/api/oracle/petitions/{number}     <- petition + all affected parcels
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from db import query

router = APIRouter(tags=["petitions"])


@router.get("/petitions")
def list_petitions(
    status: Optional[str] = Query(None, description="Filter by status: Approved, Denied, Withdrawn, Active, Pending City Council, Pending Planning Commission"),
    limit: int = Query(50, le=500),
    offset: int = Query(0),
):
    """List rezoning petitions with optional status filter."""
    conditions = ["1=1"]
    params = []

    if status:
        conditions.append("status = %s")
        params.append(status)

    where = " AND ".join(conditions)

    # committed_at here means "committed to GIWA" — this DB is shared with a
    # separate Hedera-based version of this project, which tracks its own
    # commit status on change_events.committed_at (untouched) rather than
    # giwa_committed_at (see store.py's module docstring).
    rows = query(f"""
        SELECT
            petition_number, county_id, status, action,
            current_zoning, proposed_zoning, vote_result,
            meeting_date, address,
            array_length(pins, 1) AS pin_count,
            legislation_url, ce.committed_at
        FROM rezoning_petitions rp
        LEFT JOIN LATERAL (
            SELECT giwa_committed_at AS committed_at FROM change_events
            WHERE petition_number = rp.petition_number
              AND giwa_committed_at IS NOT NULL
            LIMIT 1
        ) ce ON true
        WHERE {where}
        ORDER BY meeting_date DESC NULLS LAST
        LIMIT %s OFFSET %s
    """, params + [limit, offset])

    total = query(f"SELECT COUNT(*) AS n FROM rezoning_petitions WHERE {where}", params)

    return {
        "total": total[0]["n"],
        "limit": limit,
        "offset": offset,
        "petitions": rows,
    }


@router.get("/petitions/{petition_number}")
def get_petition(petition_number: str):
    """
    Full petition details including all affected parcels.
    Each parcel is fetched from the parcels table using pins[].
    """
    petition = query("""
        SELECT *
        FROM rezoning_petitions
        WHERE petition_number = %s
        LIMIT 1
    """, (petition_number,))

    if not petition:
        raise HTTPException(status_code=404, detail=f"Petition {petition_number} not found")

    p = petition[0]
    pins = p.get("pins") or []

    # Fetch all affected parcels from parcels table
    affected_parcels = []
    if pins:
        affected_parcels = query("""
            SELECT pin, site_address, city, owner,
                   total_value_assd, land_class, type_and_use, year_built
            FROM parcels
            WHERE pin = ANY(%s)
            ORDER BY pin
        """, (pins,))

    # On-chain proof of GIWA commitment — giwa_batch_id/giwa_committed_at,
    # not the shared committed_at/batch_id columns a separate Hedera-based
    # version of this project tracks on the same rows (see store.py).
    chain_proof = query("""
        SELECT giwa_batch_id AS batch_id, giwa_committed_at AS committed_at, event_type
        FROM change_events
        WHERE petition_number = %s
          AND giwa_committed_at IS NOT NULL
        ORDER BY giwa_committed_at DESC
    """, (petition_number,))

    return {
        "petition": p,
        "affected_parcels": affected_parcels,
        "total_parcels": len(affected_parcels),
        "on_chain_proof": chain_proof,
    }
