"""공통 의존성: 관리자 토큰, 사건 조회."""

from __future__ import annotations

import hmac

from fastapi import Depends, Header, HTTPException, Path
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import Event
from ..db.session import get_session

MAX_ID = 2**31 - 1  # SQLite INTEGER 범위 안, 실사용 범위


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    """ADMIN_TOKEN 이 설정돼 있으면 X-Admin-Token 헤더가 같아야 한다. 비어 있으면 검사하지 않는다(로컬 전용)."""
    token = get_settings().admin_token
    if token and not hmac.compare_digest(token.encode(), (x_admin_token or "").encode()):
        raise HTTPException(status_code=401, detail="admin token required")


def event_or_404(event_id: int = Path(..., ge=1, le=MAX_ID), session: Session = Depends(get_session)) -> Event:
    ev = session.get(Event, event_id)
    if ev is None or not ev.active:
        raise HTTPException(status_code=404, detail=f"event {event_id} not found")
    return ev
