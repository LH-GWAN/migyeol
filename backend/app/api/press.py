"""v2 §7.2 보도자료 초안 API — 트랙 C. 로그인 없이 공개 (§0 멘토 피드백).

GET /api/press/draft             전체 초안 (scope="all")
GET /api/press/draft?event_id=N  사건별 초안 (scope="event")
응답: {scope, event_id, title, markdown, sections:[{heading, body}], generated_at, sources:[{ref, news_id, title, provider, published_at, link}],
       disclaimer, header_note, writer, stats, ...}
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import Event
from ..db.session import get_session
from ..press import data as D
from ..press.writer import build_writer
from .deps import MAX_ID

router = APIRouter(prefix="/api/press", tags=["press"])


@router.get("/draft")
def get_draft(event_id: int | None = Query(default=None, ge=1, le=MAX_ID), session: Session = Depends(get_session)):
    settings = get_settings()
    today = settings.today()
    writer = build_writer(settings)
    if event_id is None:
        return writer.write_all(D.overview(session, settings, today)).as_dict()
    ev = session.get(Event, event_id)
    if ev is None or not ev.active:
        raise HTTPException(status_code=404, detail=f"event {event_id} not found")
    return writer.write_event(D.event_facts(session, ev, settings, today), today).as_dict()


@router.get("/events")
def list_press_events(session: Session = Depends(get_session)):
    """사건별 초안 선택 목록: [{id, title, status, org, occurred_at, has_followup, notice_found}]."""
    settings = get_settings()
    today = settings.today()
    out = []
    for ev in session.query(Event).filter(Event.active.is_(True)).order_by(Event.occurred_at.desc(), Event.id).all():
        f = D.event_facts(session, ev, settings, today)
        out.append(
            {
                "id": f.id,
                "title": f.title,
                "status": f.status if f.judged else None,
                "org": f.org,
                "occurred_at": f.occurred_at.isoformat(),
                "has_followup": f.has_followup,
                "notice_found": bool(f.notices.get("items")),
            }
        )
    return out
