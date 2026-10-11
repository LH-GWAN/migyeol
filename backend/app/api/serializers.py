"""DB 모델 -> API 응답 (docs/API_CONTRACT.md). 기사 응답에는 전문이 없다 — hilight(≤200자)와 외부 링크만.

- 근거·근거 수에는 활성 연결(해제·검토 대기 제외)·삭제되지 않은 기사·현재 prompt_version 신호만 들어간다.
- counted = 최신 판정 규칙이 채택한 신호(StatusJudgment.counted_signal_ids). considered = R5 참고 신호.
- 카드의 '발표된 기한' 과 '기한 경과 N일' 은 같은 약속(기한이 지난 약속 우선, 없으면 확약 > 계획)을 가리킨다.
- 판정 이력이 없는 사건은 사유를 '아직 상태 판정이 실행되지 않음' 으로 명시한다.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy.orm import Session

from ..config import DISCLAIMER, PHASES, STATUSES, UNJUDGED_TEXT, get_settings
from ..db.models import Article, Event, EventArticle, Promise, Quotation, StatusJudgment, TrendSnapshot
from ..llm.client import PROMPT_VERSIONS
from ..pipeline.judge import overdue_promise, trackable_promises
from ..pipeline.scheduler import next_scheduled_run
from ..rules import priority as priority_rules
from ..rules.priority import needs_check


def _iso(d: date | datetime | None) -> str | None:
    if d is None:
        return None
    if isinstance(d, datetime):
        if d.tzinfo is not None:
            return d.replace(microsecond=0).isoformat()
        return d.replace(microsecond=0).isoformat() + "+09:00"  # 저장값은 KST 벽시계(naive)
    return d.isoformat()


def article_out(article: Article | None, link: EventArticle | None = None) -> dict | None:
    if article is None:
        return None
    return {
        "news_id": article.news_id,
        "title": article.title,
        "provider": article.provider,
        "published_at": _iso(article.published_at),
        "provider_link_page": article.provider_link_page,
        "hilight": (article.hilight or article.content_200 or "")[:200],
        "byline": article.byline,
        "phase": link.phase if link else None,
        "link_method": link.link_method if link else None,
        "link_score": link.link_score if link else None,
        "link_reason": link.link_reason if link else None,
        "change_status": article.change_status,
    }


def active_links(event: Event) -> list[EventArticle]:
    return [l for l in event.links if l.is_active]


def latest_judgment(session: Session, event: Event) -> StatusJudgment | None:
    return session.query(StatusJudgment).filter_by(event_id=event.id).order_by(StatusJudgment.id.desc()).first()


def latest_trend(session: Session, event: Event) -> TrendSnapshot | None:
    return session.query(TrendSnapshot).filter_by(event_id=event.id).order_by(TrendSnapshot.id.desc()).first()


def primary_promise(event: Event, today: date) -> Promise | None:
    """카드 대표 약속: 기한이 지난 약속 > 확약 > 계획. 기한 표시와 경과 표시가 같은 약속을 가리키게 한다."""
    overdue = overdue_promise(event, today)
    if overdue is not None:
        return overdue
    ps = trackable_promises(event)
    firm = [p for p in ps if p.strength == "확약"]
    return (firm or ps or [None])[0]


def promise_out(session: Session, p: Promise, event: Event, today: date) -> dict:
    q: Quotation | None = p.quotation
    link = next((l for l in event.links if l.news_id == p.evidence_news_id), None)
    return {
        "id": p.id,
        "actor_org": p.actor_org,
        "actor_raw": p.actor_raw,
        "action": p.action,
        "deadline_raw": p.deadline_raw,
        "deadline_date": _iso(p.deadline_date),
        "deadline_precision": p.deadline_precision,
        "strength": p.strength,
        "is_trackable": p.is_trackable,
        "merged_into_id": p.merged_into_id,
        "confidence": p.confidence,
        "deadline_passed": bool(p.deadline_date and p.deadline_date < today),
        "quotation": (
            {
                "news_id": q.news_id,
                "source": q.source,
                "quotation": q.quotation,
                "provider": q.provider,
                "published_at": _iso(q.published_at),
            }
            if q
            else None
        ),
        "evidence_article": article_out(session.get(Article, p.evidence_news_id), link),
        "merged_news_ids": list(p.merged_news_ids or []),
    }


def trend_out(snapshot: TrendSnapshot | None, event: Event, today: date) -> dict:
    pp = primary_promise(event, today)
    return {
        "interval": snapshot.interval if snapshot else "month",
        "series": list(snapshot.series or []) if snapshot else [],
        "peak_label": snapshot.peak_label if snapshot else None,
        "peak_hits": snapshot.peak_hits if snapshot else 0,
        "recent_mean": snapshot.recent_mean if snapshot else 0.0,
        "drop_ratio": snapshot.drop_ratio if snapshot else 0.0,
        "markers": {
            "occurred_at": _iso(event.occurred_at),
            "deadline_date": _iso(pp.deadline_date) if pp else None,
            "deadline_precision": pp.deadline_precision if pp and pp.deadline_date else None,
            "today": today.isoformat(),
        },
    }


def _current_signals(event: Event) -> list:
    version = PROMPT_VERSIONS["followup"]
    return sorted((s for s in event.signals if s.prompt_version == version), key=lambda s: s.id)


def evidence_news_ids(session: Session, event: Event, j: StatusJudgment | None) -> set[str]:
    """근거 기사 = 추적 약속의 근거 기사(활성 연결) + 최신 판정이 채택한 신호의 기사."""
    active = {l.news_id for l in active_links(event)}
    ids = {p.evidence_news_id for p in trackable_promises(event) if p.evidence_news_id in active}
    counted = set(j.counted_signal_ids or []) if j else set()
    ids |= {s.news_id for s in _current_signals(event) if s.id in counted and s.news_id in active}
    return ids


def live_priority(session: Session, event: Event, today: date) -> float:
    """요청 시점 기준으로 우선순위를 다시 계산한다 (재판정 없이 날짜가 지나도 정렬과 배지가 어긋나지 않게)."""
    trend = latest_trend(session, event)
    passed_p = overdue_promise(event, today)
    days_since = (today - event.last_followup_at).days if event.last_followup_at else None
    return priority_rules.score(
        status=event.status,
        deadline_passed_days=(today - passed_p.deadline_date).days if passed_p else None,
        days_since_last_followup=days_since,
        drop_ratio=trend.drop_ratio if trend else None,
        has_firm_promise=any(p.strength == "확약" for p in trackable_promises(event)),
    )


def event_card(session: Session, event: Event, today: date) -> dict:
    pp = primary_promise(event, today)
    passed = (today - pp.deadline_date).days if pp and pp.deadline_date and pp.deadline_date < today else None
    days_since = (today - event.last_followup_at).days if event.last_followup_at else None
    trend = latest_trend(session, event)
    j = latest_judgment(session, event)
    return {
        "id": event.id,
        "title": event.title,
        "incident_type": event.incident_type,
        "region": event.region,
        "facility": event.facility,
        "responsible_org": event.responsible_org,
        "occurred_at": _iso(event.occurred_at),
        "status": event.status if event.status in STATUSES else STATUSES[-1],
        "status_confidence": event.status_confidence if j else None,
        "judged": j is not None,
        "priority_score": live_priority(session, event, today) if j else 0.0,
        "needs_check": needs_check(event.status, passed) if j else False,
        "needs_review": bool(event.needs_review),
        "deadline_date": _iso(pp.deadline_date) if pp else None,
        "deadline_precision": pp.deadline_precision if pp and pp.deadline_date else None,
        "deadline_passed_days": passed,
        "last_followup_at": _iso(event.last_followup_at),
        "days_since_last_followup": days_since,
        "followup_count": event.followup_count,
        "evidence_count": len(evidence_news_ids(session, event, j)),
        "article_count": len(active_links(event)),
        "primary_promise": (
            {
                "actor_org": pp.actor_org,
                "action": pp.action,
                "deadline_date": _iso(pp.deadline_date),
                "deadline_precision": pp.deadline_precision,
                "strength": pp.strength,
            }
            if pp
            else None
        ),
        "trend_sparkline": [s["hits"] for s in (trend.series if trend else [])],
        "disclaimer": DISCLAIMER,
    }


def event_detail(session: Session, event: Event, today: date) -> dict:
    j = latest_judgment(session, event)
    by_phase: dict[str, list[dict]] = {p: [] for p in PHASES}
    for l in active_links(event):
        a = session.get(Article, l.news_id)
        if a is None or a.change_status == "cancelled":
            continue
        by_phase.setdefault(l.phase if l.phase in PHASES else "기타", []).append(article_out(a, l))
    for items in by_phase.values():
        items.sort(key=lambda x: x["published_at"] or "")
    promises = [p for p in sorted(event.promises, key=lambda p: p.id) if p.strength != "해당없음" and p.merged_into_id is None]
    return {
        "card": event_card(session, event, today),
        "summary": event.summary,
        "seed_query": event.seed_query,
        "status_reason": event.status_reason if j else None,
        "status_reason_text": j.reason_text if j else UNJUDGED_TEXT,
        "latest_judgment": (
            {
                "status": j.status,
                "reason": j.reason,
                "confidence": j.confidence,
                "unresolved_reason": j.unresolved_reason,
                "judged_at": _iso(j.judged_at),
            }
            if j
            else None
        ),
        "promises": [promise_out(session, p, event, today) for p in promises],
        "timeline": [{"phase": p, "articles": by_phase[p]} for p in PHASES],
        "trend": trend_out(latest_trend(session, event), event, today),
        "disclaimer": DISCLAIMER,
    }


def evidence_out(session: Session, event: Event, today: date) -> dict:
    settings = get_settings()
    j = latest_judgment(session, event)
    counted_ids = set(j.counted_signal_ids or []) if j else set()
    considered_ids = {e.get("signal_id") for e in (j.evidence if j else [])} - counted_ids
    active = {l.news_id for l in active_links(event)}
    signals: list[dict] = []
    for s in _current_signals(event):
        link = next((l for l in event.links if l.news_id == s.news_id), None)
        art = session.get(Article, s.news_id)
        if link is None or link.rejected:
            continue  # 해제된 연결의 신호는 근거에서 뺀다
        is_active = s.news_id in active and (art is None or art.change_status != "cancelled")
        signals.append(
            {
                "id": s.id,
                "signal": s.signal,
                "promise_id": s.promise_id,
                "matches_promise": s.matches_promise,
                "confidence": s.confidence,
                "evidence_span": s.evidence_span,
                "rationale": s.rationale,
                "counted": s.id in counted_ids and is_active,
                "considered": s.id in considered_ids,
                "active": is_active,
                "article": article_out(art, link),
            }
        )
    low = [
        {"article": article_out(session.get(Article, l.news_id), l), "link_score": l.link_score, "link_reason": l.link_reason}
        for l in event.links
        if l.needs_review and not l.rejected
    ]
    return {
        "event_id": event.id,
        "title": event.title,
        "status": event.status,
        "judged": j is not None,
        "confidence": j.confidence if j else None,
        "rule": {
            "name": j.reason if j else "",
            "text": j.reason_text if j else UNJUDGED_TEXT,
            "thresholds": dict(j.thresholds or {}) if j else {},
        },
        "unresolved_reason": j.unresolved_reason if j else UNJUDGED_TEXT,
        "signals": signals,
        "low_confidence_links": low,
        "last_followup_at": _iso(event.last_followup_at),
        "next_scheduled_run": next_scheduled_run().isoformat() if settings.scheduler_enabled else None,
        "judged_at": _iso(j.judged_at) if j else None,
        "disclaimer": DISCLAIMER,
    }
