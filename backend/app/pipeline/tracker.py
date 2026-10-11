"""뉴스 변경이력(changeTracker) 확인 — 근거로 쓰인 기사가 수정(Update)·삭제(Cancelled)되면 사건을 재검토 대상으로 표시한다.

- Cancelled: 그 기사를 근거로 한 약속은 추적 대상에서 빼고, 그 기사의 후속 신호는 지운 뒤 규칙 판정을 다시 한다.
- Update: 기사를 다시 조회해 제목·하이라이트를 갱신하고, 그 기사의 후속 신호를 지워 다음 judge 에서 재분류되게 한다.
- 사건에는 change_flagged 를 세우고 needs_review 를 재계산한다 (link·관리자 처리가 덮어쓰지 않는다).
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from sqlalchemy.orm import Session

from ..bigkinds.protocol import BigKindsClient
from ..bigkinds.schemas import ChangeTrackerArgument
from ..config import Settings
from ..db.models import Article, Event, FollowupSignal, StatusJudgment
from .review import recompute_needs_review

log = logging.getLogger("migyeol.tracker")
MAX_PAGES = 50


def evidence_news_ids(session: Session, event: Event) -> set[str]:
    ids = {p.evidence_news_id for p in event.promises if p.evidence_news_id}
    latest = session.query(StatusJudgment).filter_by(event_id=event.id).order_by(StatusJudgment.id.desc()).first()
    if latest:
        ids.update(e.get("news_id") for e in (latest.evidence or []) if e.get("news_id"))
    return ids


def check_changes(
    session: Session,
    client: BigKindsClient,
    now: datetime,
    interval: str = "DAY_1",
    settings: Settings | None = None,
    today: date | None = None,
    llm=None,
    call_budget: dict | None = None,
) -> dict:
    from .collect import upsert_article
    from .judge import classify_followups, rejudge_event

    stats = {"pages": 0, "items": 0, "matched": 0, "updated": 0, "cancelled": 0, "events_flagged": [], "rejudged": []}
    page = 1
    touched: dict[str, str] = {}
    while page <= MAX_PAGES:
        res = client.change_tracker(ChangeTrackerArgument(**{"from": now.strftime("%Y-%m-%d %H:%M:%S"), "interval": interval, "offset": str(page)}))
        stats["pages"] += 1
        stats["items"] += len(res.items)
        for item in res.items:
            art = session.get(Article, item.newsitem_id)
            if art is None:
                continue
            stats["matched"] += 1
            status = "cancelled" if item.news_status == "Cancelled" else "updated"
            art.change_status = status
            art.change_checked_at = now
            touched[art.news_id] = status
            stats[status] += 1
        if not res.items or len(res.items) < res.size or page * res.size >= res.total_count:
            break
        page += 1
    session.commit()
    if not touched:
        log.info("tracker %s", stats)
        return stats

    # 수정된 기사는 다시 조회해 갱신한다 (본문 범위는 API 반환값 그대로)
    updated_ids = [nid for nid, st in touched.items() if st == "updated"]
    if updated_ids:
        try:
            for doc in client.get_news_by_ids(updated_ids).documents:
                art = session.get(Article, doc.news_id)
                if art is not None:
                    upsert_article(session, doc, None)  # 제목·분류 갱신. 하이라이트는 새 응답에 있을 때만 바뀐다
        except Exception as exc:  # noqa: BLE001
            log.warning("변경 기사 재조회 실패: %s", exc)

    for event in session.query(Event).all():
        hit = evidence_news_ids(session, event) & set(touched)
        linked_hit = {l.news_id for l in event.links} & set(touched)
        if not hit and not linked_hit:
            continue
        for nid in linked_hit:
            # 바뀐 기사의 후속 신호는 지워 다음 judge 에서 다시 분류한다
            for s in session.query(FollowupSignal).filter_by(event_id=event.id, news_id=nid).all():
                session.delete(s)
            if touched[nid] == "cancelled":
                for p in event.promises:
                    if p.evidence_news_id == nid and p.is_trackable:
                        p.is_trackable = False
                        p.rationale = (p.rationale or "") + " [근거 기사 삭제(Cancelled)로 추적 중단]"
        if hit:
            event.change_flagged = True
            stats["events_flagged"].append({"event": event.key, "news_ids": sorted(hit)})
        recompute_needs_review(session, event)
        session.commit()
        if settings is not None and today is not None and linked_hit:
            if llm is not None:
                classify_followups(session, event, llm, settings, call_budget)  # 지운 신호를 바로 다시 분류
            rejudge_event(session, event, settings, today)
            stats["rejudged"].append(event.key)
    session.commit()
    log.info("tracker %s", stats)
    return stats
