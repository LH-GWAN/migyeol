"""진행 단계 '발생' — 발생 직후 기사 + 사고 사실을 확인·보도한 기사 (2026-10-30, docs/EXPERIMENT.md §15).

- link.occurrence_report / occurrence_window / initial_phase: 발생 +3일 안은 모두 '발생', +14일까지는 제목이 사상자·신원·피해 규모·경위를 보도하고
  원인조사·대응·후속·의견 표현이 없을 때만 '발생'.
- link.gate: 발생 창 안에서는 범위(지역·시설·기관) + 사건 키워드 1개면 주제 조건 통과 (사례: 영도 청동초 스쿨존 사고 당일 보도는 시설명이 없다).
- serializers.event_detail: 발생 +3일 안의 '대응발표' 기사(원천 기사 포함)는 단계는 그대로 두고 타임라인 '발생' 에도 보인다.
- 시드 occurrence_query → collect_occurrence 가 발생 창만 더 받아 후보 출처 'occurrence' 로 넣는다.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.serializers import event_detail
from app.bigkinds.schemas import NewsDocument, NewsSearchResult
from app.config import Settings
from app.db.models import Article, Base, Event, EventArticle, EventCandidate
from app.pipeline.collect import collect_occurrence
from app.pipeline.link import gate, initial_phase, occurrence_report, occurrence_terms, occurrence_window
from app.pipeline.seeds import load_seeds, upsert_events

S = Settings(_env_file=None)
OCC = date(2023, 4, 28)
_IDS = iter(range(5000, 9999))


def ev(**kw) -> Event:
    base = dict(
        id=next(_IDS), key="t", title="", incident_type="사고>교통사고", region="", facility="", responsible_org="", org_aliases=[], keywords=[],
        expansions=[], promise_terms=[], occurred_at=OCC, seed_query="", query_override="", occurrence_query="", summary="", term_weights={},
        source_news_id=None,
    )
    base.update(kw)
    return Event(**base)


def art(title: str, days: int, hilight: str = "", cat: list[str] | None = None, news_id: str | None = None) -> Article:
    return Article(
        news_id=news_id or title[:20], title=title, hilight=hilight, content_200="", tms_keywords=[], published_at=OCC + timedelta(days=days),
        category_incident=cat or [], provider="테스트", change_status="ok",
    )


YEONGDO = dict(
    title="부산 영도구 청동초 스쿨존 어린이 사망 사고", region="영도구", facility="청동초 스쿨존", responsible_org="영도구",
    org_aliases=["영도구청", "영도구청장", "부산 영도구"], keywords=["영도 스쿨존 참사", "청동초", "방호울타리"],
)


# ---------------------------------------------------------------- 사고 사실 보도
@pytest.mark.parametrize(
    "title",
    [
        "부산 영도구 스쿨존 10살 여아 참변… 지게차 기사 무면허 확인",  # 경위
        "서해 어선 사고 잇따라…인천·경기·충남서 4명 사망·2명 실종",  # 사상자 집계
        "숨진 작업자 2명 신원 확인…20대 하청 노동자",  # 신원
        "거제 침수 피해 눈덩이…주택 120채 잠겨",  # 피해 규모
        "CCTV 에 찍힌 사고 순간…화물 100m 굴러",  # 경위 첫 보도
    ],
)
def test_occurrence_report_facts(title):
    assert occurrence_report(title)


@pytest.mark.parametrize(
    "title",
    [
        "경찰, 영도 등굣길 초등생 사망사고 집중 수사",  # 수사
        "스쿨존 1.5톤 어망통 참변…경찰 '업무상 과실치사 검토'",  # 수사 방향
        "[사설] 부산 영도 등굣길 참변…통학로 안전 더 미룰 수 없다",  # 의견
        "'예서야 미안해' 청동초 학부모 침묵시위",  # 시위·후속
        "사망 사고 난 스쿨존, 차량용 펜스 설치",  # 대응 조치
        "숨진 노동자 합동분향소 마련",  # 추모
        "경상남도 호우 특보 해제",  # '경상' 은 경상자가 아니다
        "영도구 스쿨존 방호울타리 공사 시작",  # 사실 어휘 없음
    ],
)
def test_occurrence_report_excludes_followup_and_opinion(title):
    assert not occurrence_report(title)


@pytest.mark.parametrize(
    "days,title,hilight,phase",
    [
        (0, "지게차 낙하물 스쿨존 덮쳐…초등생 사망", "", "발생"),
        (2, "경찰, 사고 원인 조사 착수", "원인을 조사하고 있다", "발생"),  # +3일 안은 표현과 무관하게 발생
        (-1, "전날 예고된 위험", "", "발생"),
        (5, "부산 영도구 스쿨존 10살 여아 참변… 지게차 기사 무면허 확인", "경찰은 원인을 조사 중이다", "발생"),  # 하이라이트의 조사 문장은 보지 않는다
        (14, "숨진 작업자 2명 신원 확인", "", "발생"),
        (15, "숨진 작업자 2명 신원 확인", "", "기타"),  # 창 밖
        (5, "경찰, 업체 압수수색…원인 규명", "", "원인조사"),
        (5, "영도구, 관내 어린이보호구역 전면 개선", "", "기타"),
        (-2, "지게차 낙하물 스쿨존 덮쳐…초등생 사망", "", "기타"),
    ],
)
def test_initial_phase(days, title, hilight, phase):
    assert initial_phase(ev(), art(title, days, hilight), S) == phase


def test_occurrence_window_settings():
    a = art("숨진 작업자 2명 신원 확인", 10)
    assert occurrence_window(ev(), a, S)
    assert not occurrence_window(ev(), a, Settings(phase_confirm_days=7, _env_file=None))
    assert occurrence_window(ev(), art("현장 정리 계속", 3), S)
    assert not occurrence_window(ev(), art("현장 정리 계속", 3), Settings(phase_occurrence_days=2, _env_file=None))


# ---------------------------------------------------------------- 연결 게이트
def test_occurrence_terms_take_title_words():
    terms = occurrence_terms(ev(**YEONGDO))
    assert {"청동초 스쿨존", "청동초", "스쿨존"} <= set(terms)
    assert "영도구" not in terms and "사고" not in terms  # 지역·일반어는 사건 키워드가 아니다
    gj = occurrence_terms(ev(title="거제 고현천 하류 극한호우 침수", region="거제시", facility="고현천", keywords=["고현천"]))
    assert "침수" in gj and "호우" in gj  # 사건 제목의 재난어


def test_gate_occurrence_window_passes_first_report_without_facility():
    e = ev(**YEONGDO)
    first = art("지게차 낙하물 스쿨존 덮쳤다…10세 초등생 사망", 0, hilight="28일 오전 부산 영도구 청동초등학교 인근 어린이보호구역에서")
    ok, reasons = gate(e, first, S)
    assert ok and "occ-window" in reasons and "region" in reasons
    # 이전 게이트(끄면)로는 시설명·사건분류·핵심어 2개가 없어 탈락했다
    assert not gate(e, first, Settings(link_occurrence_gate=False, _env_file=None))[0]
    # 사고 사실 보도는 +14일까지
    late_fact = art("부산 영도구 스쿨존 10살 여아 참변… 지게차 기사 무면허 확인", 4, hilight="부산 영도구 청동초 앞")
    assert gate(e, late_fact, S)[0]
    # 창 밖·사실 보도 아님 → 이전과 같다
    assert not gate(e, art("스쿨존 사고 1년, 달라진 것 없다", 40, hilight="부산 영도구"), S)[0]
    assert not gate(e, art("영도구, 스쿨존 전면 개선 추진", 6, hilight="부산 영도구"), S)[0]


def test_gate_occurrence_window_still_needs_scope_and_keyword():
    e = ev(**YEONGDO)
    assert not gate(e, art("지게차 낙하물 스쿨존 덮쳤다…10세 초등생 사망", 0, hilight="부산의 한 초등학교 앞"), S)[0]  # 범위(영도구) 없음
    assert not gate(e, art("영도구 주민 체육대회 열려", 1, hilight="부산 영도구"), S)[0]  # 사건 키워드 없음


# ---------------------------------------------------------------- 화면 (serializer)
@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _link(s, e: Event, a: Article, phase: str) -> None:
    s.add(a)
    s.add(EventArticle(event_id=e.id, news_id=a.news_id, phase=phase, link_method="rule", link_score=0.9, link_reason="t", confirmed=False, needs_review=False, rejected=False))


def test_event_detail_shows_early_response_articles_in_occurrence(session):
    e = ev(**YEONGDO, key="yd")
    session.add(e)
    session.flush()
    _link(session, e, art("부산 영도구 등굣길 참변, 구청 '방호울타리 설치하겠다'", 1, news_id="src"), "대응발표")  # 원천 기사(발생 다음 날)
    _link(session, e, art("영도구, 스쿨존 방호울타리 예산 확보", 30, news_id="late"), "대응발표")
    _link(session, e, art("지게차 낙하물 스쿨존 덮쳐…초등생 사망", 0, news_id="occ"), "발생")
    session.commit()
    tl = {t["phase"]: [a["news_id"] for a in t["articles"]] for t in event_detail(session, e, date(2026, 10, 22))["timeline"]}
    assert tl["발생"] == ["occ", "src"]  # 발행일 순, 원천 기사도 '발생' 에 함께
    assert sorted(tl["대응발표"]) == ["late", "src"]  # '대응발표' 칸은 그대로
    assert "late" not in tl["발생"]


def test_event_detail_empty_occurrence_stays_empty(session):
    e = ev(**YEONGDO, key="yd2")
    session.add(e)
    session.flush()
    _link(session, e, art("영도구, 스쿨존 방호울타리 예산 확보", 30, news_id="late2"), "대응발표")
    session.commit()
    d = event_detail(session, e, date(2026, 10, 22))
    tl = {t["phase"]: t["articles"] for t in d["timeline"]}
    assert tl["발생"] == [] and d["card"]["occurred_at"] == OCC.isoformat()  # 화면은 발생일과 '첫 보도 미연결' 을 보여 준다


# ---------------------------------------------------------------- 시드 occurrence_query · 수집
def test_seed_occurrence_query_round_trip(session, tmp_path):
    p = tmp_path / "events.yaml"
    p.write_text(
        """events:
