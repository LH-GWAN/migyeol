from __future__ import annotations

import re
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from ..config import STATUSES, get_settings
from ..db.models import Event, Report
from ..db.session import get_session
from ..pipeline.review import recompute_needs_review
from ..rules.orgs import canonical_org
from . import serializers as ser
from .deps import event_or_404

router = APIRouter(prefix="/api/events", tags=["events"])

_NEWS_ID_RE = re.compile(r"^\d{8}\.\d{10,30}$")


@router.get("")
def list_events(
    status: str | None = None,
    region: str | None = None,
    incident_type: str | None = None,
    needs_check_q: bool | None = Query(default=None, alias="needs_check"),
    org: str | None = Query(default=None, max_length=100),  # v2: 정규화 기관 키 (대시보드 기관 링크)
    followup: bool | None = Query(default=None),  # v2: true = 후속보도 확인됨(완료·진행), false = 그 밖
    sort: Literal["priority", "recent", "occurred"] = "priority",
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_session),
):
    settings = get_settings()
    today = settings.today()
    if status is not None and status not in STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {list(STATUSES)}")
    events = session.query(Event).filter(Event.active.is_(True)).all()
    cards = {ev.id: ser.event_card(session, ev, today) for ev in events}
    org_key = (org or "").strip()
    followup_statuses = {STATUSES[0], STATUSES[1]}  # 조치 완료 근거 확인 · 조치 진행 정황 확인
    rows = []
    for ev in events:
        card = cards[ev.id]
        if status and ev.status != status:
            continue
        if org_key and (canonical_org(ev.responsible_org) or "기관 미상") != org_key:
            continue
        if followup is not None and (ev.status in followup_statuses) != followup:
            continue
        if region and ev.region != region:
            continue
        if incident_type and not ev.incident_type.startswith(incident_type):
            continue
        if needs_check_q is not None and card["needs_check"] != needs_check_q:
            continue
        rows.append(ev)
    if sort == "priority":
        rows.sort(key=lambda e: (-cards[e.id]["priority_score"], e.occurred_at))
    elif sort == "recent":
        # 후속 기사가 없는 사건은 뒤로
        rows.sort(key=lambda e: (e.last_followup_at is not None, e.last_followup_at or date.min), reverse=True)
    else:
        rows.sort(key=lambda e: e.occurred_at, reverse=True)
    total = len(rows)
    page_rows = rows[(page - 1) * size : page * size]
    return {
        "items": [cards[e.id] for e in page_rows],
        "total": total,
        "page": page,
        "size": size,
        "filters": {
            "statuses": list(STATUSES),
            "regions": sorted({e.region for e in events if e.region}),
            "incident_types": sorted({e.incident_type for e in events if e.incident_type}),
            "orgs": sorted({canonical_org(e.responsible_org) or "기관 미상" for e in events}),
        },
    }


@router.get("/{event_id}")
def get_event(ev: Event = Depends(event_or_404), session: Session = Depends(get_session)):
    return ser.event_detail(session, ev, get_settings().today())


@router.get("/{event_id}/evidence")
def get_evidence(ev: Event = Depends(event_or_404), session: Session = Depends(get_session)):
    return ser.evidence_out(session, ev, get_settings().today())


@router.get("/{event_id}/trend")
def get_trend(ev: Event = Depends(event_or_404), session: Session = Depends(get_session)):
    return ser.trend_out(ser.latest_trend(session, ev), ev, get_settings().today())


class ReportIn(BaseModel):
    type: Literal["wrong_link", "wrong_status", "wrong_promise", "other"]
    news_id: str | None = Field(default=None, max_length=64)
    comment: str = Field(default="", max_length=2000)

    @field_validator("comment", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v

    @field_validator("news_id", mode="before")
    @classmethod
    def _news_id(cls, v: object) -> object:
        if v is None or v == "":
            return None
        if not isinstance(v, str) or not _NEWS_ID_RE.match(v):
            raise ValueError("news_id 형식이 아니다 (예: 01100101.20250101120000001)")
        return v


@router.post("/{event_id}/reports", status_code=201)
def create_report(body: ReportIn, ev: Event = Depends(event_or_404), session: Session = Depends(get_session)):
    if body.news_id and all(l.news_id != body.news_id for l in ev.links):
        raise HTTPException(status_code=422, detail="news_id 가 이 사건에 연결된 기사가 아니다")
    open_count = session.query(Report).filter_by(event_id=ev.id, resolved=False).count()
    if open_count >= 50:
        raise HTTPException(status_code=429, detail="이 사건의 미처리 신고가 너무 많다. 관리자 처리 후 다시 시도")
    r = Report(event_id=ev.id, news_id=body.news_id, type=body.type, comment=body.comment)
    session.add(r)
    session.flush()
    recompute_needs_review(session, ev)
    session.commit()
    return {"id": r.id, "event_id": ev.id, "type": r.type, "created_at": ser._iso(r.created_at)}
