"""사건의 '검토 필요' 표시를 한 곳에서 계산한다.

needs_review = 검토 대기 연결(저신뢰·다중 사건) OR 미처리 신고 OR 변경이력(tracker) 표시 OR 분류·추출 대기(잠정 판정) OR LLM 2회 실패 항목
link 단계·관리자 처리·신고 생성/처리·tracker·judge 가 모두 이 함수를 호출하므로 서로 덮어쓰지 않는다.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..db.models import Event, Report


def active_llm_failures(event: Event) -> dict[str, int]:
    """활성 연결 기사에 해당하는 실패 항목만 (해제된 기사의 실패는 검토 대상에서 빠진다)."""
    active = {l.news_id for l in event.links if l.is_active}
    qmap = {q.id: q.news_id for q in event.quotations}
    out: dict[str, int] = {}
    for key, n in (event.llm_failures or {}).items():
        if key.startswith("_"):
            continue
        kind, _, ident = key.partition(":")
        nid = ident if kind == "followup" else qmap.get(int(ident)) if ident.isdigit() else None
        if nid in active:
            out[key] = int(n)
    return out


def recompute_needs_review(session: Session, event: Event) -> bool:
    from .extract import count_pending_quotations

    pending_links = any(l.needs_review and not l.rejected for l in event.links)
    open_reports = session.query(Report).filter_by(event_id=event.id, resolved=False).count() > 0
    event.pending_quotations = count_pending_quotations(event)  # 연결 해제·강등으로 바뀐다 — 영구 잠정 방지
    failures = active_llm_failures(event)
    acked = bool((event.llm_failures or {}).get("_acked"))
    llm_gave_up = any(v >= 2 for v in failures.values()) and not acked  # 2회 실패 → 불명확 기록 + 검토 큐 (명세 §8). 관리자 '확인함' 으로 해제
    event.needs_review = bool(
        pending_links or open_reports or event.change_flagged or (event.pending_followups or 0) > 0 or event.pending_quotations > 0 or llm_gave_up
    )
    return event.needs_review
