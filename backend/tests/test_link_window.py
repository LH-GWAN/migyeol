"""연결 게이트의 기간 창·지역 필수 조건(link.scope_checks) — 단위 규칙과 실험 DB 회귀 사례.

회귀 사례는 실험 DB(시드 90건) 의 실제 사건 정의·가중치 일부와 기사 제목이다. 진짜 후속(부산 만덕 대심도 2026 준공·개통·지반 침하,
영도구 2024-12 방호울타리 완료, 현대시장 2024-07 복구 완료)은 창·지역 조건 뒤에도 통과해야 한다.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.config import Settings
from app.db.models import Article, Event
from app.pipeline.link import capped, event_scope, gate, new_incident_title, other_region_in_title, scope_checks
from app.rules import regions as R

S = Settings(_env_file=None)
_IDS = iter(range(1000, 9999))


def ev(**kw) -> Event:
    base = dict(
        id=next(_IDS), key="t", title="", incident_type="사고>산업사고", region="", facility="", responsible_org="", org_aliases=[], keywords=[],
        expansions=[], promise_terms=[], occurred_at=date(2024, 1, 1), seed_query="", query_override="", summary="", term_weights={},
        source_news_id=None,
    )
    base.update(kw)
    return Event(**base)


def art(title: str, published: date, hilight: str = "", kws: list[str] | None = None, cat: list[str] | None = None) -> Article:
    return Article(news_id=title[:20], title=title, hilight=hilight, content_200="", tms_keywords=kws or [], published_at=published, category_incident=cat or [])


MANDEOK = dict(
    title="부산 만덕~센텀 대심도 터널 공사장 토사 붕괴", region="부산시", facility="만덕~센텀 대심도 터널 공사장", responsible_org="부산시",
    org_aliases=["부산광역시", "부산시청", "부산시 건설본부"], keywords=["대심도", "만덕~센텀", "토사 붕괴", "도시철도 3호선"], occurred_at=date(2023, 2, 25),
    promise_terms=["매뉴얼", "변화", "발생", "긴급"],
    term_weights={"미남": 1.0, "대심": 0.876, "미남역": 0.801, "도시철도": 0.798, "만덕": 0.778, "토목학회": 0.726, "자문단": 0.664, "대심도": 0.6, "만덕~센텀": 0.6},
)
YEONGDO = dict(
    title="부산 영도구 청동초 스쿨존 어린이 사망 사고", incident_type="사고>교통사고", region="영도구", facility="청동초 스쿨존", responsible_org="영도구",
    org_aliases=["영도구청", "영도구청장", "부산 영도구"], keywords=["영도 스쿨존 참사", "청동초", "방호울타리"], occurred_at=date(2023, 4, 28),
    promise_terms=["부산시교육청", "종합대책", "폐회로텔레비전", "우선순위"],
    term_weights={"굴러": 1.0, "방호울타리": 0.963, "청동초등학교": 0.833, "청동초": 0.803, "스쿨존": 0.777, "초등학생": 0.75, "화물": 0.634},
)
HYUNDAI = dict(
    title="인천 동구 현대시장 화재", region="인천 동구", facility="현대시장", responsible_org="인천 동구", org_aliases=["동구청", "인천시 동구", "인천"],
    keywords=["현대시장", "전통시장 화재", "피해복구"], occurred_at=date(2023, 3, 4), promise_terms=["화재", "위해", "상인", "연소"],
    term_weights={"현대시장": 1.0, "현대": 0.759, "송림동": 0.698, "점포": 0.632, "방화": 0.338, "상인": 0.331},
)
HYEONGSAN = dict(
    title="포항 형산강 하구 모터보트 좌초", incident_type="사고>교통사고", region="포항시", facility="형산강 하구", responsible_org="포항해양경찰서",
    org_aliases=["포항해경"], keywords=["형산강", "모터보트 좌초", "석축"], occurred_at=date(2024, 6, 30), promise_terms=["아래", "암초", "있음"],
    term_weights={"하구": 1.0, "모터보트": 0.933, "형산강": 0.876, "석축": 0.873, "포항해경": 0.841, "암초": 0.838, "좌초": 0.755},
)
GONGJU = dict(
    title="충남 공주 금강 범람 주택가 침수", incident_type="재해>자연재해", region="공주시", facility="금강", responsible_org="대통령실",
    keywords=["공주 침수", "금강 범람", "특별재난지역", "극한호우"], occurred_at=date(2023, 7, 15),
    term_weights={"금강": 1.0, "공산성": 0.6, "옥룡동": 0.589, "홍수경보": 0.506, "금강교": 0.502},
)


# ---------------------------------------------------------------- 지역 사전
@pytest.mark.parametrize(
    "name,base,parents,wide",
    [
        ("성남시", "성남", {"경기"}, False), ("경북", "경북", {"경북"}, True), ("경상북도", "경북", {"경북"}, True), ("서울시", "서울", {"서울"}, True),
        ("인천 동구", "동구", {"인천"}, False), ("부산 사하구", "사하", {"부산"}, False), ("제주시", "제주", {"제주"}, False), ("울주군", "울주", {"울산"}, False),
        ("가온시", "가온", set(), False),
    ],
)
def test_region_hierarchy(name, base, parents, wide):
    assert R.region_base(name) == base
    assert R.region_parents(name) == parents
    assert R.is_wide_region(name) is wide


def test_common_word_place_names_are_not_other_regions():
    # '예산을'·'강화'·'진도 4' 같은 보통명사 겹침은 다른 지역으로 보지 않는다
    sc = event_scope(ev(**GONGJU), S)
    assert other_region_in_title(sc, "복구 예산 강화…진도 4 지진 대비") is None
    assert other_region_in_title(sc, "청주 무심천·미호강 홍수경보") == "청주"
    assert other_region_in_title(sc, "충남 공주 금강 수위 낮아져") is None


# ---------------------------------------------------------------- ① 창 안은 이전 게이트 그대로
def test_inside_window_unchanged():
    e = ev(**GONGJU)
    a = art("공주시 옥룡동 침수 주택 복구 한창", date(2023, 7, 20), kws=["공주", "침수"], cat=["재해>자연재해"])
    ok, reasons = gate(e, a, S)
    assert ok and not any(r.startswith(("x-", "cap-")) for r in reasons)


def test_day_before_occurrence_only():
    e = ev(**GONGJU)
    assert gate(e, art("공주 금강 홍수경보", date(2023, 7, 14), cat=["재해>자연재해"]), S)[0]
    assert gate(e, art("공주 금강 홍수경보", date(2023, 7, 13), cat=["재해>자연재해"]), S)[1] == ["date"]


# ---------------------------------------------------------------- ② 창 뒤: 사건 고유어 필수
def test_late_article_needs_unique_term():
    e = ev(**HYUNDAI)
    late = date(2024, 1, 7)  # 발생 + 309일
    # 지역어 + 일반어만: 탈락
    assert "x-late" in scope_checks(e, art('[신년인터뷰] 인천 동구의회 의장 "주민 친화적 의회로 도약"', late, hilight="동구 화재 피해 상인"), S)
    # 창을 끄면(LINK_EARLY_DAYS=0) 이전 동작
    assert "x-late" not in scope_checks(e, art('[신년인터뷰] 인천 동구의회 의장 "주민 친화적 의회로 도약"', late, hilight="동구 화재 피해 상인"), S.model_copy(update={"link_early_days": 0}))


def test_late_hilight_only_counts_with_region_in_title():
    e = ev(**HYUNDAI)
    late = date(2023, 12, 29)
    # 제목에 지역어·고유어가 없고 하이라이트(질의어 주변)에만 시설명 — title_region 기본값에서 탈락, title_hilight 에서는 통과
    a = art("두산건설 골프단, 버디기금 기부", late, hilight="인천 동구 현대시장 화재 피해 상인에게")
    assert "x-late" in scope_checks(e, a, S)
    assert "x-late" not in scope_checks(e, a, S.model_copy(update={"link_late_match": "title_hilight"}))


def test_late_recurrence_capped_to_review():
    e = ev(**HYEONGSAN)
    a = art("포항 형산강서 3t급 어선 좌초…70대 선장 구조", date(2026, 1, 8), hilight="포항시 남구 형산강 하구", kws=["형산강", "좌초"])
    checks = scope_checks(e, a, S)
    assert "cap-recur" in checks and capped(checks)
    ok, reasons = gate(e, a, S)
    assert ok and capped(reasons)  # 폐기가 아니라 검토 큐 이하 (decide 의 other_hit)
    # 돌아보는 말(복구·원인·재판 …)이 있으면 새 사고 속보가 아니다
    assert not new_incident_title("형산강 좌초 사고 1년…해경 암초 표지 설치 완료")
    # 약속 조치 구간을 가리키는 재발은 후속(새로운 문제)으로 남긴다
    assert "cap-recur" not in scope_checks(e, art("형산강 하구 보강 구간 옆 또 좌초…1명 구조", date(2026, 1, 8)), S)


# ---------------------------------------------------------------- ③ 지역 필수
def test_basic_region_required_or_facility():
    e = ev(**GONGJU)
    d = date(2023, 7, 20)
    assert "x-region" in scope_checks(e, art("극한호우 피해 속출, 하천 범람", d, hilight="곳곳 침수"), S)
    assert "x-region" not in scope_checks(e, art("금강 범람 주택가 복구", d), S)  # 지역어 없이 시설명으로 대체


def test_wide_region_needs_unique_term():
    e = ev(**MANDEOK)  # 부산시 = 광역
    d = date(2023, 3, 2)
    assert "x-wide-unique" in scope_checks(e, art("부산시, 건설 현장 특별 안전 점검", d, hilight="부산 공사장 붕괴 사고 이후"), S)
    assert "x-wide-unique" not in scope_checks(e, art("대심도 공사장 붕괴 원인 조사", d), S)


# ---------------------------------------------------------------- ④ 다른 지역
def test_other_region_in_title_drops():
    e = ev(**GONGJU)
    d = date(2023, 7, 15)
    assert any(c.startswith("x-other-region") for c in scope_checks(e, art("청주 무심천·미호강 홍수경보… 수위 4.45m", d, hilight="금강 홍수경보"), S))
    # 상위 광역(충남)이 제목에 있으면 다른 지역이 함께 있어도 남긴다
    assert not any(c.startswith("x-other-region") for c in scope_checks(e, art("대전·세종·충남 산사태로 5명 사망", d, hilight="공주 금강"), S))


def test_other_city_acting_on_the_event_is_kept():
    e = ev(
        title="제주 비양도 해상 금성호 침몰", region="제주시", facility="135금성호", keywords=["금성호", "어선 침몰", "비양도"], occurred_at=date(2024, 11, 8),
        term_weights={"금성호": 1.0, "침몰": 0.774, "비양도": 0.7, "135금성호": 0.6},
    )
    assert not any(c.startswith("x-") for c in scope_checks(e, art("부산시, 금성호 침몰 사고 피해자 지원 전담 공무원 배치", date(2024, 11, 10)), S))


# ---------------------------------------------------------------- ⑤ 다른 구체 장소
def test_other_place_with_incident_capped():
    e = ev(
        title="이천 물류센터 대형 화재", region="이천시", facility="물류센터", keywords=["이천 물류센터", "물류센터 화재"], occurred_at=date(2025, 5, 13),
        term_weights={"물류센터": 0.9, "부발읍": 0.7},
    )
    checks = scope_checks(e, art("이천 부발읍 냉동창고 화재…인천 쿠팡센터와 다른 곳", date(2025, 5, 14)), S)
    assert not any(c.startswith("x-") for c in checks)
    assert not any(c.startswith("cap-place=서울시장") for c in scope_checks(e, art("이천 화재 현장 찾은 서울시장", date(2025, 5, 14)), S))


def test_scope_rules_off_is_previous_behaviour():
    off = S.model_copy(update={"link_scope_rules": False})
    e = ev(**GONGJU)
    assert scope_checks(e, art("청주 무심천·미호강 홍수경보", date(2023, 7, 15)), off) == []


# ---------------------------------------------------------------- 회귀: 진짜 후속 기사는 창·지역 조건 뒤에도 통과
@pytest.mark.parametrize(
    "title,published",
    [
        ("[대담한K] ‘대심도’ 준공…“만덕~센텀 10분대 이동”", date(2026, 1, 27)),
        ("40분 걸리던 만덕~센텀 10분대로…부산 대심도 고속화도로 개통", date(2026, 2, 10)),
        ("GS건설, 부산 최초 대심도 ‘만덕~센텀 고속화도로’ 개통…“40분 거리 10분대로”", date(2026, 2, 11)),
        ("만덕~센텀 공사 인근 지반 침하… 내성지하차도 전면 통제", date(2026, 4, 5)),
        ("부산 만덕~센텀 고속화도로 또 ‘지반침하’", date(2026, 4, 6)),
        ("부산 내성·수영강변지하차도 침하 복구 완료...대심도 개통 두 달만에 공사 안전 우려 확산", date(2026, 4, 6)),
        ("부산 지하차도서 침하…전면 통제 후 하루 만에 복구", date(2026, 4, 6)),
        ("\"두 달 만에 안전 붕괴\"…대심도 지반 침하에 '부산시 책임론'", date(2026, 4, 7)),
        ("부산 대심도 지하차도 인근 한달만에 또 지반 침하", date(2026, 5, 18)),
    ],
)
def test_regression_mandeok_2026(title, published):
    e = ev(**MANDEOK)
    a = art(title, published, hilight="만덕~센텀 대심도 지하차도", kws=["대심도", "부산"])
    checks = scope_checks(e, a, S)
    assert not any(c.startswith("x-") for c in checks), checks
    assert not capped(checks), checks


@pytest.mark.parametrize(
    "title,published",
    [
        ("부산 ‘스쿨존 사망’ 1년 반 훌쩍…‘방호울타리’ 설치 34% 그쳐", date(2024, 11, 13)),
        ("'부산 영도구 등굣길 참변' 청동초에 방호울타리 설치 완료", date(2024, 12, 24)),
        ("더 이상 참사 없길… 방호 울타리 들어선 영도구 등굣길", date(2024, 12, 23)),
    ],
)
def test_regression_yeongdo_2024(title, published):
    e = ev(**YEONGDO)
    a = art(title, published, hilight="청동초 방호울타리", kws=["영도구", "방호울타리"])
    checks = scope_checks(e, a, S)
    assert not any(c.startswith("x-") for c in checks) and not capped(checks), checks


@pytest.mark.parametrize(
    "title",
    [
        "화재로 탔던 인천 동구 현대시장 1년 3개월만에 완전 복구",
        "인천 동구, 현대시장 화재 피해 시설물 복구 완료",
        "인천동구 현대시장 화재복구 완료 시장활성화 기대",
        "인천 동구, 현대시장 화재 복구 완료",
    ],
)
def test_regression_hyundai_market_2024(title):
    e = ev(**HYUNDAI)
    a = art(title, date(2024, 7, 2), hilight="현대시장 화재 복구", kws=["현대시장", "인천"])
    ok, reasons = gate(e, a, S)
    assert ok and not capped(reasons), reasons


# ---------------------------------------------------------------- 검토 큐는 집계·후속 신호에서 빠진다
from tests.test_pipeline_behaviors import fresh  # noqa: E402,F401 — 공용 픽스처


def test_review_queue_links_feed_no_followup_or_metrics(fresh):  # noqa: F811
    """검토 큐(needs_review) 연결은 후속 기사·추적 약속·실험 집계·카드 기사 수 어디에도 들어가지 않는다."""
    from app.api.serializers import active_links
    from app.experiment.metrics import collect_facts
    from app.pipeline.judge import followup_articles, trackable_promises

    session, pipe, settings = fresh
    ev = session.query(Event).filter_by(key="gaon-underpass-flood").one()
    fups = followup_articles(session, ev, settings)
    assert fups, "픽스처 사건에 후속 기사가 있어야 비교가 된다"
    target = fups[-1].news_id
    link = next(l for l in ev.links if l.news_id == target)
    link.needs_review = True
    for p in ev.promises:
        if p.evidence_news_id == target:
            p.merged_news_ids = []
    session.commit()
    session.refresh(ev)
    assert target not in {a.news_id for a in followup_articles(session, ev, settings)}
    assert target not in {l.news_id for l in active_links(ev)}
    assert all(p.evidence_news_id != target for p in trackable_promises(ev))
    facts = {f.event.key: f for f in collect_facts(session, settings, keys={ev.key})}
    f = facts[ev.key]
    assert target not in {a.news_id for a in f.linked} and target not in {a.news_id for a in f.followups}
    assert all(s.news_id != target for s, _ in f.valid)
