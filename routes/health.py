from fastapi import APIRouter
from db import query

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    rows = query("""
        SELECT
            (SELECT COUNT(*) FROM parcels)            AS parcels,
            (SELECT COUNT(*) FROM rezoning_petitions) AS petitions,
            (SELECT COUNT(*) FROM change_events)      AS change_events,
            (SELECT COUNT(*) FROM change_events
             WHERE committed_at IS NULL
               AND event_type IN (
                 'new_petition','petition_status_change','petition_vote_change'
               ))                                     AS pending_rezoning_events
    """)
    return {"status": "ok", "counts": rows[0]}
