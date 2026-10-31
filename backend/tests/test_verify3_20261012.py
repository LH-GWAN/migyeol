"""3차 검증(코드리뷰_2026-10-12_3차검증.md) 회귀 테스트 — 문서에 나온 문장과 사례를 그대로 고정한다."""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.bigkinds import build_client
from app.config import get_settings
from app.db.models import Article, Event, EventArticle, EventCandidate, PipelineRun, Quotation, StatusJudgment
from app.db.session import init_db, reset_engine, session_factory
from app.llm.client import build_llm, completion_adjustment
from app.pipeline import collect as collect_mod
from app.pipeline.embeddings import build_embedding
from app.pipeline.extract import parse_deadline
from app.pipeline.link import gate, is_strong
from app.pipeline.runner import Pipeline, _lock_path
from app.redact import mask, register_secret
from app.rules.actors import is_public_actor, looks_like_person, normalize_actor_org
from app.rules.terms import action_terms
from tests.test_pipeline_behaviors import fresh  # noqa: F401
from tests.test_reverify_20261012 import _FailingLLM, _fresh_pipeline
from tests.test_review_20261012 import FakeNews, _settings

# ----------------------------------------------------------------------------- 1-1 완료 표현
COMPLETE = [
    "계획했던 공사를 모두 끝냈다", "예정된 공사를 마쳤다", "계획된 보강공사를 완료했다",
    "목표했던 공사를 모두 마쳤다", "당초 목표대로 준공했다", "연말 목표였던 배수펌프 증설을 마쳤다",
    "예상보다 빨리 개통됐다", "보강공사를 100% 마쳤다", "공사를 먼저 끝낸 뒤 개통했다",
    "예정대로 완공했다", "당초 계획대로 마쳤다", "예정보다 앞당겨 준공했다", "전 구간 라이닝 보강을 완료했으며",
]
PROGRESS = [
    "1단계 공사를 마쳤고 2단계는 연말 완료 예정", "배수로 정비를 일부 끝냈다", "준공을 앞두고 있다",
    "5월 완료 예정이라고 밝혔다", "연내 설치를 마무리할 계획이다", "올해 안에 교체할 것이라고 말했다", "현재 보강공사 추진 중이다",
]


@pytest.mark.parametrize("span", COMPLETE)
def test_complete_sentences_stay_complete(span):
    assert completion_adjustment(span) is None, span


@pytest.mark.parametrize("span", PROGRESS)
def test_future_or_partial_sentences_become_progress(span):
    assert completion_adjustment(span) is not None, span


# ----------------------------------------------------------------------------- 1-2 공공 주체
LEX = ["가온시", "가온시청", "가온시장"]


@pytest.mark.parametrize("src", ["이영도 대변인", "김민구 관계자", "청주시 이영도 대변인", "박정국 국장", "최영실 실장", "김동구 구청장", "정민도 도로과장"])
def test_person_names_are_never_normalized_to_org(src):
    assert normalize_actor_org(src, "가온시", LEX) not in ("이영도", "김민구", "박정국", "최영실", "김동구", "정민도")


@pytest.mark.parametrize("name", ["청주시", "보령시", "강남구", "중구", "도로국", "가온시", "경기도", "충청북도", "청주시청 도로과"])
def test_real_orgs_are_not_person_shaped(name):
    assert not looks_like_person(name, LEX), name


@pytest.mark.parametrize("name", ["김민구", "이영도", "박정국", "최영실"])
def test_names_are_person_shaped(name):
    assert looks_like_person(name, LEX), name


@pytest.mark.parametrize(
    "src,org",
    [("서초구 구청장", "서초구"), ("영동군 군수", "영동군"), ("청주시 도로과장", "청주시"), ("보령시 시장", "보령시"), ("청주시 관계자", "청주시"), ("도로국 관계자", "도로국"), ("청주시 이영도 대변인", "청주시")],
)
def test_real_orgs_with_titles_are_public_and_normalized(src, org):
    assert is_public_actor(src) and normalize_actor_org(src) == org


