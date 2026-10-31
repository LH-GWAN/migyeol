"""2026-10-12 재검증(코드리뷰_2026-10-12_재검증.md) 회귀 테스트."""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

from app.bigkinds import build_client
from app.config import get_settings
from app.db.models import Article, Event, EventArticle, PipelineRun, Promise, StatusJudgment
from app.db.session import init_db, reset_engine, session_factory
from app.llm.client import LLMFailure, build_llm, validate_span
from app.llm.schemas import FollowupInput, FollowupSignal, PromiseRef
from app.pipeline import collect as collect_mod
from app.pipeline.embeddings import build_embedding
from app.pipeline.extract import parse_deadline
from app.pipeline.judge import rejudge_event, trackable_promises
from app.pipeline.link import gate, is_strong
from app.pipeline.runner import Pipeline
from app.redact import mask
from app.rules.actors import is_public_actor, normalize_actor_org
from app.rules.terms import action_terms
from tests.test_pipeline_behaviors import fresh  # noqa: F401
from tests.test_review_20261012 import FakeNews, _settings


def _fresh_pipeline(tmp_path, name: str, **overrides):
    url = f"sqlite:///{tmp_path / name}"
    get_settings.cache_clear()
    os.environ["DATABASE_URL"] = url
    settings = get_settings().model_copy(update=overrides) if overrides else get_settings()
    reset_engine()
    init_db(url, drop=True)
    session = session_factory()()
    return session, settings, Pipeline(session, settings, build_client(settings), build_llm(settings), build_embedding(settings))


# ----------------------------------------------------------------------------- 1-1 큰 달 안의 월말 기사
def test_big_month_keeps_late_month_articles():
    """초기 1000건 + 매일 5건, 2026-06-20 완료 기사 — 큰 달이어도 하순 기사가 들어온다."""
    occurred = date(2025, 7, 15)
    docs = [(f"i{i:04d}", date(2025, 7, 15) + timedelta(days=i % 3), f"{i:04d}") for i in range(1000)]
    d, n = date(2025, 7, 30), 0
    while d < date(2026, 9, 11):
        for _ in range(5):
            docs.append((f"t{n:05d}", d, f"{n:05d}"))
            n += 1
        d += timedelta(days=1)
    got, info = collect_mod.fetch_windowed(FakeNews(docs), "q", occurred, date(2026, 9, 11), _settings())
    assert len(got) <= 300 and info["total_hits"] == len(docs)
    # 큰 달(2026-06, 150건 중 ~11건)에서 상·중·하순이 모두 들어온다 — 월초 기사만 받지 않는다
    june = sorted(d.published_date.day for d in got if d.published_date and d.published_date.strftime("%Y%m") == "202606")
    assert june and june[0] <= 10 and any(11 <= x <= 20 for x in june) and june[-1] >= 21
    # 특정 완료 기사(6/20)는 시드 질의 표본에 반드시 들지 않는다 — 약속 키워드 재검색(collect_promise_followups)이 잡는 경로.
    # 그 질의는 범위가 좁아 예산 안에 다 들어온다
    narrow = [("done", date(2026, 6, 20), "배수펌프 증설 완료"), ("s1", date(2026, 3, 2), "배수펌프 증설 착공")]
    got2, _ = collect_mod.fetch_windowed(FakeNews(narrow), '"배수펌프" AND "가온시"', date(2025, 7, 18), date(2026, 9, 11), _settings())
    assert {x.news_id for x in got2} == {"done", "s1"}


