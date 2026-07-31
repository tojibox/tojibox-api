"""
/api/oracle/pending-events   <- called by the pipeline/committer (togibox-scraper)
/api/oracle/events           <- full change event log

Both are backed by live Postgres (store.py), filtered on the GIWA-specific
giwa_committed_at column — this DB is shared with a separate Hedera-based
version of this project, which tracks its own commit status on a plain
committed_at column for the same rows. See store.py's module docstring.
"""
import hashlib
from fastapi import APIRouter, Query
from store import get_pending_events, list_events as store_list_events

router = APIRouter(tags=["events"])


def _leaf_hash(event: dict) -> str:
    raw = "|".join([
        str(event.get("id", "")),
        str(event.get("event_type", "")),
        str(event.get("petition_number") or event.get("pin") or ""),
        str(event.get("detected_at", "")),
        str(event.get("after_state") or ""),
    ])
    return "0x" + hashlib.sha256(raw.encode()).hexdigest()


@router.get("/pending-events")
def get_pending_events_route(limit: int = Query(500, le=1000)):
    rows = get_pending_events(limit=limit)
    events = []
    for ev in rows:
        row = dict(ev)
        row["leaf_hash"] = _leaf_hash(ev)
        events.append(row)
    return {"count": len(events), "events": events}


@router.get("/events")
def list_events(
    event_type: str = Query(None),
    committed: bool = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
):
    total, events = store_list_events(
        event_type=event_type, committed=committed, limit=limit, offset=offset
    )
    return {"total": total, "limit": limit, "offset": offset, "events": events}
