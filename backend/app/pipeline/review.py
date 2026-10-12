"""사건의 '검토 필요' 표시를 한 곳에서 계산한다.

needs_review = 검토 대기 연결(저신뢰·다중 사건) OR 미처리 신고 OR 변경이력(tracker) 표시 OR 분류·추출 대기(잠정 판정) OR LLM 2회 실패 항목
link 단계·관리자 처리·신고 생성/처리·tracker·judge 가 모두 이 함수를 호출하므로 서로 덮어쓰지 않는다.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..db.models import Event, Report


def recompute_needs_review(session: Session, event: Event) -> bool:
    pending_links = any(l.needs_review and not l.rejected for l in event.links)
    open_reports = session.query(Report).filter_by(event_id=event.id, resolved=False).count() > 0
    llm_gave_up = any(v >= 2 for v in (event.llm_failures or {}).values())  # 2회 실패 → 불명확 기록 + 검토 큐 (명세 §8)
    event.needs_review = bool(
        pending_links or open_reports or event.change_flagged or (event.pending_followups or 0) > 0 or (event.pending_quotations or 0) > 0 or llm_gave_up
    )
    return event.needs_review
