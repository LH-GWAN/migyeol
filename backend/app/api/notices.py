"""v2 §7.3 공공기관 게시판 확인 API — 트랙 C.

GET  /api/notices/events/{event_id}  사건의 기관 발표 확인 결과 (공개, 외부 요청 없음 — 저장된 결과만)
POST /api/notices/run                관리자 토큰. {event_id?} 트리거된 사건에 대해 확인 실행 (scripts/run_notices.py 와 같은 함수)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import DISCLAIMER, get_settings
from ..db.models import Event
from ..db.session import get_session
from ..notices.service import NoticesBusy, notices_out, run_checks
from .deps import MAX_ID, event_or_404, require_admin

router = APIRouter(prefix="/api/notices", tags=["notices"])

NOTICE_NOTE = "기관 게시판에서 찾은 항목은 보조 근거이며 사건 상태(5종)를 바꾸지 않습니다."


@router.get("/events/{event_id}")
def get_event_notices(event: Event = Depends(event_or_404), session: Session = Depends(get_session)):
    out = notices_out(session, event, get_settings())
    out["note"] = NOTICE_NOTE
    out["disclaimer"] = DISCLAIMER
    return out


class NoticeRunIn(BaseModel):
    event_id: int | None = Field(default=None, ge=1, le=MAX_ID)


@router.post("/run", dependencies=[Depends(require_admin)])
def run_notices(body: NoticeRunIn | None = None, session: Session = Depends(get_session)):
    event_id = body.event_id if body else None
    if event_id is not None:
        ev = session.get(Event, event_id)
        if ev is None or not ev.active:
            raise HTTPException(status_code=404, detail=f"event {event_id} not found")
    try:
        out = run_checks(session, get_settings(), event_id=event_id)
    except NoticesBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    out["results"] = [
        {k: r[k] for k in ("event_id", "title", "org", "triggered", "reason", "outcome", "items_found", "requests")} for r in out["results"]
    ]
    return out
