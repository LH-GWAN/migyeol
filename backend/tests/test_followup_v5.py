"""rules-v5 (docs/EXPERIMENT.md §14.6) — 구절 오독 고침 · 같은 행동 제목 경로 · 수사 표현 표지 · 같은 시설 재발 · 발생 직후 새로운문제."""

from __future__ import annotations

from datetime import date

import pytest

from app.config import Settings
from app.db.models import Article, Event
from app.llm import rules_adapter as R
from app.llm.schemas import FollowupInput, PromiseRef
from app.pipeline import judge as J


def S(**kw) -> Settings:
    base = dict(_env_file=None, followup_expression_fixes=True, followup_headline_expressions=True, followup_cutoff="grace", followup_min_link_prob=0.0)
    base.update(kw)
    return Settings(**base)


@pytest.mark.parametrize(
    "text",
    [
        "정부는 월성원전 2·3·4호기에 대한 폐로 절차에 착수하라",  # 요구 — 경주 지진 오탐
        "또 화재 당시 건물 지하 수영장에는 어린이 수십 명이 강습을 받고 있었다",  # '또한 화재 때' — 야탑동 오탐
        "공사 중이던 냉동창고에서 불이 났다",  # 장소 설명 — 55보급창 오탐
    ],
)
def test_phrase_fixes_drop_misread_phrases(text):
    assert [s for s, _ in R._expressions(text, True, True, False)] != []  # rules-v4 는 읽었다
    assert [s for s, _ in R._expressions(text, True, True, True)] == []


@pytest.mark.parametrize("text", ["복구 공사 중이다", "제방 보강공사를 진행 중", "또다시 침수 피해 발생", "운행을 재개했다"])
def test_phrase_fixes_keep_real_status(text):
    assert [s for s, _ in R._expressions(text, True, True, True)] == [s for s, _ in R._expressions(text, True, True, False)] != []


def _inp(title: str, action: str) -> FollowupInput:
    return FollowupInput(event_summary="", promises=[PromiseRef(id=7, actor_org="공주시", action=action)], article_title=title, hilight="",
                         published_at="2024-06-19", term_weights={"공주시": 0.05})


def test_same_action_title_path():
    """'우기 전 복구를 완료하겠다' ↔ '장마 앞두고 복구 완료' — 약속 낱말은 안 겹치지만 같은 종류의 행동(복구)·완료 표현의 제목."""
    inp = _inp("작년 산사태가 할퀸 곳…장마 앞두고 복구 완료", "우기 전 최대한 신속하게 복구를 완료해 2차 피해가 발생하지 않도록")
    assert R.RulesLLM(S()).classify_followup(inp).signal == "불명확"
    sig = R.RulesLLM(S(followup_same_action=True)).classify_followup(inp)
    assert sig.signal == "완료" and sig.promise_id == 7 and sig.confidence == R.SAME_ACTION_CONF and "같은 행동 제목" in sig.rationale
    other = _inp("작년 산사태가 할퀸 곳…장마 앞두고 복구 완료", "가해자를 입건해 처벌하겠다")  # 다른 종류의 행동이면 받지 않는다
    assert R.RulesLLM(S(followup_same_action=True)).classify_followup(other).signal == "불명확"


def test_investigation_tag():
    inp = FollowupInput(event_summary="", promises=[PromiseRef(id=1, actor_org="경찰", action="야탑 복합상가 화재 원인을 수사하겠다")],
                        article_title="경찰, 야탑 복합상가 화재 종업원 입건", hilight="", published_at="2025-01-13")
    sig = R.RulesLLM(S()).classify_followup(inp)
    assert sig.signal == "진행" and R.INVESTIGATION_TAG in sig.rationale


def test_recurrence_article():
    ev = Event(key="k", title="울산 청량나들목 램프 교통사고 다발 구간", facility="청량나들목", occurred_at=date(2023, 10, 31))
    late = Article(news_id="n1", title="울산 청량나들목 램프 구간에서 트레일러 넘어져", published_at=date(2026, 2, 28))
    early = Article(news_id="n2", title="울산 청량나들목 램프 구간에서 트레일러 넘어져", published_at=date(2023, 11, 20))
    other = Article(news_id="n3", title="청량나들목 정체 개선 용역 발주", published_at=date(2025, 8, 11))
    assert J.recurrence_article(ev, late) and not J.recurrence_article(ev, early) and not J.recurrence_article(ev, other)
    generic = Event(key="g", title="공사장 붕괴", facility="공사장", occurred_at=date(2023, 1, 1))
    assert not J.recurrence_article(generic, Article(news_id="n4", title="공사장 붕괴 또 사고", published_at=date(2024, 1, 1)))  # 일반 시설어는 아니다


def test_investigation_source():
    assert J.investigation_source(Event(key="a", title="t", source_quotation="합동 현장감식으로 화재 원인을 규명하겠다"))
    assert not J.investigation_source(Event(key="b", title="t", source_quotation="숨은 영웅들을 칭찬합니다"))


def test_rules_v5_model_name():
    assert R.RulesLLM(S()).model == "rules-v4"
    assert R.RulesLLM(S(followup_phrase_fixes=True)).model == "rules-v6"  # 10-30 D1: 구절 가드가 바뀌어 캐시 무효화
    assert R.RulesLLM(S(followup_early_problem_days=3)).model == "rules-v5"
