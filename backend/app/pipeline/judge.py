"""5. 후속 신호 분류(LLM 역할 ⓑ) + 상태 판정(규칙) + 우선순위.

후속 기사 = 연결된 기사 중 published_at > max(발생일 + 유예, 가장 이른 약속 발언일)
기사당 LLM 1회, (event, news_id, prompt_version) 로 캐시. evidence_span 은 입력의 부분 문자열이어야 한다.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy.orm import Session

from ..bigkinds.protocol import BigKindsClient
from ..config import STATUS_DONE, Settings
from ..db.models import Article, Event, FollowupSignal, Promise, StatusJudgment, TrendSnapshot
from ..llm.client import PROMPT_VERSIONS, LLMClient, validate_span
from ..llm.schemas import FollowupInput, PromiseRef
from ..rules import priority as priority_rules
from ..rules.status import PromiseIn, SignalIn, Thresholds, judge
from .trend import compute_trend

log = logging.getLogger("migyeol.judge")


def trackable_promises(event: Event) -> list[Promise]:
    return sorted([p for p in event.promises if p.is_trackable], key=lambda p: p.id)


def deadline_passed_days(event: Event, today: date) -> int | None:
    passed = [
        (today - p.deadline_date).days
        for p in trackable_promises(event)
        if p.deadline_date is not None and p.deadline_date < today
    ]
    return max(passed) if passed else None


def followup_cutoff(session: Session, event: Event, settings: Settings) -> date:
    cutoff = event.occurred_at + timedelta(days=settings.followup_grace_days)
    dates = []
    for p in trackable_promises(event):
        a = session.get(Article, p.evidence_news_id)
        if a is not None and a.published_at is not None:
            dates.append(a.published_at)
    if dates:
        cutoff = max(cutoff, min(dates))
    return cutoff


def followup_articles(session: Session, event: Event, settings: Settings) -> list[Article]:
    cutoff = followup_cutoff(session, event, settings)
    out = []
    for l in event.links:
        if l.rejected or l.needs_review:
            continue
        a = session.get(Article, l.news_id)
        if a is not None and a.published_at is not None and a.published_at > cutoff and a.change_status != "cancelled":
            out.append(a)
    return sorted(out, key=lambda a: a.published_at)


def judge_event(
    session: Session,
    client: BigKindsClient,
    event: Event,
    llm: LLMClient,
    settings: Settings,
    today: date,
) -> dict:
    session.refresh(event)
    promises = trackable_promises(event)
    refs = [PromiseRef(id=p.id, actor_org=p.actor_org, action=p.action, deadline_date=p.deadline_date.isoformat() if p.deadline_date else None) for p in promises]
    promise_ids = {p.id for p in promises}
    version = PROMPT_VERSIONS["followup"]
    existing = {s.news_id: s for s in event.signals if s.prompt_version == version}
    followups = followup_articles(session, event, settings)
    stats = {"event": event.key, "followups": len(followups), "llm_calls": 0}

    quotes_by_news: dict[str, list[str]] = {}
    for q in event.quotations:
        quotes_by_news.setdefault(q.news_id, []).append(f"{q.source}: {q.quotation}")

    for a in followups:
        if a.news_id in existing:
            continue
        inp = FollowupInput(
            event_summary=event.summary or event.title,
            promises=refs,
            article_title=a.title or "",
            hilight=a.hilight or a.content_200 or "",
            quotations=quotes_by_news.get(a.news_id, []),
            published_at=a.published_at.isoformat(),
        )
        stats["llm_calls"] += 1
        res = validate_span(llm.classify_followup(inp, key=f"{event.key}||{a.news_id}"), inp)
        pid = res.promise_id if res.promise_id in promise_ids else None
        sig = FollowupSignal(
            event_id=event.id,
            promise_id=pid,
            news_id=a.news_id,
            signal=res.signal,
            matches_promise=bool(res.matches_promise and pid is not None),
            evidence_span=res.evidence_span[:200],
            confidence=res.confidence,
            rationale=res.rationale,
            model=llm.model,
            prompt_version=version,
        )
        event.signals.append(sig)
        existing[a.news_id] = sig
    session.flush()

    # 보도량 추이
    trend = compute_trend(client, event, today)
    session.add(TrendSnapshot(event_id=event.id, **trend))

    # 상태 판정 (규칙)
    by_news = {a.news_id: a for a in followups}
    signals_in = [
        SignalIn(
            id=s.id,
            news_id=s.news_id,
            signal=s.signal,
            matches_promise=s.matches_promise,
            confidence=s.confidence,
            published_at=by_news[s.news_id].published_at if s.news_id in by_news else None,
            promise_id=s.promise_id,
            evidence_span=s.evidence_span,
        )
        for s in existing.values()
        if s.news_id in by_news
    ]
    promises_in = [PromiseIn(id=p.id, deadline_date=p.deadline_date, strength=p.strength, is_trackable=p.is_trackable) for p in event.promises]
    th = Thresholds(settings.signal_min_conf, settings.completion_min_conf, settings.stale_followup_days)
    j = judge(signals_in, promises_in, [a.published_at for a in followups], today, th)

    session.add(
        StatusJudgment(
            event_id=event.id,
            status=j.status,
            reason=j.reason,
            reason_text=j.reason_text,
            confidence=j.confidence,
            evidence=j.evidence,
            unresolved_reason=j.unresolved_reason,
            thresholds=th.as_dict(),
        )
    )
    event.status = j.status
    event.status_reason = j.reason
    event.status_confidence = j.confidence
    event.followup_count = len(followups)
    event.last_followup_at = followups[-1].published_at if followups else None

    days_since = (today - event.last_followup_at).days if event.last_followup_at else None
    passed = deadline_passed_days(event, today)
    event.priority_score = priority_rules.score(
        status=j.status,
        deadline_passed_days=passed,
        days_since_last_followup=days_since,
        drop_ratio=trend["drop_ratio"],
        has_firm_promise=any(p.strength == "확약" for p in promises),
    )

    # 유효 신호가 있는 기사의 단계 갱신
    counted = set(j.counted_signal_ids)
    for s in existing.values():
        if s.id not in counted:
            continue
        for l in event.links:
            if l.news_id == s.news_id and l.phase != "대응발표":
                l.phase = "조치진행" if s.signal == "진행" else "결과확인"

    session.commit()
    stats.update(status=j.status, reason=j.reason, priority=event.priority_score, drop_ratio=trend["drop_ratio"])
    log.info("judge %s", stats)
    return stats


def is_done(event: Event) -> bool:
    return event.status == STATUS_DONE