def test_unused_budget_rolls_over_to_truncated_buckets():
    """전재만 많은 달에 배분된 예산은 잘린 다른 달로 넘어간다."""
    occurred = date(2025, 7, 15)
    docs = [(f"d{i:04d}", date(2025, 8, 3), "같은 제목 기사") for i in range(300)]  # 8월: 전재 300건 (1건만 남는다)
    docs += [(f"s{i:04d}", date(2025, 9, 1) + timedelta(days=i % 28), f"{i:04d}") for i in range(300)]  # 9월: 300건 다른 기사
    got, info = collect_mod.fetch_windowed(FakeNews(docs), "q", occurred, date(2025, 10, 5), _settings(collect_month_cap=200))
    sept = [x for x in got if x.news_id.startswith("s")]
    assert len(sept) >= 190  # 8월 몫이 9월로 이월됐다 (단순 배분이면 ~150)


# ----------------------------------------------------------------------------- 1-7 time_line 0건·실패
class _EmptyTimeline(FakeNews):
    def time_line(self, arg):
        from app.bigkinds.schemas import TimeLineResult

        self.calls.append("time_line")
        return TimeLineResult(total_hits=0, time_line=[])


class _BrokenTimeline(FakeNews):
    def time_line(self, arg):
        raise RuntimeError("boom")


def test_empty_timeline_is_cross_checked_with_search():
    docs = [("a", date(2025, 7, 16), "발생"), ("b", date(2025, 8, 20), "후속")]
    got, info = collect_mod.fetch_windowed(_EmptyTimeline(docs), "q", date(2025, 7, 15), date(2025, 9, 11), _settings())
    assert {x.news_id for x in got} == {"a", "b"} and info["warnings"]


def test_broken_timeline_warns_and_still_collects():
    docs = [("a", date(2025, 7, 16), "발생")]
    got, info = collect_mod.fetch_windowed(_BrokenTimeline(docs), "q", date(2025, 7, 15), date(2025, 9, 11), _settings())
    assert [x.news_id for x in got] == ["a"] and any("time_line 실패" in w for w in info["warnings"])


def test_zero_collection_keeps_existing_candidates(fresh, monkeypatch):
    session, pipe, settings = fresh
    ev = session.query(Event).filter_by(key="gaon-underpass-flood").one()
    from app.db.models import EventCandidate

    before = session.query(EventCandidate).filter_by(event_id=ev.id).count()
    assert before > 0
    monkeypatch.setattr(collect_mod, "fetch_windowed", lambda *a, **k: ([], {"windows": 0, "total_hits": 0, "truncated_windows": [], "api_calls": 0, "warnings": ["time_line 실패"]}))
    pipe.run("collect", ev.id)
    assert session.query(EventCandidate).filter_by(event_id=ev.id).count() == before


# ----------------------------------------------------------------------------- 1-2 link 만 실행
def test_link_only_run_does_not_judge_unjudged_events(tmp_path, fixtures_ready):
    session, settings, pipe = _fresh_pipeline(tmp_path, "linkonly.db")
    pipe.run("collect")
    pipe.run("link")
    assert session.query(StatusJudgment).count() == 0
    from app.api.serializers import event_card

    for ev in session.query(Event).all():
        assert event_card(session, ev, settings.today())["judged"] is False
    session.close()


# ----------------------------------------------------------------------------- 1-3 이름 + 직함
@pytest.mark.parametrize("src", ["김민구 과장", "이영도 장관", "박정국 국장", "최영실 실장", "김동구 구청장"])
def test_person_name_with_title_is_not_normalized_to_name(src):
    assert normalize_actor_org(src) is None  # LLM 기관명으로 대체된다


@pytest.mark.parametrize("src", ["김민구 대표", "이영도 사장", "민주노총 위원장", "주민대책위원장", "감리단장", "누리연구소장"])
def test_private_titles_from_reverification(src):
    assert not is_public_actor(src), src


def test_title_only_token_is_public_but_not_an_org():
    # 10-30 rules-8: 기업에도 있는 직함('본부장')만으로는 공공이 아니다. 정부 고유 직함('장관')은 공공이지만 기관명은 아니다
    assert not is_public_actor("본부장") and normalize_actor_org("본부장") is None
    assert is_public_actor("장관") and normalize_actor_org("장관") is None
    assert normalize_actor_org("청주시 관계자") == "청주시"
    assert normalize_actor_org("경기도 관계자") == "경기도"


