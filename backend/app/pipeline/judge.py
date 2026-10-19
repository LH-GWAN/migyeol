"""5. 후속 신호 분류(LLM 역할 ⓑ) + 상태 판정(규칙) + 우선순위.

후속 기사 = 활성 연결(해제·검토 대기 제외) 기사 중 published_at > max(발생일 + 유예, 가장 이른 약속 발언일), 삭제(cancelled) 제외.
추적 약속 = is_trackable 이고 근거 기사가 활성 연결인 약속 (연결이 해제·삭제되면 약속도 추적에서 빠진다).
신호 캐시 키: (event, news_id, prompt_version) + 분류 당시 model + 추적 약속 집합 해시. 모델이나 약속 집합이 바뀌면 다시 분류한다.
LLM 호출은 DB 쓰기 트랜잭션 밖에서 하고, 실패한 기사는 저장하지 않는다(다음 실행에서 재시도). 최신 기사부터 분류한다.
규칙 판정(rejudge_event)은 **현재 약속 집합으로 분류된** 신호만 쓴다. 분류를 못 받은 후속 기사가 있으면
Event.pending_followups 에 건수를 남기고 판정은 잠정(검토 큐)으로 표시한다 — 명세 §8.
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
    """추적 약속: is_trackable 이고 근거 기사(대표 근거 또는 병합된 근거 중 하나)가 이 사건의 활성 연결(해제·검토 대기 아님)인 것."""
    active = {l.news_id for l in event.links if l.is_active}
    return sorted(
        [p for p in event.promises if p.is_trackable and (p.evidence_news_id in active or any(n in active for n in (p.merged_news_ids or [])))],
        key=lambda p: p.id,
    )


def is_judged(session: Session, event: Event) -> bool:
    """judge 가 한 번이라도 실행된 사건인가. link 단계·관리자 처리 뒤의 규칙 재판정은 이 사건에만 한다 (판정 전 사건을 '판정됨' 으로 만들지 않는다)."""
    return session.query(StatusJudgment.id).filter_by(event_id=event.id).first() is not None


def note_llm_failure(event: Event, key: str) -> int:
    """항목별 LLM 실패 횟수를 올린다. 반환: 누적 횟수."""
    counts = dict(event.llm_failures or {})
    counts[key] = int(counts.get(key, 0)) + 1
    counts.pop("_acked", None)  # 새 실패가 나면 관리자 '확인함' 은 무효
    event.llm_failures = counts
    return counts[key]


def clear_llm_failure(event: Event, key: str) -> None:
    counts = dict(event.llm_failures or {})
    if key in counts:
        counts.pop(key)
        event.llm_failures = counts


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


def days_since_followup(session: Session, event: Event, settings: Settings, today: date) -> int:
    """우선순위용 '보도 공백' 일수. 후속 기사가 없는 사건도 기준일(발생+유예 / 첫 약속) 이후 경과로 가산점을 받는다.
    판정(rejudge)과 API(live_priority)가 같은 값을 쓰도록 한 곳에 둔다."""
    if event.last_followup_at:
        return (today - event.last_followup_at).days
    return max(0, (today - followup_cutoff(session, event, settings)).days)


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
    stats = {"event": event.key, "followups": len(followups), "llm_calls": 0, "llm_failed": 0, "reclassified": 0, "deferred": 0}
    if not promises:
        return stats  # 추적 약속이 없으면 신호가 뜻이 없다 — LLM 을 부르지 않는다 (R5 사유로 표시)

    quotes_by_news: dict[str, list[str]] = {}
    for q in event.quotations:
        quotes_by_news.setdefault(q.news_id, []).append(f"{q.source}: {q.quotation}")

    todo: list[Article] = []
    for a in reversed(followups):  # 최신 기사부터 — 호출 상한에 걸려도 판정에 중요한 최근 신호가 먼저 분류된다
        s = existing.get(a.news_id)
        if s is not None and s.model == llm.model and s.promise_set_hash == pset:
            continue
        if s is not None:
            stats["reclassified"] += 1
        todo.append(a)

    results: list[tuple[Article, object]] = []
    for a in todo:
        if call_budget is not None and call_budget.get("remaining", 1) <= 0:
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
            n = note_llm_failure(event, f"followup:{a.news_id}")
            if n >= 2:
                # 명세 §8: 1회 재시도 후에도 실패하면 '불명확' 으로 기록하고 검토 큐로 (무한 재시도 방지)
                log.warning("followup LLM %d회 실패 — 불명확 기록·검토 큐 %s/%s: %s", n, event.key, a.news_id, exc)
                results.append((a, _unclear_after_failures(n, exc)))
                stats.setdefault("gave_up", 0)
                stats["gave_up"] += 1
            else:
                log.warning("followup LLM 실패(다음 실행에서 재시도) %s/%s: %s", event.key, a.news_id, exc)
            continue
        clear_llm_failure(event, f"followup:{a.news_id}")
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


def _unclear_after_failures(n: int, exc: Exception):
    from ..llm.schemas import FollowupSignal as _Sig

    return _Sig(signal="불명확", promise_id=None, matches_promise=False, evidence_span="", confidence=0.0, rationale=f"LLM 호출 {n}회 실패로 불명확 기록 — 사람 검토 필요 ({type(exc).__name__})")


def rejudge_event(session: Session, event: Event, settings: Settings, today: date, trend: dict | None = None) -> Judgment:
    """저장된 신호와 추이로 규칙 판정·우선순위·단계만 다시 계산한다 (LLM·API 호출 없음)."""
    session.refresh(event)
    version = PROMPT_VERSIONS["followup"]
    followups = followup_articles(session, event, settings)
    by_news = {a.news_id: a for a in followups}
    promises = trackable_promises(event)
    pset = promise_set_hash(promises)
    # 현재 약속 집합으로 분류된 신호만 유효하다 — 약속이 취소·해제된 뒤 남은 옛 신호로 판정하지 않는다
    current = [s for s in event.signals if s.prompt_version == version and s.news_id in by_news and s.promise_set_hash == pset]
    pending = len(by_news) - len({s.news_id for s in current}) if promises else 0
    pending_q = int(event.pending_quotations or 0)
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
        for s in current
    ]
    tracked_ids = {p.id for p in promises}
    promises_in = [PromiseIn(id=p.id, deadline_date=p.deadline_date, strength=p.strength, is_trackable=p.id in tracked_ids) for p in event.promises]
    th = Thresholds(settings.signal_min_conf, settings.completion_min_conf, settings.stale_followup_days)
    j = judge(signals_in, promises_in, [a.published_at for a in followups], today, th)
    notes = []
    if pending_q:
        notes.append(f"인용문 {pending_q}건은 아직 약속 추출 전")
    if pending:
        notes.append(f"후속 기사 {pending}건은 아직 분류 전")
    if notes:
        note = ", ".join(notes) + "(호출 상한·실패 이월) — 잠정 판정"
        j.reason_text = f"{j.reason_text} ({note})"
        j.unresolved_reason = f"{j.unresolved_reason} / {note}" if j.unresolved_reason else note

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
    event.pending_followups = pending
    from .review import recompute_needs_review

    recompute_needs_review(session, event)

    if trend is None:
        trend = latest_trend_dict(session, event)
    days_since = days_since_followup(session, event, settings, today)
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
