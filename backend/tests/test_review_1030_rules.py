"""2026-10-30 버그 목록(코드리뷰_2026-10-30_버그.md) A 항목 — 문서의 예문·사례를 그대로 고정한 단위 회귀 테스트.
DB 사본이 필요한 검사(금지어 0건, 사건 160 발생일, 데모 사건 화면)는 tests/test_review_1030_db.py."""

from __future__ import annotations

import re
from datetime import date

import pytest

from app.llm import rules_adapter as R
from app.llm.rules_adapter import actor_from_source, completion_blocked, span_truncated_prefix
from app.rules.actors import is_public_actor, normalize_actor_org

# ----------------------------------------------------------------------------- A4 (rules-5) '완료' 오독
MISREAD_AS_DONE = [
    ("177 당부", "홍준표 대구시장은 군위군 대율리 지방하천 수해 복구현장을 방문해 공사 관계자들을 격려하고 조속한 복구 완료를 당부했다"),
    ("272 부정", "가평군은 아직 지난 폭우로 인한 실종자 수색과 수해 복구 작업이 끝나지 않았다"),
    ("281 조건", "수사 결과 옹벽 붕괴 원인이 시의 관리 소홀로 드러나면, 이 시장에 대한 형사 처벌이 이뤄질 것으로 전망된다"),
    ("269 촉구", "사상~하단선 공사 현장 인근 주민들이 조속한 피해 보상과 완공을 촉구하고 나섰다"),
    ("269 이유", "사상하단선 구간에 지하철 공사를 위해 2023년 새로운 상수도관으로 교체했기 때문에 노후화되지 않았다"),
    ("18 부정", "한국타이어는 아직 대전공장 화재 관련 보험금 청구 절차를 끝내지 않았다"),
    ("18 발언 종료", "한국타이어 공장 이전에 우리 충남이 적극적으로 나서주시길 당부드린다 며 발언을 마쳤다"),
    ("18 부정 결과", "2차 화재의 경우 발화 특이점이 식별되지 않아 구체적인 발화부 규명이 어려운 것으로 확인됐다"),
    ("158 효과 없음", "인천대교는 투신을 막기 위해 2022년 11월 주정차 방지용 플라스틱 드럼통 1500개를 설치했으나 효과가 없었다"),
    ("임박", "다음 주 복구 완료를 앞두고 막바지 점검이 한창이다"),
]
STILL_DONE = [
    "장마 앞두고 복구 완료",
    "전 구간 라이닝 보강을 완료했으며 안전 점검도 마쳤다",
    "배수펌프 증설 공사를 마쳤다",
    "작업대 선로 침범이 직접 원인으로 드러났다",
    "노후 상수도관 교체를 완료했다",
]


@pytest.mark.parametrize("label,sentence", MISREAD_AS_DONE)
def test_demand_negation_condition_are_not_completion(label, sentence):
    sigs = [s for s, _ in R._expressions(sentence, fixes=True, headline=True, phrase=True)]
    assert "완료" not in sigs, (label, sigs)


@pytest.mark.parametrize("sentence", STILL_DONE)
def test_real_completion_still_detected(sentence):
    sigs = [s for s, _ in R._expressions(sentence, fixes=True, headline=True, phrase=True)]
    assert "완료" in sigs, sentence


def test_imminent_completion_counts_as_progress():
    sigs = [s for s, _ in R._expressions("복구 완료를 앞두고 있다", fixes=True, headline=True, phrase=True)]
    assert sigs == ["진행"]


def test_completion_blocked_reasons():
    def why(text, needle):
        m = re.search(needle, text)
        assert m
        return completion_blocked(text, m)

    assert why("조속한 복구 완료를 당부했다", "완료") == "요구·당부"
    assert why("복구 작업이 끝나지 않았다", "끝") == "부정"
    assert why("원인이 관리 소홀로 드러나면 처벌된다", "드러나") == "조건"
    assert why("상수도관으로 교체했기 때문에 노후화되지 않았다", "교체했") == "이유·배경"
    assert why("드럼통을 설치했으나 효과가 없었다", "설치했") == "효과 없음"
    assert why("당부드린다 며 발언을 마쳤다", "마쳤") == "발언 종료"
    assert why("규명이 어려운 것으로 확인됐다", "확인됐") == "부정"
    assert why("보강 공사를 완료했다", "완료했") is None