def test_rule_org_beats_llm_variant_and_llm_person_name_is_rejected(fresh, monkeypatch):
    """규칙이 '청주시' 를 맞히면 LLM 의 '충청북도' 로 덮어쓰지 않고, 규칙이 없고 LLM 이 '김민구' 를 주면 기관 미상("")."""
    from app.llm.schemas import PromiseExtraction
    from app.pipeline import extract as extract_mod

    from app.bigkinds.schemas import QuotationDocument

    session, pipe, settings = fresh
    ev = session.query(Event).filter_by(key="gaon-underpass-flood").one()
    link = next(l for l in ev.links if l.is_active)
    docs = [
        QuotationDocument(news_id=link.news_id, source="가온시 관계자", quotation="배수펌프를 증설하겠다", published_at="2025-08-01", provider="P"),
        QuotationDocument(news_id=link.news_id, source="홍길동 장관", quotation="차단시설을 설치하겠다", published_at="2025-08-02", provider="P"),  # 정부 직함이라 공공이지만 기관은 못 찾는다
    ]

    class _LLM:
        mode, model = "stub", "stub"

        def extract_promise(self, inp, *, key=""):
            org = "충청북도" if "배수펌프" in inp.quotation else "김민구"
            return PromiseExtraction(is_promise=True, actor_org=org, actor_raw=inp.source, action=inp.quotation, deadline_raw=None, deadline_date=None, deadline_precision="none", strength="확약", confidence=0.9, rationale="")

        def classify_followup(self, inp, *, key=""):
            raise AssertionError

    monkeypatch.setattr(extract_mod, "fetch_quotations", lambda *a, **k: (docs, []))
    extract_mod.extract_event(session, pipe.client, ev, _LLM(), settings, pipe.embedder, {"remaining": 10})
    session.refresh(ev)
    by_action = {p.action: p for p in ev.promises}
    assert by_action["배수펌프를 증설하겠다"].actor_org == "가온시"  # 규칙 결과 유지 (LLM 변형으로 덮어쓰지 않음)
    assert by_action["차단시설을 설치하겠다"].actor_org == ""  # LLM 이 준 사람 이름은 버린다 → 기관 미상
    from app.api.serializers import event_detail

    out = event_detail(session, ev, settings.today())
    assert any(p["actor_org"] == "기관 미상" for p in out["promises"])


def test_unknown_orgs_merge_only_when_same_speaker_or_wording(fresh):
    """기관 미상("") 약속: 화자(원문)와 문구가 모두 다르면 병합하지 않는다. 같은 화자나 같은 문구면 병합한다 (10-30 R3 — 275 ×10 중복)."""
    from app.db.models import Promise
    from app.pipeline.extract import _merge_duplicates

    session, pipe, settings = fresh
    ev = session.query(Event).filter_by(key="nuri-landslide").one()
    link = next(l for l in ev.links if l.is_active)
    ev.promises.append(Promise(event_id=ev.id, actor_org="", actor_raw="주민 대표", action="사방댐을 설치해 달라고 한다", strength="확약", is_trackable=True, evidence_news_id=link.news_id, confidence=0.9))
    ev.promises.append(Promise(event_id=ev.id, actor_org="", actor_raw="시공사 관계자", action="사방댐을 설치할 계획이다", strength="확약", is_trackable=True, evidence_news_id=link.news_id, confidence=0.9))
    session.flush()
    assert _merge_duplicates(ev, pipe.embedder) == 0
    for i in range(3):
        ev.promises.append(Promise(event_id=ev.id, actor_org="", actor_raw="미상", action="사방댐을 설치한다", strength="확약", is_trackable=True, evidence_news_id=link.news_id, confidence=0.9))
    session.flush()
    assert _merge_duplicates(ev, pipe.embedder) == 2


# ----------------------------------------------------------------------------- 1-3 action_terms
@pytest.mark.parametrize("text,bad", [("2배로", "배로"), ("34곳을", "곳을"), ("2대를", "대를"), ("안에", "안에"), ("차를", "차를"), ("길을", "길을"), ("둑을", "둑을"),
                                      ("교체해", "교체해"), ("설치해", "설치해"), ("정비해", "정비해"), ("대피로를", "대피"), ("순찰로를", "순찰")])
def test_particle_residue_is_not_a_search_term(text, bad):
    assert bad not in action_terms(f"{text} 정비한다")


