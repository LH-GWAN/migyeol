from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import Article, Event, EventArticle, Report
from ..db.session import get_session
from . import serializers as ser

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/review-queue")
def review_queue(session: Session = Depends(get_session)):
    today = get_settings().today()
    events = session.query(Event).all()
    links = []
    for ev in events:
        for l in ev.links:
            if l.needs_review and not l.rejected:
                links.append(
                    {
                        "event_id": ev.id,
                        "event_title": ev.title,
                        "article": ser.article_out(session.get(Article, l.news_id), l),
                        "link_score": l.link_score,
                        "link_reason": l.link_reason,
                    }
                )
    reports = session.query(Report).filter_by(resolved=False).order_by(Report.id.desc()).all()
    by_id = {e.id: e for e in events}
    return {
        "events": [ser.event_card(session, e, today) for e in events if e.needs_review],
        "links": links,
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
        ],
    }


class LinkAction(BaseModel):
    action: Literal["confirm", "reject"]


@router.post("/links/{event_id}/{news_id}")
def link_action(event_id: int, news_id: str, body: LinkAction, session: Session = Depends(get_session)):
    link = session.get(EventArticle, {"event_id": event_id, "news_id": news_id})
    if link is None:
        raise HTTPException(status_code=404, detail="link not found")
    link.link_method = "manual"
    if body.action == "confirm":
        link.confirmed = True
        link.needs_review = False
        link.rejected = False
    else:
        link.rejected = True
        link.needs_review = False
        link.confirmed = False
    ev = session.get(Event, event_id)
    if ev is not None:
        ev.needs_review = any(l.needs_review for l in ev.links if not l.rejected)
    session.commit()
    return {"event_id": event_id, "news_id": news_id, "link_method": "manual", "confirmed": link.confirmed, "rejected": link.rejected}


@router.post("/reports/{report_id}/resolve")
def resolve_report(report_id: int, session: Session = Depends(get_session)):
    r = session.get(Report, report_id)
    if r is None:
        raise HTTPException(status_code=404, detail="report not found")
    r.resolved = True
    session.commit()
    return {"id": r.id, "resolved": True}
