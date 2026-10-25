"""2단계 후속 신호 보강 (docs/EXPERIMENT.md §10) — 표현 고침 · 기준일(source) · 재순위 확률 바닥 · 비교만 한 규칙 스위치."""

from __future__ import annotations

import os
from datetime import date

import pytest

from app.config import Settings, get_settings
from app.db.models import Article, Event, EventArticle, FollowupSignal
from app.llm import rules_adapter as R
from app.llm.schemas import FollowupInput, FollowupSignal as SignalOut, PromiseRef
from app.pipeline import judge as J


def S(**kw) -> Settings:
    base = dict(_env_file=None, followup_expression_fixes=False, followup_cutoff="grace", followup_min_link_prob=0.0)
    base.update(kw)
    return Settings(**base)


# ---------------------------------------------------------------- 표현 고침
@pytest.mark.parametrize(
    "text,before,after",
    [
        ("안양농수산물시장 청과동, 내년 2월 재개장", ["완료"], []),
        ("오는 3일 재개통 예정인 도로", ["완료", "진행"], ["진행"]),  # 앞날의 재개통은 완료가 아니라 진행(예정)
        ("현대시장 3년 만에 재개장", ["완료"], ["완료"]),
        ("화재 피해 시장 재개장했다", ["완료"], ["완료"]),
        ("추가 피해 최소화를 위해 응급복구에 총력", ["새로운문제"], []),
        ("추가 붕괴 우려에 주민 대피", ["새로운문제"], []),
        ("또다시 침수 피해 발생", ["새로운문제"], ["새로운문제"]),
    ],
)
def test_expression_fixes(text, before, after):
    assert [s for s, _ in R._expressions(text)] == before
    assert [s for s, _ in R._expressions(text, True)] == after


def test_rules_model_name_follows_switches():
    """보강을 켠 규칙으로 분류한 신호는 rules-v3 — v2 로 분류한 캐시 신호와 섞이지 않는다."""
    assert R.RulesLLM(S()).model == R.RULES_MODEL == "rules-v2"
    assert R.RulesLLM(S(followup_expression_fixes=True)).model == "rules-v3"
    assert R.RulesLLM(S(followup_kw_alone=False)).model == "rules-v3"
    assert R.RulesLLM(None).model == "rules-v2"


def _inp(title: str, action: str, hilight: str = "", identity: list[str] | None = None) -> FollowupInput:
    return FollowupInput(
        event_summary="", promises=[PromiseRef(id=1, actor_org="기관", action=action)], article_title=title, hilight=hilight,
        published_at="2025-01-01", identity_terms=identity or [],
    )


def test_skip_commitment_sentence_is_not_progress():
    """기사가 인용한 다짐 문장('…방안을 정부에 제안하겠다')의 '예산 확보' 는 진행 보도가 아니다 (온양 산불 오탐)."""
    inp = _inp("예산 확보 등 임도를 확충하는 방안을 정부에 제안하겠다", "예산 확보 등 임도를 확충하는 방안을 정부에 제안")
    sig, det = R.RulesLLM(S()).classify_followup_detail(inp)
    assert sig.signal == "진행" and det["commit"]
    assert R.RulesLLM(S(followup_skip_commitment=True)).classify_followup(inp).signal == "불명확"


def test_action_match_needs_same_kind_of_action_for_identity_overlap():
    """시설 이름만 겹치고 다른 조치면(가드레일 조성 ↔ 정체 개선 용역 발주) 같은 약속의 후속으로 보지 않는다 (청량나들목 오탐)."""
    inp = _inp("청량나들목 정체 개선 분석 용역 발주", "청량나들목 일원에 가드레일을 조성", identity=["청량나들목"])
    sig, det = R.RulesLLM(S()).classify_followup_detail(inp)
    assert sig.signal == "진행" and det["identity_hits"] == 1 and det["same_kind"] is False
    assert R.RulesLLM(S(followup_action_match=True)).classify_followup(inp).signal == "불명확"
    # 같은 종류의 행동(설치 묶음: 조성·설치)이면 시설 이름 겹침도 센다
    same = _inp("청량나들목 가드레일 설치 완료했다", "청량나들목 일원에 가드레일을 조성", identity=["청량나들목"])
    assert R.RulesLLM(S(followup_action_match=True)).classify_followup(same).signal == "완료"


