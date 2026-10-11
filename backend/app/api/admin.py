from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import Article, Event, EventArticle, Report
from ..db.session import get_session
from ..pipeline.judge import rejudge_event
from ..pipeline.review import recompute_needs_review
from . import serializers as ser
from .deps import MAX_ID, require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _link_row(session: Session, ev: Event, l: EventArticle) -> dict:
    return {
        "event_id": ev.id,
        "event_title": ev.title,
        "article": ser.article_out(session.get(Article, l.news_id), l),
        "link_score": l.link_score,
        "link_reason": l.link_reason,
    }


@router.get("/review-queue")
def review_queue(session: Session = Depends(get_session)):
    today = get_settings().today()
    events = session.query(Event).filter(Event.active.is_(True)).all()
    links = [_link_row(session, ev, l) for ev in events for l in ev.links if l.needs_review and not l.rejected]
    rejected = [_link_row(session, ev, l) for ev in events for l in ev.links if l.rejected]
    reports = session.query(Report).filter_by(resolved=False).order_by(Report.id.desc()).all()
    by_id = {e.id: e for e in events}
    return {
        "events": [ser.event_card(session, e, today) for e in events if e.needs_review],
        "links": links,
        "rejected_links": rejected,
        "reports": [
            {
                "id": r.id,
                "event_id": r.event_id,
                "event_title": by_id[r.event_id].title if r.event_id in by_id else "",
                "type": r.type,
                "news_id": r.news_id,
                "comment": r.comment,
                "created_at": ser._iso(r.created_at),
                "resolved": r.resolved,
            }
            for r in reports
            if r.event_id in by_id
        ],
    }


class LinkAction(BaseModel):
    action: Literal["confirm", "reject", "restore"]


@router.post("/links/{event_id}/{news_id}")
def link_action(
    body: LinkAction,
    event_id: int = Path(..., ge=1, le=MAX_ID),
    news_id: str = Path(..., max_length=64),
    session: Session = Depends(get_session),
):
    link = session.get(EventArticle, {"event_id": event_id, "news_id": news_id})
    if link is None:
        raise HTTPException(status_code=404, detail="link not found")
    link.link_method = "manual"
    if body.action == "confirm":
        link.confirmed, link.needs_review, link.rejected = True, False, False
    elif body.action == "reject":
        link.confirmed, link.needs_review, link.rejected = False, False, True
    else:  # restore: 해제를 되돌려 검토 큐로
        link.confirmed, link.needs_review, link.rejected = False, True, False
    ev = session.get(Event, event_id)
    if ev is not None:
        recompute_needs_review(session, ev)
        session.commit()
        # 연결이 바뀌면 근거도 바뀐다 — LLM 없이 규칙 판정·우선순위·단계만 다시 계산
        rejudge_event(session, ev, get_settings(), get_settings().today())
    session.commit()
    return {"event_id": event_id, "news_id": news_id, "link_method": "manual", "confirmed": link.confirmed, "rejected": link.rejected}


@router.post("/reports/{report_id}/resolve")
def resolve_report(report_id: int = Path(..., ge=1, le=MAX_ID), session: Session = Depends(get_session)):
    r = session.get(Report, report_id)
    if r is None:
        raise HTTPException(status_code=404, detail="report not found")
    r.resolved = True
    session.flush()  # autoflush=False — recompute 의 미처리 신고 count 가 이 변경을 보도록
    ev = session.get(Event, r.event_id)
    if ev is not None:
        recompute_needs_review(session, ev)
    session.commit()
    return {"id": r.id, "resolved": True}


@router.post("/events/{event_id}/clear-change-flag")
def clear_change_flag(event_id: int = Path(..., ge=1, le=MAX_ID), session: Session = Depends(get_session)):
    """변경이력(tracker) 검토를 마쳤을 때 표시를 내린다."""
    ev = session.get(Event, event_id)
    if ev is None:
        raise HTTPException(status_code=404, detail=f"event {event_id} not found")
    ev.change_flagged = False
    recompute_needs_review(session, ev)
    session.commit()
    return {"event_id": event_id, "needs_review": ev.needs_review}
