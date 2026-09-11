"""확인 필요 우선순위 점수 (개발 프롬프트 9절). 높을수록 목록 상단."""

from __future__ import annotations

from ..config import (
    NEEDS_CHECK_STATUSES,
    STATUS_DONE,
    STATUS_NEW_PROBLEM,
    STATUS_NO_FOLLOWUP,
    STATUS_UNDETERMINED,
)


def score(
    status: str,
    deadline_passed_days: int | None,
    days_since_last_followup: int | None,
    drop_ratio: float | None,
    has_firm_promise: bool,
) -> float:
    s = 0.0
    if deadline_passed_days is not None and deadline_passed_days >= 0 and status != STATUS_DONE:
        s += 40
    if status == STATUS_NO_FOLLOWUP:
        s += 25
    elif status == STATUS_UNDETERMINED:
        s += 15
    elif status == STATUS_NEW_PROBLEM:
        s += 10
    if days_since_last_followup is not None:
        s += min(20.0, days_since_last_followup / 10.0)
    if drop_ratio is not None and drop_ratio >= 0.8:
        s += 10
    if has_firm_promise:
        s += 10
    return round(s, 1)


def needs_check(status: str, deadline_passed_days: int | None) -> bool:
    return status in NEEDS_CHECK_STATUSES or (deadline_passed_days is not None and deadline_passed_days >= 0 and status != STATUS_DONE)
