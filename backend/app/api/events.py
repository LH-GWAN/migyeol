from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import STATUSES, get_settings
from ..db.models import Event, Report
from ..db.session import get_session
from ..pipeline.judge import deadline_passed_days
from ..rules.priority import needs_check
from . import serializers as ser

router = APIRouter(prefix="/api/events", tags=["events"])


def _event_or_404(session: Session, event_id: int) -> Event:
    ev = session.get(Event, event_id)
    if ev is None:
        raise HTTPException(status_code=404, detail=f"event {event_id} not found")
    return ev


@router.get("")
def list_events(
    status: str | None = None,
    region: str | None = None,
    incident_type: str | None = None,
    needs_check_q: bool | None = Query(default=None, alias="needs_check"),
    sort: Literal["priority", "recent", "occurred"] = "priority",
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_session),
):
    settings = get_settings()
    today = settings.today()
    if status is not None and status not in STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {list(STATUSES)}")
    events = session.query(Event).all()
    rows = []
    for ev in events:
        if status and ev.status != status:
            continue
        if region and ev.region != region:
            continue
        if incident_type and not ev.incident_type.startswith(incident_type):
            continue
        if needs_check_q is not None and needs_check(ev.status, deadline_passed_days(ev, today)) != needs_check_q:
            continue
        rows.append(ev)
    if sort == "priority":
        rows.sort(key=lambda e: (-e.priority_score, e.occurred_at))
    elif sort == "recent":
        rows.sort(key=lambda e: (e.last_followup_at or e.occurred_at), reverse=True)
    else:
        rows.sort(key=lambda e: e.occurred_at, reverse=True)
    total = len(rows)
    page_rows = rows[(page - 1) * size : page * size]
    return {
        "items": [ser.event_card(session, e, today) for e in page_rows],
        "total": total,
        "page": page,
        "size": size,
        "filters": {
            "statuses": list(STATUSES),
            "regions": sorted({e.region for e in events if e.region}),
            "incident_types": sorted({e.incident_type for e in events if e.incident_type}),
        },
    }


@router.get("/{event_id}")
def get_event(event_id: int, session: Session = Depends(get_session)):
    ev = _event_or_404(session, event_id)
    return ser.event_detail(session, ev, get_settings().today())


@router.get("/{event_id}/evidence")
def get_evidence(event_id: int, session: Session = Depends(get_session)):
    ev = _event_or_404(session, event_id)
    return ser.evidence_out(session, ev, get_settings().today())


@router.get("/{event_id}/trend")
def get_trend(event_id: int, session: Session = Depends(get_session)):
    ev = _event_or_404(session, event_id)
    return ser.trend_out(ser.latest_trend(session, ev), ev, get_settings().today())


class ReportIn(BaseModel):
    type: Literal["wrong_link", "wrong_status", "wrong_promise", "other"]
    news_id: str | None = None
    comment: str = Field(default="", max_length=2000)


@router.post("/{event_id}/reports", status_code=201)
def create_report(event_id: int, body: ReportIn, session: Session = Depends(get_session)):
    ev = _event_or_404(session, event_id)
    r = Report(event_id=ev.id, news_id=body.news_id, type=body.type, comment=body.comment)
    session.add(r)
    ev.needs_review = True
    session.commit()
    return {"id": r.id, "event_id": ev.id, "type": r.type, "created_at": ser._iso(r.created_at)}