def test_two_char_nouns_ending_with_hae_are_kept():
    terms = action_terms("손해 배상과 재해 복구")
    assert "손해" in terms and "재해" in terms


def test_mock_promise_terms_have_no_residue(fresh):
    session, pipe, settings = fresh
    ev = session.query(Event).filter_by(key="gaon-underpass-flood").one()
    assert "배로" not in (ev.promise_terms or []) and set(ev.promise_terms) >= {"배수펌프", "차단시설"}


# ----------------------------------------------------------------------------- 1-4 수집
class QueryAwareNews(FakeNews):
    """질의를 실제로 거르는 가짜 클라이언트: 'AND' 로 나눈 절마다 따옴표 용어 중 하나가 제목에 있어야 한다."""

    def _match(self, title: str, query: str) -> bool:
        import re

        clauses = re.split(r"\)\s*AND\s*\(", query.strip().strip("()"))
        for cl in clauses:
            terms = re.findall(r'"([^"]+)"', cl)
            if terms and not any(t in title for t in terms):
                return False
        return True

    def _in(self, arg):
        a = date.fromisoformat(arg.published_at.from_)
        b = date.fromisoformat(arg.published_at.until)
        return [d for d in self.docs if a <= d[1] < b and self._match(d[2], arg.query)]


def _big_event_docs():
    docs = [(f"i{i:04d}", date(2025, 7, 15) + timedelta(days=i % 3), f"가온시 달빛지하차도 침수 {i:04d}") for i in range(1000)]
    d, n = date(2025, 7, 30), 0
    while d < date(2026, 9, 11):
        for _ in range(5):
            docs.append((f"t{n:05d}", d, f"가온시 달빛지하차도 후속 {n:05d}"))
            n += 1
        d += timedelta(days=1)
    return docs


def test_status_query_finds_completion_article_in_big_event():
    """1-A 원래 재현: 초기 1000건 + 하루 5건 + 2026-06-20 완료 기사 — 조치 상태 질의가 완료 기사를 직접 찾는다."""
    docs = _big_event_docs()
    docs.append(("done", date(2026, 6, 20), "가온시 달빛지하차도 배수펌프 증설 완료"))
    docs.append(("other", date(2026, 3, 3), "가온시 달빛지하차도 침수 대책 마무리"))  # 다른 표현
    client = QueryAwareNews(docs)
    got, info = collect_mod.fetch_windowed(client, '("달빛지하차도") AND ("가온시")', date(2025, 7, 15), date(2026, 9, 11), _settings())
    ids = {x.news_id for x in got}
    assert "done" in ids and "other" in ids
    assert len(got) <= 300 and "status" in info["queries"]


def test_call_volume_for_medium_three_year_event_is_bounded():
    """월 15건짜리 3년 된 사건: 월마다 3회씩 부르지 않는다 (상태 질의 + 분기 표본)."""
    docs = []
    d, n = date(2023, 1, 10), 0
    while d < date(2026, 9, 11):
        if d.day % 2 == 0:
            docs.append((f"m{n:05d}", d, f"가온시 달빛지하차도 보도 {n:05d}"))
            n += 1
        d += timedelta(days=1)
    client = QueryAwareNews(docs)
    got, info = collect_mod.fetch_windowed(client, '("달빛지하차도") AND ("가온시")', date(2023, 1, 10), date(2026, 9, 11), _settings())
    assert info["api_calls"] <= 25  # 이전(월별 3회 분할)이면 44개월 × 3 ≈ 130회
    assert client.calls.count("search") <= 22


def test_quiet_event_still_uses_four_calls():
    docs = [("a", date(2023, 1, 10), "가온시 달빛지하차도 발생"), ("b", date(2024, 6, 1), "가온시 달빛지하차도 후속"), ("c", date(2025, 12, 1), "가온시 달빛지하차도 완료")]
    client = QueryAwareNews(docs)
    got, info = collect_mod.fetch_windowed(client, '("달빛지하차도") AND ("가온시")', date(2023, 1, 10), date(2026, 9, 11), _settings())
    assert {x.news_id for x in got} == {"a", "b", "c"} and info["api_calls"] <= 4


