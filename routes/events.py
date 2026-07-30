"""
/api/oracle/pending-events   <- called by the pipeline/committer (togibox-scraper)
/api/oracle/events           <- full change event log
"""
import hashlib
from fastapi import APIRouter, Query
from store import _change_events_raw, REZONING_PETITIONS

router = APIRouter(tags=["events"])

_PETITIONS_BY_NUMBER = {p["petition_number"]: p for p in REZONING_PETITIONS}
_PENDING_TYPES = {"new_petition", "petition_status_change", "petition_vote_change"}


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
def get_pending_events(limit: int = Query(500, le=1000)):
    pending = [
        ev for ev in _change_events_raw
        if not ev.get("committed_at") and ev.get("event_type") in _PENDING_TYPES
    ]
    pending_sorted = sorted(pending, key=lambda e: e.get("detected_at") or "")[:limit]

    rows = []
    for ev in pending_sorted:
        petition = _PETITIONS_BY_NUMBER.get(ev.get("petition_number") or "")
        row = dict(ev)
        row["leaf_hash"] = _leaf_hash(ev)
        if petition:
            row["current_zoning"]   = petition.get("current_zoning")
            row["proposed_zoning"]  = petition.get("proposed_zoning")
            row["petition_status"]  = petition.get("status")
            row["meeting_date"]     = petition.get("meeting_date")
            row["affected_pins"]    = petition.get("pins")
            row["petition_address"] = petition.get("address")
        rows.append(row)

    return {"count": len(rows), "events": rows}


@router.get("/events")
def list_events(
    event_type: str = Query(None),
    committed: bool = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
):
    events = list(_change_events_raw)

    if event_type:
        events = [e for e in events if e.get("event_type") == event_type]
    if committed is True:
        events = [e for e in events if e.get("committed_at")]
    elif committed is False:
        events = [e for e in events if not e.get("committed_at")]

    events = sorted(events, key=lambda e: e.get("detected_at") or "", reverse=True)
    total = len(events)

    return {"total": total, "limit": limit, "offset": offset, "events": events[offset: offset + limit]}