def test_vector_cache_overflow_keeps_texts_already_cached():
    """캐시가 상한을 넘을 때 이미 들어 있던 문장도 다시 계산한다 (예전에는 비운 뒤 KeyError — 3단계 추출에서 발견)."""

    class Zero:
        name = "zero"

        def encode(self, texts):
            return [[0.0, 1.0] for _ in texts]

    llm = R.RulesLLM(S(), Zero())
    llm.VEC_CACHE_MAX = 2
    llm._vectors(["가", "나", "다"])  # 캐시 3개 > 상한 2
    assert len(llm._vectors(["가", "라"])) == 2  # '가' 는 캐시에 있었지만 비워진 뒤에도 돌려준다


def test_action_groups():
    assert R.action_groups("가드레일을 조성한다") & R.action_groups("가드레일 설치를 마쳤다")
    assert not (R.action_groups("가드레일을 조성한다") & R.action_groups("정체 개선 용역 발주"))


# ---------------------------------------------------------------- 기준일 · 재순위 확률 바닥
def test_cutoff_date_grace_vs_source():
    occ, src = date(2024, 11, 17), date(2024, 11, 17)
    pd = [date(2024, 11, 17)]
    assert J.cutoff_date(occ, src, pd, S(followup_cutoff="grace")) == date(2024, 11, 24)  # 발생 + 7일
    assert J.cutoff_date(occ, src, pd, S(followup_cutoff="source")) == date(2024, 11, 17)  # 원천 발언 다음 날(11-18)부터
    assert J.cutoff_date(date(2023, 7, 15), date(2024, 5, 31), [], S(followup_cutoff="source")) == date(2024, 5, 31)
    assert J.cutoff_date(occ, None, [], S(followup_cutoff="source")) == occ


def _link(reason: str, score: float, **kw) -> EventArticle:
    base = dict(event_id=1, news_id="n", link_method="embedding", link_score=score, link_reason=reason, confirmed=False, needs_review=False, rejected=False)
    base.update(kw)
    return EventArticle(**base)


def test_link_prob_and_usable_followup_link():
    s = S(followup_min_link_prob=0.6)
    reranked_low = _link("region+facility+p=0.45+s=0.70+w=0.50", 0.45)
    reranked_high = _link("region+p=0.83+s=0.70+w=0.50", 0.83)
    plain = _link("region+facility+w=0.50", 0.58)  # 재순위를 끈 연결 — link_score 는 합산 점수
    assert J.link_prob(reranked_low) == 0.45 and J.link_prob(plain) is None
    assert not J.usable_followup_link(reranked_low, s)
    assert J.usable_followup_link(reranked_high, s) and J.usable_followup_link(plain, s)
    assert J.usable_followup_link(_link("p=0.10+source-article", 0.10, confirmed=True), s)  # 관리자 확정·원천 기사
    assert J.usable_followup_link(_link("p=0.10", 0.10, link_method="manual"), s)
    late = _link("region+facility-title+p=0.20+keep-late-facility+w=0.60", 0.20)
    assert J.usable_followup_link(late, s) and J.kept_late(late) and J.low_prob_link(late, s)  # 후보로는 남는다 (진행은 classify 에서 내린다)
    assert not J.usable_followup_link(_link("p=0.90", 0.90, needs_review=True), s)
    assert J.usable_followup_link(reranked_low, S(followup_min_link_prob=0.0))  # 바닥 0 = 끔