def test_decade_split_prefers_early_part_and_rollover_respects_month_cap():
    from app.pipeline.collect import _decades, _fetch_bucket

    client = FakeNews([(f"x{i:03d}", date(2025, 8, 1) + timedelta(days=i % 30), f"{i:03d}") for i in range(90)])
    docs, _, _, _, _ = _fetch_bucket(client, "q", date(2025, 8, 1), date(2025, 9, 1), 4, split_big_month=True)
    days = [d.published_date.day for d in docs]
    assert len(docs) == 4 and sum(1 for x in days if x <= 10) == 2 and sum(1 for x in days if 11 <= x <= 20) == 1  # 2/1/1
    docs2, *_ = _fetch_bucket(client, "q", date(2025, 8, 1), date(2025, 9, 1), 2, split_big_month=True)
    assert [d.published_date.day for d in docs2][0] <= 10  # cap 2 여도 상순을 건너뛰지 않는다
    assert len(_decades(date(2025, 8, 5), date(2025, 11, 20))) == 3  # 긴 구간도 셋으로


def test_zero_override_query_warns(fresh, tmp_path):
    from tests.test_pipeline_behaviors import _with_seed_patch

    session, pipe, settings = fresh
    ev = session.query(Event).filter_by(key="gaon-underpass-flood").one()
    patched = _with_seed_patch(tmp_path, settings, "gaon-underpass-flood", query='"없는말없는말"')
    pipe2 = Pipeline(session, patched, build_client(patched), build_llm(patched), build_embedding(patched))
    run = pipe2.run("collect", ev.id)
    assert any("오타" in w["warning"] for w in run.stats.get("warnings", []))
    from app.main import app

    with TestClient(app) as tc:
        meta = tc.get("/api/meta").json()
        assert meta["last_run"]["warnings"]


# ----------------------------------------------------------------------------- 1-5 tracker 단독·relink
def test_tracker_alone_does_not_judge_unjudged_events(tmp_path, fixtures_ready):
    session, settings, pipe = _fresh_pipeline(tmp_path, "trk.db")
    pipe.run("collect")
    pipe.run("link")
    pipe.run("tracker")
    assert session.query(StatusJudgment).count() == 0
    session.close()


def test_single_event_relink_rejudges_demoted_other_event(fresh, monkeypatch):
    """run("extract", event_id=Y) 의 relink 로 다른 사건 X 의 연결이 강등되면 X 도 재판정된다 (judge 는 Y 만 다루므로)."""
    from app.pipeline import link as link_mod

    session, pipe, settings = fresh
    x = session.query(Event).filter_by(key="gaon-underpass-flood").one()
    y = session.query(Event).filter_by(key="gaon-seori-levee").one()
    nid = next(l.news_id for l in x.links if l.is_active and l.link_method != "manual")
    session.add(EventArticle(event_id=y.id, news_id=nid, link_method="embedding", link_score=0.99, auto_decision="link"))
    session.commit()
    monkeypatch.setattr(link_mod, "link_event", lambda session, event, settings, embedder, other_facilities=None: {"event": event.key, "linked": 0, "review": 0})
    before = session.query(StatusJudgment).filter_by(event_id=x.id).count()
    pipe.run("extract", y.id)
    session.expire_all()
    assert session.get(EventArticle, {"event_id": x.id, "news_id": nid}).needs_review is True
    assert session.query(StatusJudgment).filter_by(event_id=x.id).count() > before


# ----------------------------------------------------------------------------- 1-6 영구 잠정·검토
def test_pending_quotation_clears_when_its_link_is_rejected(tmp_path, fixtures_ready):
    from app.main import app

    session, settings, pipe = _fresh_pipeline(tmp_path, "pend.db", llm_max_calls_per_run=0)
    pipe.run("all")
    ev = next(e for e in session.query(Event).all() if e.pending_quotations > 0)
    pending_q = next(q for q in ev.quotations if q.is_public_actor and not q.promises)
    with TestClient(app) as tc:
        assert tc.post(f"/api/admin/links/{ev.id}/{pending_q.news_id}", json={"action": "reject"}).status_code == 200
    session.expire_all()
    ev = session.get(Event, ev.id)
    assert all(q.news_id != pending_q.news_id or not any(l.news_id == q.news_id and l.is_active for l in ev.links) for q in ev.quotations)
    assert ev.pending_quotations == sum(1 for q in ev.quotations if q.is_public_actor and not q.promises and any(l.news_id == q.news_id and l.is_active for l in ev.links))
    session.close()


