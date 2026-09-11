"""사건 상태 판정 규칙 (개발 프롬프트 9절). 순수 함수 — DB 에 의존하지 않는다.

유효 신호 = matches_promise AND confidence >= signal_min_conf AND signal ∈ {완료, 진행, 새로운문제}

R1  가장 최근 유효 신호가 새로운문제(≥ completion_min_conf)                       -> 새로운 문제 발생
R2  완료 신호 ≥ completion_min_conf 1건 이상, 또는 ≥ signal_min_conf 2건 이상      -> 조치 완료 근거 확인
R3  진행 신호 1건 이상                                                          -> 조치 진행 정황 확인
R4  후속 기사 0건 / 마지막 후속 기사 stale_followup_days 이전 / 기한 경과+완료·진행 없음 -> 후속보도 부족
R5  그 외                                                                       -> 판단 불가 (+ 사유)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..config import (
    STATUS_DONE,
    STATUS_IN_PROGRESS,
    STATUS_NEW_PROBLEM,
    STATUS_NO_FOLLOWUP,
    STATUS_UNDETERMINED,
)

SIGNAL_DONE = "완료"
SIGNAL_PROGRESS = "진행"
SIGNAL_NEW_PROBLEM = "새로운문제"
SIGNAL_UNRELATED = "무관"
SIGNAL_UNCLEAR = "불명확"
SIGNALS = (SIGNAL_DONE, SIGNAL_PROGRESS, SIGNAL_NEW_PROBLEM, SIGNAL_UNRELATED, SIGNAL_UNCLEAR)


@dataclass(frozen=True)
class SignalIn:
    id: int | None
    news_id: str
    signal: str
    matches_promise: bool
    confidence: float
    published_at: date | None
    promise_id: int | None = None
    evidence_span: str = ""


@dataclass(frozen=True)
class PromiseIn:
    id: int | None
    deadline_date: date | None
    strength: str
    is_trackable: bool


@dataclass(frozen=True)
class Thresholds:
    signal_min_conf: float = 0.6
    completion_min_conf: float = 0.7
    stale_followup_days: int = 60

    def as_dict(self) -> dict[str, float | int]:
        return {
            "signal_min_conf": self.signal_min_conf,
            "completion_min_conf": self.completion_min_conf,
            "stale_followup_days": self.stale_followup_days,
        }


@dataclass
class Judgment:
    status: str
    reason: str
    reason_text: str
    confidence: float
    evidence: list[dict] = field(default_factory=list)
    unresolved_reason: str | None = None
    counted_signal_ids: list[int] = field(default_factory=list)


def _ev(s: SignalIn) -> dict:
    return {
        "signal_id": s.id,
        "news_id": s.news_id,
        "signal": s.signal,
        "confidence": round(s.confidence, 3),
        "span": s.evidence_span,
        "published_at": s.published_at.isoformat() if s.published_at else None,
    }


def deadline_passed(promises: list[PromiseIn], today: date) -> bool:
    return any(p.is_trackable and p.deadline_date is not None and p.deadline_date < today for p in promises)


def judge(
    signals: list[SignalIn],
    promises: list[PromiseIn],
    followup_dates: list[date],
    today: date,
    th: Thresholds = Thresholds(),
) -> Judgment:
    valid = [
        s
        for s in signals
        if s.matches_promise and s.confidence >= th.signal_min_conf and s.signal in (SIGNAL_DONE, SIGNAL_PROGRESS, SIGNAL_NEW_PROBLEM)
    ]
    done = [s for s in valid if s.signal == SIGNAL_DONE]
    strong_done = [s for s in done if s.confidence >= th.completion_min_conf]
    progress = [s for s in valid if s.signal == SIGNAL_PROGRESS]
    problems = [s for s in valid if s.signal == SIGNAL_NEW_PROBLEM]
    latest = max(valid, key=lambda s: s.published_at or date.min) if valid else None
    last_followup = max(followup_dates) if followup_dates else None
    trackable = [p for p in promises if p.is_trackable]
    passed = deadline_passed(promises, today)

    def mean_conf(items: list[SignalIn]) -> float:
        return round(sum(s.confidence for s in items) / len(items), 3) if items else 0.5

    # R1
    if latest is not None and latest.signal == SIGNAL_NEW_PROBLEM and latest.confidence >= th.completion_min_conf:
        return Judgment(
            status=STATUS_NEW_PROBLEM,
            reason="R1_new_problem",
            reason_text=f"가장 최근 유효 신호가 '새로운 문제'(신뢰도 {latest.confidence:.2f}, 기준 {th.completion_min_conf} 이상)",
            confidence=mean_conf(problems),
            evidence=[_ev(s) for s in problems],
            counted_signal_ids=[s.id for s in problems if s.id is not None],
        )
    # R2
    if strong_done or len(done) >= 2:
        counted = strong_done or done
        if strong_done:
            text = f"완료 신호 {len(strong_done)}건(최고 신뢰도 {max(s.confidence for s in strong_done):.2f})이 기준 {th.completion_min_conf} 이상"
        else:
            text = f"완료 신호 {len(done)}건이 기준 {th.signal_min_conf} 이상 (2건 이상 규칙)"
        return Judgment(
            status=STATUS_DONE,
            reason="R2_completion_evidence",
            reason_text=text,
            confidence=mean_conf(counted),
            evidence=[_ev(s) for s in counted],
            counted_signal_ids=[s.id for s in counted if s.id is not None],
        )
    # R3
    if progress:
        return Judgment(
            status=STATUS_IN_PROGRESS,
            reason="R3_progress_evidence",
            reason_text=f"진행 신호 {len(progress)}건(기준 {th.signal_min_conf} 이상), 완료 근거 없음",
            confidence=mean_conf(progress),
            evidence=[_ev(s) for s in progress],
            counted_signal_ids=[s.id for s in progress if s.id is not None],
        )
    # R4
    if not followup_dates:
        return Judgment(
            status=STATUS_NO_FOLLOWUP,
            reason="R4_no_followup",
            reason_text="사건 발생 이후 조치와 관련된 후속 기사가 수집되지 않음",
            confidence=0.5,
            unresolved_reason="후속 기사 0건",
        )
    days_since = (today - last_followup).days if last_followup else None
    if days_since is not None and days_since >= th.stale_followup_days:
        return Judgment(
            status=STATUS_NO_FOLLOWUP,
            reason="R4_stale_followup",
            reason_text=f"마지막 후속 기사가 {days_since}일 전(기준 {th.stale_followup_days}일)이고 완료·진행 신호가 없음",
            confidence=0.5,
            unresolved_reason=f"마지막 후속 기사 {last_followup.isoformat()} 이후 관련 보도 없음",
        )
    if passed and not done and not progress:
        return Judgment(
            status=STATUS_NO_FOLLOWUP,
            reason="R4_deadline_passed",
            reason_text="발표된 기한이 지났으나 완료·진행 근거가 보도에서 확인되지 않음",
            confidence=0.5,
            unresolved_reason="기한 경과, 완료·진행 신호 없음",
        )
    # R5
    if not trackable:
        why = "추적 가능한 약속(확약·계획)이 인용문에서 추출되지 않음"
    elif done:
        why = f"완료 신호 {len(done)}건이 있으나 신뢰도({max(s.confidence for s in done):.2f})가 기준 {th.completion_min_conf} 미만이고 단건"
    else:
        why = f"후속 기사 {len(followup_dates)}건이 있으나 조치와 관련된 유효 신호(신뢰도 {th.signal_min_conf} 이상)가 없음"
    return Judgment(
        status=STATUS_UNDETERMINED,
        reason="R5_undetermined",
        reason_text=why,
        confidence=0.5,
        evidence=[_ev(s) for s in done],
        unresolved_reason=why,
        counted_signal_ids=[],
    )
