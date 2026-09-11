"""뉴스 변경이력(changeTracker) 확인 — 근거로 쓰인 기사가 수정(Update)·삭제(Cancelled)되면 사건을 재검토 대상으로 표시한다."""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from ..bigkinds.protocol import BigKindsClient
from ..bigkinds.schemas import ChangeTrackerArgument
from ..db.models import Article, Event, Promise, StatusJudgment

log = logging.getLogger("migyeol.tracker")


def evidence_news_ids(session: Session, event: Event) -> set[str]:
    ids = {p.evidence_news_id for p in event.promises if p.evidence_news_id}
    latest = session.query(StatusJudgment).filter_by(event_id=event.id).order_by(StatusJudgment.id.desc()).first()
    if latest:
        ids.update(e.get("news_id") for e in (latest.evidence or []) if e.get("news_id"))
    return ids


def check_changes(session: Session, client: BigKindsClient, now: datetime, interval: str = "DAY_1") -> dict:
    stats = {"pages": 0, "items": 0, "matched": 0, "updated": 0, "cancelled": 0, "events_flagged": []}
    page = 1
    touched: dict[str, str] = {}
    while True:
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
        if len(res.items) < res.size or page * res.size >= res.total_count:
            break
        page += 1

    if touched:
        for event in session.query(Event).all():
            hit = evidence_news_ids(session, event) & set(touched)
            if hit:
                event.needs_review = True
                stats["events_flagged"].append({"event": event.key, "news_ids": sorted(hit)})
    session.commit()
    log.info("tracker %s", stats)
    return stats