- key: k1
  title: 부산 영도구 청동초 스쿨존 어린이 사망 사고
  incident_type: 사고>교통사고
  region: 영도구
  facility: 청동초 스쿨존
  occurred_at: '2023-04-28'
  query: ("청동초") AND ("영도구")
  occurrence_query: ("영도") AND ("스쿨존" OR "등굣길")
  exclude_terms:
  - 백령도
- key: k2
  title: 다른 사건
  incident_type: 사고>교통사고
  region: 가온시
  facility: 가온교
  occurred_at: '2023-05-01'
""",
        encoding="utf-8",
    )
    seeds = load_seeds(p)
    assert seeds[0].occurrence_query == '("영도") AND ("스쿨존" OR "등굣길")' and seeds[1].occurrence_query == ""
    evs = upsert_events(session, seeds)
    assert evs[0].occurrence_query == seeds[0].occurrence_query and evs[1].occurrence_query == ""
    assert seeds[0].exclude_terms == ["백령도"] and evs[0].exclude_terms == ["백령도"] and evs[1].exclude_terms == []


class _FakeClient:
    mode = "mock"

    def __init__(self, docs):
        self.docs = docs
        self.args = []

    def search_news(self, arg):
        self.args.append(arg)
        return NewsSearchResult(total_hits=len(self.docs), documents=self.docs[arg.return_from : arg.return_from + arg.return_size])


def test_collect_occurrence_adds_window_candidates(session):
    e = ev(**YEONGDO, key="yd3", occurrence_query='("영도") AND ("스쿨존")')
    session.add(e)
    session.flush()
    docs = [
        NewsDocument(news_id="n1", title="지게차 낙하물 스쿨존 덮쳐…초등생 사망", published_at="2023-04-28T09:00:00+09:00", provider="가"),
        NewsDocument(news_id="n2", title="부산서 굴러온 화물에 등굣길 아동 사망", published_at="2023-04-28T10:00:00+09:00", provider="나"),
    ]
    client = _FakeClient(docs)
    out = collect_occurrence(session, client, e, e.occurrence_query, None, date(2026, 10, 22))
    assert out["fetched"] == 2 and out["new"] == 2 and len(client.args) == 1
    assert client.args[0].published_at.dump() == {"from": "2023-04-27", "until": "2023-05-13"}  # 발생 −1 ~ +14일
    rows = session.query(EventCandidate).filter_by(event_id=e.id).all()
    assert {(r.news_id, r.source) for r in rows} == {("n1", "occurrence"), ("n2", "occurrence")}


# ---------------------------------------------------------------- 후속 판정·가중치에 미치는 영향 차단
def test_occurrence_gate_links_are_not_followup_evidence():
    from app.pipeline.judge import occurrence_gate_link, usable_followup_link

    def lk(reason: str, **kw) -> EventArticle:
        base = dict(event_id=1, news_id="n", phase="발생", link_method="embedding", link_score=0.9, link_reason=reason, confirmed=False, needs_review=False, rejected=False)
        base.update(kw)
        return EventArticle(**base)

    occ = lk("region+occ-window+p=0.90+s=0.70+w=0.40")
    assert occurrence_gate_link(occ) and not usable_followup_link(occ, S)  # '호우 특보 해제' 같은 사건 전개를 완료로 읽지 않게
    assert usable_followup_link(lk("region+incident+p=0.90+s=0.70+w=0.40"), S)
    assert not occurrence_gate_link(lk("region+occ-window+p=0.90", confirmed=True))  # 관리자 확정은 그대로
    assert not occurrence_gate_link(lk("region+occ-windowX"))


def test_core_documents_skip_occurrence_candidates(session):
    from app.pipeline.link import core_documents

    e = ev(**YEONGDO, key="yd4")
    session.add(e)
    session.flush()
    seed = art("영도구 청동초 스쿨존 사고 지게차 화물", 0, hilight="부산 영도구 청동초 스쿨존", cat=["사고>교통사고>교통사고"], news_id="seed1")
    broad = art("영도구 청동초 스쿨존 폭우 침수 정전", 0, hilight="부산 영도구 청동초 스쿨존", cat=["사고>교통사고>교통사고"], news_id="broad1")
    session.add_all([seed, broad, EventCandidate(event_id=e.id, news_id="seed1", source="seed"), EventCandidate(event_id=e.id, news_id="broad1", source="occurrence")])
    session.commit()
    docs = core_documents(session, e)
    assert any("지게차" in d for d in docs)
    assert not any("정전" in d for d in docs)  # 발생 창 보강 후보는 사건 고유어 가중치를 흐리지 않는다


def test_extract_skips_quotes_from_occurrence_gate_links(session, monkeypatch):
    """발생 창 완화 연결의 인용문은 약속 추출 대상이 아니다 (판정 재료는 이전 게이트로 붙은 연결에서만)."""
    from app.bigkinds.schemas import QuotationDocument
    from app.pipeline import extract as X

    e = ev(**YEONGDO, key="yd5")
    session.add(e)
    session.flush()
    _link(session, e, art("부산 영도구 등굣길 참변", 0, news_id="ok1"), "발생")
    _link(session, e, art("부산 영도구 스쿨존 사망", 0, news_id="occ1"), "발생")
    session.get(EventArticle, {"event_id": e.id, "news_id": "occ1"}).link_reason = "region+occ-window+p=0.90"
    session.commit()
    docs = [
        QuotationDocument(news_id=n, source="영도구청장", quotation="스쿨존에 방호울타리를 설치하겠다", published_at="2023-04-28")
        for n in ("ok1", "occ1")
    ]
    monkeypatch.setattr(X, "fetch_quotations", lambda *a, **k: (docs, []))

    class _LLM:
        model = "rules-test"
        mode = "rules"

        def extract_promise(self, inp, key=""):
            raise X.LLMFailure("stop")  # 인용문 저장 단계만 본다

    out = X.extract_event(session, None, e, _LLM(), S)
    assert out["linked_quotes"] == 1
    assert {q.news_id for q in e.quotations} == {"ok1"}


def test_gate_exclude_terms_drop_other_incident_in_same_area():
    """시드 제외어 — 옹진 어선 전복(2025-12-30) 사건에 붙던 백령도 모터보트 전복(2026-01-05) 기사를 뺀다."""
    ongjin = dict(
        title="옹진 서해 해상 어선 전복 사망사고", region="옹진군", facility="어선", responsible_org="해양경찰청", org_aliases=["해경", "인천해경"],
        keywords=["어선 전복", "서해 어선 사고"], occurred_at=date(2025, 12, 30),
    )
    other = Article(news_id="b1", title="인천 백령도 해상서 모터보트 전복…50대 1명 사망", hilight="인천 옹진군 백령도 해상에서 해경", content_200="",
                    tms_keywords=[], published_at=date(2026, 1, 5), category_incident=["사고>교통사고>해양사고"])
    real = Article(news_id="r1", title="서해 어선 사고 잇따라…인천·경기·충남서 4명 사망·2명 실종", hilight="옹진군 해상 어선 전복 해경", content_200="",
                   tms_keywords=[], published_at=date(2025, 12, 30), category_incident=["사고>교통사고>해양사고"])
    assert gate(ev(**ongjin), other, S)[0]  # 제외어가 없으면 같은 지역·같은 재난어라 통과했다
    e = ev(**ongjin, exclude_terms=["백령도", "모터보트"])
    assert gate(e, other, S) == (False, ["x-exclude=백령도"])
    assert gate(e, real, S)[0]  # 진짜 사고 기사는 그대로