# ----------------------------------------------------------------------------- 1-4 추출 이월·실패 2회
class _FailingLLM:
    mode = "stub"
    model = "stub"

    def __init__(self, inner):
        self.inner = inner

    def extract_promise(self, inp, *, key=""):
        raise LLMFailure("down")

    def classify_followup(self, inp, *, key=""):
        raise LLMFailure("down")


def test_extract_deferral_marks_pending_quotations(tmp_path, fixtures_ready):
    session, settings, pipe = _fresh_pipeline(tmp_path, "deferral.db", llm_max_calls_per_run=0)
    pipe.run("all")
    pend = [e for e in session.query(Event).all() if e.pending_quotations > 0]
    assert pend and all(e.needs_review for e in pend)
    from app.api.serializers import latest_judgment

    assert any("약속 추출 전" in (latest_judgment(session, e).reason_text or "") for e in pend)
    session.close()


def test_llm_failure_gives_up_after_second_attempt(tmp_path, fixtures_ready):
    session, settings, pipe = _fresh_pipeline(tmp_path, "fail.db")
    pipe.run("collect")
    pipe.run("link")
    pipe.llm = _FailingLLM(pipe.llm)
    pipe.run("extract")
    ev = session.query(Event).filter_by(key="gaon-underpass-flood").one()
    assert ev.pending_quotations > 0 and ev.needs_review
    pipe.run("extract")  # 2회째: '해당없음' 으로 기록, 더는 대기가 아니다
    session.refresh(ev)
    assert ev.pending_quotations == 0
    assert any(p.strength == "해당없음" and "실패" in p.rationale for p in ev.promises)
    assert any(v >= 2 for v in ev.llm_failures.values()) and ev.needs_review
    pipe.run("extract")
    assert all(s.get("llm_calls", 0) == 0 for s in session.query(PipelineRun).order_by(PipelineRun.id.desc()).first().stats["extract"])
    session.close()


def test_followup_failure_recorded_as_unclear_after_second_attempt(fresh):
    session, pipe, settings = fresh
    ev = session.query(Event).filter_by(key="nuri-train-derail").one()
    for s in list(ev.signals):
        session.delete(s)
    session.commit()
    pipe.llm = _FailingLLM(pipe.llm)
    pipe.run("judge", ev.id)
    session.refresh(ev)
    assert ev.pending_followups > 0
    pipe.run("judge", ev.id)
    session.refresh(ev)
    assert ev.pending_followups == 0 and all(s.signal == "불명확" and "실패" in s.rationale for s in ev.signals)
    assert ev.needs_review


# ----------------------------------------------------------------------------- 1-5 병합 약속
def test_merged_promise_tracked_while_any_evidence_link_active(fresh):
    session, pipe, settings = fresh
    ev = session.query(Event).filter_by(key="gaon-underpass-flood").one()
    p = trackable_promises(ev)[0]
    other = next(l.news_id for l in ev.links if l.is_active and l.news_id != p.evidence_news_id)
    p.merged_news_ids = [other]
    rep_link = session.get(EventArticle, {"event_id": ev.id, "news_id": p.evidence_news_id})
    rep_link.rejected = True
    session.commit()
    session.refresh(ev)
    assert p in trackable_promises(ev)


# ----------------------------------------------------------------------------- 1-6 별칭이 지역을 품을 때
def test_alias_containing_region_is_not_org_match(fresh):
    session, pipe, settings = fresh
    ev = session.query(Event).filter_by(key="gaon-underpass-flood").one()
    ev.promise_terms = ["침수", "경보"]
    art = Article(news_id="99999999.20260101000000002", title='가온시장 "연말 침수 대비 경보 체계"', hilight="가온시장이 침수 경보 체계를 말했다", published_at=date(2026, 1, 1), provider="P")
    ok, reasons = gate(ev, art)
    assert "org" not in reasons and not is_strong(reasons)


