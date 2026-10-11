"""5. 후속 신호 분류(LLM 역할 ⓑ) + 상태 판정(규칙) + 우선순위.

후속 기사 = 활성 연결(해제·검토 대기 제외) 기사 중 published_at > max(발생일 + 유예, 가장 이른 약속 발언일), 삭제(cancelled) 제외.
신호 캐시 키: (event, news_id, prompt_version) + 분류 당시 model + 추적 약속 집합 해시. 모델이나 약속 집합이 바뀌면 다시 분류한다.
LLM 호출은 DB 쓰기 트랜잭션 밖에서 하고, 실패한 기사는 저장하지 않는다(다음 실행에서 재시도).
규칙 판정(rejudge_event)은 LLM 없이 저장된 신호만으로 다시 실행할 수 있다 — 관리자 연결 해제·변경이력 뒤에 쓴다.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, timedelta

from sqlalchemy.orm import Session

from ..bigkinds.protocol import BigKindsClient
from ..config import STATUS_DONE, Settings
from ..db.models import Article, Event, FollowupSignal, Promise, StatusJudgment, TrendSnapshot
from ..llm.client import PROMPT_VERSIONS, LLMClient, LLMFailure, validate_span
from ..llm.schemas import FollowupInput, PromiseRef
from ..rules import priority as priority_rules
from ..rules.status import SIGNAL_PROGRESS, Judgment, PromiseIn, SignalIn, Thresholds, judge
from .trend import compute_trend

log = logging.getLogger("migyeol.judge")


def trackable_promises(event: Event) -> list[Promise]:
    return sorted([p for p in event.promises if p.is_trackable], key=lambda p: p.id)


def promise_set_hash(promises: list[Promise]) -> str:
    raw = "|".join(f"{p.id}:{p.action}:{p.deadline_date}" for p in sorted(promises, key=lambda p: p.id))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def overdue_promise(event: Event, today: date) -> Promise | None:
    """기한이 지난 추적 약속 중 가장 오래 지난 것."""
    passed = [p for p in trackable_promises(event) if p.deadline_date is not None and p.deadline_date < today]
    return min(passed, key=lambda p: p.deadline_date) if passed else None


def deadline_passed_days(event: Event, today: date) -> int | None:
    """우선순위용: 추적 약속 중 최대 경과 일수."""
    p = overdue_promise(event, today)
    return (today - p.deadline_date).days if p else None


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
        if not l.is_active:
            continue
        a = session.get(Article, l.news_id)
        if a is not None and a.published_at is not None and a.published_at > cutoff and a.change_status != "cancelled":
            out.append(a)
    return sorted(out, key=lambda a: a.published_at)


def latest_trend_dict(session: Session, event: Event) -> dict | None:
    snap = session.query(TrendSnapshot).filter_by(event_id=event.id).order_by(TrendSnapshot.id.desc()).first()
    if snap is None:
        return None
    return {"interval": snap.interval, "series": snap.series, "peak_label": snap.peak_label, "peak_hits": snap.peak_hits, "recent_mean": snap.recent_mean, "drop_ratio": snap.drop_ratio}


def classify_followups(
    session: Session,
    event: Event,
    llm: LLMClient,
    settings: Settings,
    call_budget: dict | None = None,
) -> dict:
    """후속 기사마다 LLM 신호를 만든다(캐시 유효하면 건너뜀). DB 쓰기는 호출이 모두 끝난 뒤 한 번."""
    session.refresh(event)
    promises = trackable_promises(event)
    refs = [PromiseRef(id=p.id, actor_org=p.actor_org, action=p.action, deadline_date=p.deadline_date.isoformat() if p.deadline_date else None) for p in promises]
    promise_ids = {p.id for p in promises}
    pset = promise_set_hash(promises)
    version = PROMPT_VERSIONS["followup"]
    existing = {s.news_id: s for s in event.signals if s.prompt_version == version}
    followups = followup_articles(session, event, settings)
    stats = {"event": event.key, "followups": len(followups), "llm_calls": 0, "llm_failed": 0, "reclassified": 0}

    quotes_by_news: dict[str, list[str]] = {}
    for q in event.quotations:
        quotes_by_news.setdefault(q.news_id, []).append(f"{q.source}: {q.quotation}")

    todo: list[Article] = []
    for a in followups:
        s = existing.get(a.news_id)
        if s is not None and s.model == llm.model and s.promise_set_hash == pset:
            continue
        if s is not None:
            stats["reclassified"] += 1
        todo.append(a)

    results: list[tuple[Article, object]] = []
    for a in todo:
        if call_budget is not None and call_budget.get("remaining", 1) <= 0:
            stats.setdefault("deferred", 0)
            stats["deferred"] += 1
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
        if call_budget is not None:
            call_budget["remaining"] = call_budget.get("remaining", 0) - 1
        try:
            res = validate_span(llm.classify_followup(inp, key=f"{event.key}||{a.news_id}"), inp)
        except LLMFailure as exc:
            stats["llm_failed"] += 1
            log.warning("followup LLM 실패(다음 실행에서 재시도) %s/%s: %s", event.key, a.news_id, exc)
            continue
        results.append((a, res))

    for a, res in results:
        pid = res.promise_id if res.promise_id in promise_ids else None
        sig = existing.get(a.news_id)
        if sig is None:
            sig = FollowupSignal(event_id=event.id, news_id=a.news_id, prompt_version=version)
            event.signals.append(sig)
            existing[a.news_id] = sig
        sig.promise_id = pid
        sig.signal = res.signal
        sig.matches_promise = bool(res.matches_promise and pid is not None)
        sig.evidence_span = res.evidence_span[:200]
        sig.confidence = res.confidence
        sig.rationale = res.rationale
        sig.model = llm.model
        sig.promise_set_hash = pset
    session.commit()
    return stats


def rejudge_event(session: Session, event: Event, settings: Settings, today: date, trend: dict | None = None) -> Judgment:
    """저장된 신호와 추이로 규칙 판정·우선순위·단계만 다시 계산한다 (LLM·API 호출 없음)."""
    session.refresh(event)
    version = PROMPT_VERSIONS["followup"]
    followups = followup_articles(session, event, settings)
    by_news = {a.news_id: a for a in followups}
    signals_in = [
        SignalIn(
            id=s.id,
            news_id=s.news_id,
            signal=s.signal,
            matches_promise=s.matches_promise,
            confidence=s.confidence,
            published_at=by_news[s.news_id].published_at,
            promise_id=s.promise_id,
            evidence_span=s.evidence_span,
        )
        for s in event.signals
        if s.prompt_version == version and s.news_id in by_news
    ]
    promises = trackable_promises(event)
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
            counted_signal_ids=list(j.counted_signal_ids),
            unresolved_reason=j.unresolved_reason,
            thresholds=th.as_dict(),
            prompt_version=version,
        )
    )
    event.status = j.status
    event.status_reason = j.reason
    event.status_confidence = j.confidence
    event.followup_count = len(followups)
    event.last_followup_at = followups[-1].published_at if followups else None

    if trend is None:
        trend = latest_trend_dict(session, event)
    cutoff = followup_cutoff(session, event, settings)
    # 후속 기사가 없는 사건도 '얼마나 오래 보도가 없었나'(기준일 이후 경과)로 가산점을 받는다
    days_since = (today - event.last_followup_at).days if event.last_followup_at else max(0, (today - cutoff).days)
    passed = deadline_passed_days(event, today)
    event.priority_score = priority_rules.score(
        status=j.status,
        deadline_passed_days=passed,
        days_since_last_followup=days_since,
        drop_ratio=(trend or {}).get("drop_ratio"),
        has_firm_promise=any(p.strength == "확약" for p in promises),
    )

    # 유효 신호가 있는 기사의 단계 갱신 — 채택 여부와 무관하게 모든 유효 신호 (R1·R2 사건의 진행·완료 기사도 단계에 반영)
    valid_ids = set(j.valid_signal_ids)
    for s in event.signals:
        if s.id not in valid_ids:
            continue
        for l in event.links:
            if l.news_id == s.news_id and l.phase != "대응발표":
                l.phase = "조치진행" if s.signal == SIGNAL_PROGRESS else "결과확인"
    session.commit()
    return j


def judge_event(
    session: Session,
    client: BigKindsClient,
    event: Event,
    llm: LLMClient,
    settings: Settings,
    today: date,
    call_budget: dict | None = None,
) -> dict:
    stats = classify_followups(session, event, llm, settings, call_budget)

    # 보도량 추이 — 실패하면 이전 스냅샷으로 대체하고 판정은 계속한다
    try:
        trend = compute_trend(client, event, today)
        session.add(TrendSnapshot(event_id=event.id, **trend))
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        log.warning("time_line 실패 %s: %s — 이전 추이로 대체", event.key, exc)
        trend = latest_trend_dict(session, event)
        stats["trend_error"] = str(exc)[:200]

    j = rejudge_event(session, event, settings, today, trend)
    stats.update(status=j.status, reason=j.reason, priority=event.priority_score, drop_ratio=(trend or {}).get("drop_ratio"))
    log.info("judge %s", stats)
    return stats


def is_done(event: Event) -> bool:
    return event.status == STATUS_DONE