# ----------------------------------------------------------------------------- A5 (press-5) 발췌 어절 경계
LONG_288 = (
    "국정자원 화재 감사 결과 전기공사 담당자 조사 결과 무등록업체가 화재 원인이 된 리튬배터리 이전 설치 전기 공사를 한 것으로 확인됐다..감사 결과 "
    "국정자원 전산실 내 배터리 재배치 공사 과정에서 국정자원의 관리·감독이 허술했던 점이 드러났다"
)


def test_window_cuts_at_word_boundary_and_keeps_substring():
    m = re.search("확인됐", LONG_288)
    w = R._window(LONG_288, m)
    assert len(w) <= R.MAX_SPAN_CHARS and w in LONG_288
    assert "등록업체" not in w or "무등록업체" in w  # '무등록업체' 가 '등록업체' 로 잘리지 않는다
    assert not w.startswith("등록업체")
    assert LONG_288[LONG_288.index(w) - 1] in R._WORD_BREAK  # 어절 시작에서 잘렸다


def test_truncated_span_gets_ellipsis_only_when_not_a_sentence_start():
    assert span_truncated_prefix("무등록업체가 화재 원인이 된", LONG_288) == "…"
    assert span_truncated_prefix("국정자원 화재 감사 결과 전기공사", LONG_288) == ""
    assert span_truncated_prefix("", LONG_288) == ""


# ----------------------------------------------------------------------------- A7 (rules-2·fe-1·press-1) 주체 기관
@pytest.mark.parametrize("source", ["김 지사", "김문수 장관", "이 장관", "원 장관", "김 군수", "오 부지사", "박 팀장", "사조위 위원장", "후 박 위원장"])
def test_unnormalizable_person_sources_become_unknown_org(source):
    assert actor_from_source(source, "행정안전부") is None  # 저장 시 "" = 기관 미상


@pytest.mark.parametrize("source,org", [("김주수 의성 군수", "의성군"), ("김문수 고용부 장관", "고용노동부"), ("김영훈 고용노동부 장관", "고용노동부"), ("원희룡 국토교통부 장관", "국토교통부")])
def test_known_org_in_source_is_still_extracted(source, org):
    assert actor_from_source(source, "행정안전부") == org


@pytest.mark.parametrize("source", ["행정부시장", "김동연 부시장", "부군수", "김 부구청장"])
def test_deputy_titles_do_not_produce_bogus_orgs(source):
    assert normalize_actor_org(source) in (None,) and is_public_actor(source)
    assert normalize_actor_org(source, "오산시", ["오산시청"]) in (None, "오산시")
    assert (normalize_actor_org(source) or "") not in ("부시", "부군", "부구", "행정부시")


def test_deputy_title_with_org_keeps_org():
    assert normalize_actor_org("오산시 부시장", "오산시", ["오산시청"]) == "오산시"


@pytest.mark.parametrize("source,org,expected", [("시 관계자", "성남시", "성남시"), ("군 관계자", "거창군", "거창군"), ("군 관계자", "공군", "공군"), ("시 관계자", "경찰", None)])
def test_generic_region_official_maps_to_responsible_org(source, org, expected):
    assert normalize_actor_org(source, org, [org + "청"]) == expected


def test_generic_region_official_maps_to_event_region_when_org_differs():
    assert normalize_actor_org("시 관계자", "국토교통부", ["오산시", "국토교통부", "국토부"]) == "오산시"


# ----------------------------------------------------------------------------- B1 (rules-3) 별칭 왼쪽 경계
def test_alias_requires_left_boundary():
    assert normalize_actor_org("김진열 군위군수", "행정안전부", ["행안부", "위군"]) != "행정안전부"
    assert normalize_actor_org("김진열 군위군수", "군위군", ["군위군청"]) == "군위군"
