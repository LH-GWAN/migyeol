"""DB 모델 -> API 응답 (docs/API_CONTRACT.md). 기사 응답에는 전문이 없다 — hilight(≤200자)와 외부 링크만."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy.orm import Session

from ..config import DISCLAIMER, PHASES, STATUSES
from ..db.models import Article, Event, EventArticle, FollowupSignal, Promise, Quotation, StatusJudgment, TrendSnapshot
from ..pipeline.judge import deadline_passed_days, trackable_promises
from ..pipeline.scheduler import next_scheduled_run
from ..rules.priority import needs_check


def _iso(d: date | datetime | None) -> str | None:
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.replace(microsecond=0).isoformat() + "+09:00"
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


def _links(event: Event, include_review: bool = False) -> list[EventArticle]:
    return [l for l in event.links if not l.rejected and (include_review or not l.needs_review)]


def latest_judgment(session: Session, event: Event) -> StatusJudgment | None:
    return session.query(StatusJudgment).filter_by(event_id=event.id).order_by(StatusJudgment.id.desc()).first()


def latest_trend(session: Session, event: Event) -> TrendSnapshot | None:
    return session.query(TrendSnapshot).filter_by(event_id=event.id).order_by(TrendSnapshot.id.desc()).first()


def primary_promise(event: Event) -> Promise | None:
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
    pp = primary_promise(event)
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
            "today": today.isoformat(),
        },
    }


def event_card(session: Session, event: Event, today: date) -> dict:
    pp = primary_promise(event)
    passed = deadline_passed_days(event, today)
    days_since = (today - event.last_followup_at).days if event.last_followup_at else None
    trend = latest_trend(session, event)
    evidence_ids = {p.evidence_news_id for p in trackable_promises(event)} | {s.news_id for s in event.signals}
    return {
        "id": event.id,
        "title": event.title,
        "incident_type": event.incident_type,
        "region": event.region,
        "facility": event.facility,
        "responsible_org": event.responsible_org,
        "occurred_at": _iso(event.occurred_at),
        "status": event.status if event.status in STATUSES else STATUSES[-1],
        "status_confidence": event.status_confidence,
        "priority_score": event.priority_score,
        "needs_check": needs_check(event.status, passed),
        "needs_review": bool(event.needs_review),
        "deadline_date": _iso(pp.deadline_date) if pp else None,
        "deadline_passed_days": passed,
        "last_followup_at": _iso(event.last_followup_at),
        "days_since_last_followup": days_since,
        "followup_count": event.followup_count,
        "evidence_count": len(evidence_ids),
        "article_count": len(_links(event)),
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
    for l in _links(event):
        a = session.get(Article, l.news_id)
        if a is None:
            continue
        by_phase.setdefault(l.phase if l.phase in PHASES else "기타", []).append(article_out(a, l))
    for items in by_phase.values():
        items.sort(key=lambda x: x["published_at"] or "")
    promises = [p for p in sorted(event.promises, key=lambda p: p.id) if p.strength != "해당없음"]
    return {
        "card": event_card(session, event, today),
        "summary": event.summary,
        "seed_query": event.seed_query,
        "status_reason": event.status_reason,
        "status_reason_text": j.reason_text if j else "",
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
    j = latest_judgment(session, event)
    counted_ids = {e.get("signal_id") for e in (j.evidence if j else [])}
    signals: list[dict] = []
    for s in sorted(event.signals, key=lambda s: s.id):
        link = next((l for l in event.links if l.news_id == s.news_id), None)
        signals.append(
            {
                "id": s.id,
                "signal": s.signal,
                "promise_id": s.promise_id,
                "matches_promise": s.matches_promise,
                "confidence": s.confidence,
                "evidence_span": s.evidence_span,
                "rationale": s.rationale,
                "counted": s.id in counted_ids,
                "article": article_out(session.get(Article, s.news_id), link),
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
        "confidence": j.confidence if j else 0.0,
        "rule": {"name": j.reason if j else "", "text": j.reason_text if j else "", "thresholds": dict(j.thresholds or {}) if j else {}},
        "unresolved_reason": j.unresolved_reason if j else None,
        "signals": signals,
        "low_confidence_links": low,
        "last_followup_at": _iso(event.last_followup_at),
        "next_scheduled_run": next_scheduled_run().isoformat(),
        "judged_at": _iso(j.judged_at) if j else None,
        "disclaimer": DISCLAIMER,
    }