# ---------------------------------------------------------------- 판정 단계의 내림 (가상 픽스처 DB)
class FixedLLM:
    """모든 후속 기사에 같은 신호 — 판정 단계가 기준일·바닥으로 내리는지만 본다."""

    model = "fixed"

    def __init__(self, signal: str):
        self.signal = signal

    def classify_followup(self, inp, *, key: str = ""):
        pid = inp.promises[0].id if inp.promises else None
        return SignalOut(signal=self.signal, promise_id=pid, matches_promise=True, evidence_span=(inp.article_title or "")[:80], confidence=0.9, rationale="고정")


@pytest.fixture
def pipeline_db(tmp_path, fixtures_ready):
    from app.bigkinds import build_client
    from app.db.session import init_db, reset_engine, session_factory
    from app.llm.client import build_llm
    from app.pipeline.embeddings import build_embedding
    from app.pipeline.runner import Pipeline

    url = f"sqlite:///{tmp_path / 'fu3.db'}"
    get_settings.cache_clear()
    os.environ["DATABASE_URL"] = url
    settings = get_settings()
    reset_engine()
    init_db(url, drop=True)
    session = session_factory()()
    Pipeline(session, settings, build_client(settings), build_llm(settings), build_embedding(settings)).run("all")
    yield session, settings
    session.close()


def _event_with_promises(session):
    for ev in session.query(Event).all():
        ps = J.trackable_promises(ev)
        if ps and len([l for l in ev.links if l.is_active]) >= 3:
            return ev, ps
    pytest.skip("가상 픽스처에 추적 약속이 있는 사건이 없다")


def test_source_cutoff_drops_signals_not_after_their_promise(pipeline_db):
    session, settings = pipeline_db
    ev, ps = _event_with_promises(session)
    s = settings.model_copy(update={"followup_cutoff": "source"})
    J.classify_followups(session, ev, FixedLLM("완료"), s)
    session.expire_all()
    pdates = J.promise_dates(session, J.trackable_promises(ev))
    for sig in session.query(FollowupSignal).filter_by(event_id=ev.id).all():
        a = session.get(Article, sig.news_id)
        if sig.matches_promise:
            assert a.published_at > pdates[sig.promise_id]  # 약속 발언일 뒤 기사만 그 약속의 신호
        elif "약속 발언일" in sig.rationale:
            assert sig.signal == "불명확"


def test_low_probability_late_link_counts_only_for_done_or_new_problem(pipeline_db):
    session, settings = pipeline_db
    ev, _ = _event_with_promises(session)
    s = settings.model_copy(update={"followup_min_link_prob": 0.6})
    arts = J.followup_articles(session, ev, s)
    if not arts:
        pytest.skip("후속 기사가 없다")
    target = arts[-1]
    link = next(l for l in ev.links if l.news_id == target.news_id)
    link.link_reason, link.link_score, link.confirmed, link.link_method = "region+facility-title+p=0.20+keep-late-facility+w=0.60", 0.20, False, "embedding"
    session.commit()
    assert target.news_id in {a.news_id for a in J.followup_articles(session, ev, s)}  # 후보로는 남는다
    J.classify_followups(session, ev, FixedLLM("진행"), s)
    session.expire_all()
    sig = session.query(FollowupSignal).filter_by(event_id=ev.id, news_id=target.news_id).one()
    assert sig.signal == "불명확" and not sig.matches_promise and "재순위 확률이 낮은 늦은 기사" in sig.rationale
    J.classify_followups(session, ev, FixedLLMModel("새로운문제"), s)
    session.expire_all()
    sig = session.query(FollowupSignal).filter_by(event_id=ev.id, news_id=target.news_id).one()
    assert sig.signal == "새로운문제" and sig.matches_promise
    # 바닥 규칙이 아닌 낮은 확률 연결은 후속 기사에서 빠진다
    link = session.get(EventArticle, {"event_id": ev.id, "news_id": target.news_id})
    link.link_reason = "region+p=0.20+w=0.60"
    session.commit()
    assert target.news_id not in {a.news_id for a in J.followup_articles(session, ev, s)}


class FixedLLMModel(FixedLLM):
    model = "fixed-2"  # 캐시를 피해 다시 분류하게
