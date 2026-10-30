"""'후속보도 확인' 은 상태로 센다 (2026-10-30) — 유효 신호가 있어도 판단 불가·후속보도 부족 사건은 빠진다.

실측 사례: 212 코레일 서울본부 화재(완료 신호 0.68 < 완료 기준 0.7 → 판단 불가),
213 파주 집중호우(새로운문제 신호 0.64, 이후 보도 끊김 → 후속보도 부족). 둘 다 신호는 유효(≥ 0.6)였다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import STATUS_DONE, STATUS_IN_PROGRESS, STATUS_NEW_PROBLEM, STATUS_NO_FOLLOWUP, STATUS_UNDETERMINED
from app.experiment.metrics import EventFacts


@pytest.mark.parametrize(
    "status, expected",
    [
        (STATUS_DONE, True),
        (STATUS_IN_PROGRESS, True),
        (STATUS_NEW_PROBLEM, True),
        (STATUS_UNDETERMINED, False),
        (STATUS_NO_FOLLOWUP, False),
    ],
)
def test_metrics_has_followup_follows_status(status, expected):
    signal = (SimpleNamespace(id=1, signal="완료"), SimpleNamespace(published_at=None))
    f = EventFacts(event=SimpleNamespace(status=status), org="코레일", linked=[], followups=[], valid=[signal])
    assert f.has_followup is expected


def test_metrics_has_followup_needs_a_signal():
    f = EventFacts(event=SimpleNamespace(status=STATUS_DONE), org="코레일", linked=[], followups=[], valid=[])
    assert f.has_followup is False