# ----------------------------------------------------------------------------- 2장
def test_mask_without_word_boundary():
    assert "<access_key>" in mask("key_11111111-2222-3333-4444-555555555555abc") and "11111111" not in mask("x11111111-2222-3333-4444-555555555555")


@pytest.mark.parametrize(
    "span,expected",
    [("1단계 공사를 마쳤고 2단계는 연말 완료 예정", "진행"), ("배수로 정비를 일부 끝냈다", "진행"), ("예정보다 앞당겨 준공했다", "완료"), ("당초 계획대로 마쳤다", "완료")],
)
def test_partial_completion_is_progress(span, expected):
    inp = FollowupInput(event_summary="x", promises=[PromiseRef(id=1, actor_org="a", action="b", deadline_date=None)], article_title="", hilight=span, quotations=[], published_at="2026-01-01")
    sig = FollowupSignal(signal="완료", promise_id=1, matches_promise=True, evidence_span=span, confidence=0.9, rationale="")
    assert validate_span(sig, inp).signal == expected


def test_deadline_with_spaced_dots():
    assert parse_deadline("2026. 6. 30.", date(2025, 7, 1))[0] == date(2026, 6, 30)
    assert parse_deadline("2026. 6", date(2025, 7, 1))[0] == date(2026, 6, 30)


def test_action_terms_reverification_cases():
    assert "km" not in action_terms("배수관로 8km를 교체") and "우수관로" in action_terms("우수관로 정비")
    assert "상인" in action_terms("상인들을 지원") and "상인들" not in action_terms("상인들을 지원")
    assert "재해" in action_terms("재해 복구를 추진")


def test_tracker_only_failure_fails_the_run(fresh, monkeypatch):
    session, pipe, settings = fresh
    from app.pipeline import tracker as tracker_mod

    def boom(*a, **k):
        raise RuntimeError("tracker down")

    monkeypatch.setattr(tracker_mod, "check_changes", boom)
    with pytest.raises(RuntimeError):
        pipe.run("tracker")
    assert session.query(PipelineRun).order_by(PipelineRun.id.desc()).first().ok is False


def test_interrupted_runs_are_closed_on_startup(fresh):
    session, pipe, settings = fresh
    session.add(PipelineRun(stage="all", mode="x", started_at=None))
    session.commit()
    from app.main import _close_interrupted_runs

    _close_interrupted_runs()
    session.expire_all()
    stale = session.query(PipelineRun).filter(PipelineRun.stage == "all", PipelineRun.mode == "x").one()
    assert stale.ok is False and stale.finished_at is not None and "중단" in stale.error


def test_multi_event_demotion_rejudges_other_event(fresh):
    """run('link', event_id=Y) 로 X 의 연결이 강등되면 X 도 재판정된다."""
    session, pipe, settings = fresh
    x = session.query(Event).filter_by(key="gaon-underpass-flood").one()
    y = session.query(Event).filter_by(key="gaon-seori-levee").one()
    nid = next(l.news_id for l in x.links if l.is_active and l.link_method != "manual")
    # y 쪽에 더 높은 점수의 자동 연결을 만들어 x 의 연결을 강등시킨다
    session.add(EventArticle(event_id=y.id, news_id=nid, link_method="embedding", link_score=0.99, auto_decision="link"))
    session.commit()
    before = session.query(StatusJudgment).filter_by(event_id=x.id).count()
    from app.pipeline.link import resolve_multi_event_links

    changed = resolve_multi_event_links(session, [x, y])
    assert x.id in changed


def test_protocol_relative_provider_link_gets_scheme():
    from app.pipeline.collect import _absolute_link

    assert _absolute_link("//www.yeongnam.com/web/view.php?key=1") == "https://www.yeongnam.com/web/view.php?key=1"
    assert _absolute_link("https://a.b/c") == "https://a.b/c" and _absolute_link(None) is None