def test_gave_up_items_can_be_acknowledged_and_retried(tmp_path, fixtures_ready):
    from app.main import app

    session, settings, pipe = _fresh_pipeline(tmp_path, "giveup.db")
    pipe.run("collect")
    pipe.run("link")
    pipe.llm = _FailingLLM(pipe.llm)
    pipe.run("extract")
    pipe.run("extract")
    ev = session.query(Event).filter_by(key="nuri-landslide").one()  # 검토 대기 연결이 없는 사건
    assert ev.needs_review and any(v >= 2 for v in ev.llm_failures.values())
    with TestClient(app) as tc:
        r = tc.post(f"/api/admin/events/{ev.id}/llm-ack").json()
        assert r["needs_review"] is False
        card = next(c for c in tc.get("/api/events?size=100").json()["items"] if c["id"] == ev.id)
        assert card["llm_gave_up"] and card["llm_acked"] and "pending_quotations" in card
        r = tc.post(f"/api/admin/events/{ev.id}/llm-retry").json()
        assert r["removed"] > 0
    session.expire_all()
    ev = session.get(Event, ev.id)
    assert ev.llm_failures == {} and not any(p.strength == "해당없음" and "실패" in (p.rationale or "") for p in ev.promises)
    assert ev.needs_review  # 추출 대기가 되살아나 잠정 상태로 돌아간다
    pipe.llm = build_llm(settings)  # LLM 복구 → 다시 시도된다
    run = pipe.run("extract", ev.id)
    assert run.stats["extract"][0]["llm_calls"] > 0
    session.close()


# ----------------------------------------------------------------------------- 1-7 기동 시 중단 처리
def test_startup_skips_closing_runs_while_another_process_holds_the_lock(fresh):
    import fcntl

    from app.main import _close_interrupted_runs

    session, pipe, settings = fresh
    session.add(PipelineRun(stage="judge", mode="x"))
    session.commit()
    fh = open(_lock_path(), "a+")
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        _close_interrupted_runs()
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()
    session.expire_all()
    assert session.query(PipelineRun).filter_by(mode="x").one().finished_at is None
    _close_interrupted_runs()
    session.expire_all()
    assert session.query(PipelineRun).filter_by(mode="x").one().ok is False


def test_successful_run_clears_stale_error(fresh):
    session, pipe, settings = fresh
    run = pipe.run("link")
    assert run.ok and run.error is None


# ----------------------------------------------------------------------------- 1-8 약속 재검색 후보의 강한 규칙
def test_promise_candidate_completion_article_is_strong(fresh):
    session, pipe, settings = fresh
    ev = session.query(Event).filter_by(key="gaon-underpass-flood").one()
    ev.promise_terms = ["배수펌프", "차단시설"]
    art = Article(news_id="99999999.20260101000000003", title='가온시청 "배수펌프 증설 완료"', hilight="가온시청이 배수펌프와 차단시설 설치를 마쳤다", published_at=date(2026, 1, 1), provider="P", category_incident=["재해>자연재해>홍수"])
    ok, reasons = gate(ev, art)
    assert ok and is_strong(reasons, promise_candidate=True) and not is_strong(reasons, promise_candidate=False)


# ----------------------------------------------------------------------------- 2장
def test_lock_path_for_memory_db(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    get_settings.cache_clear()
    try:
        assert _lock_path().parent.name == "data" and _lock_path().parent.parent.name == "backend"
    finally:
        monkeypatch.delenv("DATABASE_URL")
        get_settings.cache_clear()


def test_redact_variants():
    key = "11111111-2222-3333-4444-555555555555"
    register_secret(key)
    for form in (key.replace("-", ""), key.replace("-", "%2D"), key.replace("-", "_"), key.upper()):
        assert form not in mask(f"x{form}y"), form


def test_deadline_space_separated():
    assert parse_deadline("2026 6 30", date(2025, 7, 1))[0] == date(2026, 6, 30)
