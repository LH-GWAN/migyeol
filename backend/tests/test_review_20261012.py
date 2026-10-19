"""2026-10-12 수정 검증(코드리뷰_2026-10-12_수정검증.md) 회귀 테스트 — 0장·1장·2장 항목별."""

from __future__ import annotations

import json
import os
from datetime import date, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.bigkinds.live import LiveBigKindsClient
from app.bigkinds.protocol import BigKindsError
from app.bigkinds.schemas import ChangeTrackerResult, IssueRankingResult, NewsDocument, NewsSearchResult, TimeLineResult, WordCloudResult
from app.config import Settings, get_settings
from app.db.models import Article, Event, EventArticle, FollowupSignal
from app.db.session import init_db, reset_engine, session_factory
from app.llm.client import build_llm
from app.pipeline import collect as collect_mod
from app.pipeline.embeddings import build_embedding
from app.pipeline.judge import rejudge_event, trackable_promises
from app.pipeline.link import gate, is_strong
from app.pipeline.runner import Pipeline
from app.redact import mask, register_secret
from app.rules.actors import is_public_actor, normalize_actor_org
from app.rules.terms import action_terms
from tests.test_pipeline_behaviors import fresh  # noqa: F401 — 공용 픽스처

KEY = "aaaaaaaa-aaaa-bbbb-cccc-123456789abc"


# ----------------------------------------------------------------------------- 0-1 키 노출
def test_bigkinds_error_masks_access_key():
    register_secret(KEY)
    err = BigKindsError("/search/news", -1, {"result": -1, "reason": f"Invalid Access Key!:{KEY}"})
    assert KEY not in str(err) and KEY not in str(err.payload)
    assert "<access_key>" in str(err)
    assert "deadbeef-0000-1111-2222-333333333333" not in mask("x deadbeef-0000-1111-2222-333333333333 y")


def test_live_invalid_key_never_reaches_run_record_or_meta(tmp_path, fixtures_ready):
    """실서버가 키를 되돌려 주는 응답을 흉내 낸다 — pipeline_runs.error, failures, /api/meta, 500 detail 어디에도 키가 없어야 한다."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": -1, "reason": f"Invalid Access Key!:{KEY}", "return_object": None})

    url = f"sqlite:///{tmp_path / 'leak.db'}"
    get_settings.cache_clear()
    os.environ["DATABASE_URL"] = url
    settings = get_settings()
    reset_engine()
    init_db(url, drop=True)
    session = session_factory()()
    client = LiveBigKindsClient(KEY, "https://example.test", rps=0, http=httpx.Client(transport=httpx.MockTransport(handler)))
    pipe = Pipeline(session, settings, client, build_llm(settings), build_embedding(settings))
    run = pipe.run("collect")  # 사건 단위 격리라 실행 자체는 성공, failures 에 남는다
    blob = json.dumps(run.stats, ensure_ascii=False)
    assert run.stats["failures"] and KEY not in blob and "<access_key>" in blob
    with pytest.raises(RuntimeError) as exc:
        pipe.run("discover")  # discover 는 사건 단위 격리가 없어 실행 실패로 기록된다
    assert KEY not in str(exc.value)
    err_row = session.query(__import__("app.db.models", fromlist=["PipelineRun"]).PipelineRun).order_by(-__import__("app.db.models", fromlist=["PipelineRun"]).PipelineRun.id).first()
    assert err_row.error and KEY not in err_row.error
    from app.main import app

    with TestClient(app) as tc:
        meta = tc.get("/api/meta").json()
        assert meta["last_run"]["ok"] is False
        assert meta["last_run"]["error_summary"] == "빅카인즈 API 오류"
        assert KEY not in json.dumps(meta, ensure_ascii=False)
    session.close()


# ----------------------------------------------------------------------------- 0-2 관리자 토큰
def test_admin_token_constant_time_and_required(monkeypatch, fresh):
    from app.main import app

    monkeypatch.setenv("ADMIN_TOKEN", "secret-token")
    get_settings.cache_clear()
    try:
        with TestClient(app) as tc:
            assert tc.get("/api/admin/review-queue").status_code == 401
            assert tc.get("/api/admin/review-queue", headers={"X-Admin-Token": "wrong"}).status_code == 401
            assert tc.get("/api/admin/review-queue", headers={"X-Admin-Token": "secret-token"}).status_code == 200
            assert tc.get("/api/events").status_code == 200  # 공개 API 는 토큰 없이
    finally:
        monkeypatch.delenv("ADMIN_TOKEN")
        get_settings.cache_clear()


# ----------------------------------------------------------------------------- 0-3 마이그레이션
def test_migration_adds_json_default_column_and_prechecks(tmp_path):
    from sqlalchemy import inspect, text

    from app.db.session import get_engine, migrate_missing_columns

    url = f"sqlite:///{tmp_path / 'mig.db'}"
    reset_engine()
    engine = init_db(url, drop=True)
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE "status_judgments" DROP COLUMN "counted_signal_ids"'))
        conn.execute(text('ALTER TABLE "events" DROP COLUMN "pending_followups"'))
    added = migrate_missing_columns(get_engine(url))
    assert "status_judgments.counted_signal_ids" in added and "events.pending_followups" in added
    cols = {c["name"]: c for c in inspect(engine).get_columns("status_judgments")}
    assert "counted_signal_ids" in cols and "[]" in str(cols["counted_signal_ids"]["default"])
    import sqlite3

    raw = sqlite3.connect(str(tmp_path / "mig.db"))  # FK 검사 없이 기본값만 확인
    raw.execute("INSERT INTO status_judgments (event_id, status, reason, reason_text, confidence, evidence, thresholds, prompt_version, judged_at) VALUES (1,'s','r','t',0.5,'[]','{}','v','2026-01-01 00:00:00')")
    assert raw.execute("SELECT counted_signal_ids FROM status_judgments").fetchone()[0] == "[]"
    raw.close()
    reset_engine()


# ----------------------------------------------------------------------------- 1-A ~ 1-D 수집
class FakeNews:
    """search_news / time_line 만 있는 가짜 클라이언트. 문서는 (news_id, published) 로 만든다."""

    mode = "fake"

    def __init__(self, docs: list[tuple[str, date, str]]):
        self.docs = sorted(docs, key=lambda d: (d[1], d[0]))
        self.calls: list[str] = []
        self.call_log: list = []

    def _in(self, arg):
        a = date.fromisoformat(arg.published_at.from_)
        b = date.fromisoformat(arg.published_at.until)
        return [d for d in self.docs if a <= d[1] < b]

    def search_news(self, arg):
        self.calls.append("search")
        hit = self._in(arg)
        page = hit[arg.return_from : arg.return_from + arg.return_size]
        return NewsSearchResult(total_hits=len(hit), documents=[NewsDocument(news_id=n, title=t, published_at=p.isoformat(), provider="P") for n, p, t in page])

    def time_line(self, arg):
        self.calls.append("time_line")
        hit = self._in(arg)
        buckets: dict[str, int] = {}
        for _, p, _ in hit:
            lb = p.strftime("%Y%m%d") if arg.interval == "day" else p.strftime("%Y%m")
            buckets[lb] = buckets.get(lb, 0) + 1
        return TimeLineResult(total_hits=len(hit), time_line=[{"label": k, "hits": v} for k, v in sorted(buckets.items())])


def _settings(**kw) -> Settings:
    return get_settings().model_copy(update={"max_articles_per_event": 300, "collect_initial_cap": 150, "collect_month_cap": 80, **kw})


def test_initial_window_keeps_later_days_when_first_days_overflow():
    """1-B: 7/16~17 에 400건이 쏟아져도 7/18 약속 기사는 들어온다."""
    occurred = date(2025, 7, 15)
    docs = [(f"a{i:04d}", date(2025, 7, 16) + timedelta(days=i % 2), f"{i:04d}") for i in range(400)]  # 제목은 서로 전재가 아니게
    docs.append(("promise", date(2025, 7, 18), "가온시, 배수펌프 증설 확약"))
    client = FakeNews(docs)
    got, info = collect_mod.fetch_windowed(client, "q", occurred, date(2025, 8, 1), _settings())
    ids = {d.news_id for d in got}
    assert "promise" in ids and len(got) == 150 and info["total_hits"] == 401


def test_total_cap_does_not_starve_recent_months_and_total_hits_counts_all():
    """1-A: 초기 1000건 + 이후 매일 5건 + 2026-06 완료 기사 → 완료 기사가 수집되고 total_hits 는 전체 건수."""
    occurred = date(2025, 7, 15)
    docs = [(f"i{i:04d}", date(2025, 7, 15) + timedelta(days=i % 3), f"{i:04d}") for i in range(1000)]
    d = date(2025, 7, 30)
    n = 0
    while d < date(2025, 9, 11):
        for k in range(5):
            docs.append((f"t{n:05d}", d, f"{n:05d}"))
            n += 1
        d += timedelta(days=1)
    docs.append(("done", date(2025, 9, 5), "가온시, 배수펌프 증설 완료"))
    client = FakeNews(docs)
    got, info = collect_mod.fetch_windowed(client, "q", occurred, date(2025, 9, 11), _settings())
    assert "done" in {x.news_id for x in got}
    assert info["total_hits"] == len(docs)
    assert len(got) <= 300 and len(got) >= 290
    assert info["api_calls"] <= 2 + 16 + 3  # time_line 2회 + 날짜별 ≤16 + 월별 ≤3


def test_near_duplicates_do_not_consume_cap():
    """1-C: 같은 달 전재 기사 200건이 있어도 25일 착공 기사가 들어온다."""
    occurred = date(2025, 7, 15)
    docs = [(f"d{i:04d}", date(2025, 8, 3), "같은 제목 기사") for i in range(200)]  # 같은 날·같은 언론사·같은 제목 → 전재
    docs.append(("start", date(2025, 8, 25), "배수펌프 증설 착공"))
    client = FakeNews(docs)
    got, _ = collect_mod.fetch_windowed(client, "q", occurred, date(2025, 9, 11), _settings(collect_month_cap=50))
    assert "start" in {x.news_id for x in got}


def test_quiet_old_event_uses_few_calls():
    """1-D: 3년 된 조용한 사건은 월마다 부르지 않는다 (time_line 2 + search 2)."""
    occurred = date(2023, 1, 10)
    docs = [("a", date(2023, 1, 10), "발생"), ("b", date(2024, 6, 1), "후속"), ("c", date(2025, 12, 1), "완료")]
    client = FakeNews(docs)
    got, info = collect_mod.fetch_windowed(client, "q", occurred, date(2026, 9, 11), _settings())
    assert {x.news_id for x in got} == {"a", "b", "c"}
    assert info["api_calls"] <= 4 and client.calls.count("search") <= 2


def test_collect_sort_is_guide_object_form():
    assert isinstance(collect_mod._SORT, dict)


# ----------------------------------------------------------------------------- 1-F 공공 주체
@pytest.mark.parametrize(
    "src",
    [
        "가온시 시민안전과장", "청주시 시민안전국장", "시민안전실 관계자", "여성가족부 관계자", "한국도로공사 사장", "국가철도공단 이사장",
        "주민센터 관계자", "교육청 학생안전과 관계자", "도로과 관계자", "시·군·도 관계자", "LH·한전·코레일 관계자", "경기도 관계자",
    ],
)
def test_public_actor_positive_cases_from_verification(src):
    assert is_public_actor(src), src


@pytest.mark.parametrize("src", ["김민구", "이영도", "더불어민주당 대변인", "식당 사장", "입주민 대표", "㈜누리건설 대표", "피해 주민들", "택시 기사 김모씨"])
def test_public_actor_negative_cases_from_verification(src):
    assert not is_public_actor(src), src
    assert normalize_actor_org(src) is None, src  # 개인명·민간은 기관으로 정규화하지 않는다 (§2-4)


# ----------------------------------------------------------------------------- 1-K 행동 키워드
def test_action_terms_regressions():
    assert "마치고" not in action_terms("보강공사를 마치고 신속히 개통", exclude=["누리군"])
    assert "신속히" not in action_terms("보강공사를 마치고 신속히 개통", exclude=["누리군"])
    terms = action_terms("달빛지하차도 배수로 정비", exclude=["누리군"])
    assert "달빛지하차도" in terms and "배수로" in terms and "달빛지하차" not in terms
    assert len(action_terms("피해 주민들에게 지원금을 지급하고 안전대책을 마련", exclude=["가온시"])) < 2


# ----------------------------------------------------------------------------- 1-L 사건분류 코드
def test_incident_code_string_is_not_split_on_commas():
    from app.bigkinds.codes import is_mvp_incident
    from app.bigkinds.schemas import _to_str_list

    assert _to_str_list("2,9,34") == ["2,9,34"]
    assert is_mvp_incident(_to_str_list("2,9,34"))
    assert _to_str_list("사고>산업사고>화재,재해>자연재해") == ["사고>산업사고>화재", "재해>자연재해"]
    doc = NewsDocument.model_validate({"news_id": "x", "category_incident": "3,11"})
    assert doc.category_incident == ["3,11"]


# ----------------------------------------------------------------------------- bk-3 관대 파서
def test_lenient_parsers_accept_nulls():
    assert TimeLineResult.model_validate({"total_hits": None, "time_line": [{"label": None, "hits": None}, None]}).time_line[0].hits == 0
    assert WordCloudResult.model_validate({"nodes": [{"id": None, "name": None, "weight": "1.5"}]}).nodes[0].weight == 1.5
    assert IssueRankingResult.model_validate({"date": None, "topics": [{"topic_rank": None, "news_cluster": None}]}).topics[0].topic_rank == 0
    r = ChangeTrackerResult.model_validate({"total_count": None, "size": None, "message": None, "dataList": [{"data": {"newsitem_id": None, "news_status": None}}]})
    assert r.size == 3000 and r.items[0].newsitem_id == ""


# ----------------------------------------------------------------------------- 1-G / 1-H / 1-I / 1-J (파이프라인)
def test_llm_budget_exhaustion_marks_judgment_pending(tmp_path, fixtures_ready):
    """1-G: 호출 상한에 걸린 사건은 pending_followups > 0, needs_review, 사유에 '잠정' 이 남는다. 최신 기사부터 분류한다."""
    url = f"sqlite:///{tmp_path / 'budget.db'}"
    get_settings.cache_clear()
    os.environ["DATABASE_URL"] = url
    settings = get_settings().model_copy(update={"llm_max_calls_per_run": 8})
    reset_engine()
    init_db(url, drop=True)
    session = session_factory()()
    from app.bigkinds import build_client

    pipe = Pipeline(session, settings, build_client(settings), build_llm(settings), build_embedding(settings))
    run = pipe.run("all")
    judged = run.stats["judge"]
    assert sum(s.get("deferred", 0) for s in judged) > 0
    pend = [e for e in session.query(Event).all() if e.pending_followups > 0]
    assert pend and all(e.needs_review for e in pend)
    from app.api.serializers import latest_judgment

    j = latest_judgment(session, pend[0])
    assert "잠정" in j.reason_text and j.unresolved_reason and "분류 전" in j.unresolved_reason
    # 분류된 기사는 가장 최근 것부터
    for e in pend:
        version = "followup_v1"
        done_dates = sorted(session.get(Article, s.news_id).published_at for s in e.signals if s.prompt_version == version)
        from app.pipeline.judge import followup_articles

        all_dates = sorted(a.published_at for a in followup_articles(session, e, settings))
        if done_dates:
            assert done_dates[-1] == all_dates[-1]
    session.close()


def test_cancelled_evidence_does_not_count_stale_signals(fresh):
    """1-H: 근거 기사가 삭제돼 약속이 빠지면, 옛 약속 집합으로 분류된 신호는 판정에 쓰지 않는다."""
    session, pipe, settings = fresh
    ev = _event_by_key(session, "nuri-train-derail")
    assert ev.status == "조치 진행 정황 확인"
    for p in list(trackable_promises(ev)):
        art = session.get(Article, p.evidence_news_id)
        art.change_status = "cancelled"
        p.is_trackable = False
    session.commit()
    j = rejudge_event(session, ev, settings, settings.today())
    assert j.status != "조치 진행 정황 확인" and not j.counted_signal_ids
    from app.api.serializers import evidence_out

    out = evidence_out(session, ev, settings.today())
    assert not any(s["counted"] for s in out["signals"])


def test_removed_link_drops_its_promise_from_tracking(fresh, tmp_path):
    """1-I: 자동 연결이 삭제되면(시드 변경) 그 기사에서 뽑은 약속은 추적·대표 약속·우선순위에서 빠진다."""
    from app.api.serializers import event_card
    from app.bigkinds import build_client
    from tests.test_pipeline_behaviors import _with_seed_patch

    session, pipe, settings = fresh
    ev = _event_by_key(session, "gaon-market-explosion")
    before = event_card(session, ev, settings.today())
    assert before["primary_promise"] is not None
    patched = _with_seed_patch(tmp_path, settings, "gaon-market-explosion", region="먼곳시", facility="없는시장", keywords=["없는말"], responsible_org="먼곳시", org_aliases=[], query='"없는말없는말"')
    pipe2 = Pipeline(session, patched, build_client(patched), build_llm(patched), build_embedding(patched))
    pipe2.run("collect", ev.id)
    pipe2.run("link", ev.id)
    session.refresh(ev)
    assert not [l for l in ev.links if l.is_active]
    assert trackable_promises(ev) == []
    after = event_card(session, ev, settings.today())
    assert after["primary_promise"] is None and after["priority_score"] <= before["priority_score"]


def test_org_equal_to_region_is_not_strong_with_generic_promise_terms(fresh):
    """1-J: 책임기관 == 지역(가온시)이면 '기관 + 약속 키워드 2개' 만으로 자동 연결되지 않는다."""
    session, pipe, settings = fresh
    ev = _event_by_key(session, "gaon-underpass-flood")
    ev.promise_terms = ["지원금", "주민들"]
    art = Article(news_id="99999999.20260101000000001", title="가온시, 피해 주민들에게 지원금 지급", hilight="가온시가 지원금을 주민들에게 지급한다", published_at=date(2026, 1, 1), provider="P")
    ok, reasons = gate(ev, art)
    assert "org" not in reasons and not is_strong(reasons)


def test_review_pending_link_does_not_demote_other_events_auto_link(fresh):
    """사건 간 배타 처리: 검토 대기 연결은 다른 사건의 정상 자동 연결을 강등시키지 않는다."""
    from app.pipeline.link import resolve_multi_event_links

    session, pipe, settings = fresh
    a = _event_by_key(session, "gaon-underpass-flood")
    b = _event_by_key(session, "gaon-seori-levee")
    nid = next(l.news_id for l in a.links if l.is_active)
    session.add(EventArticle(event_id=b.id, news_id=nid, link_method="embedding", link_score=0.9, needs_review=True, auto_decision="review"))
    session.commit()
    resolve_multi_event_links(session, [a, b])
    la = session.get(EventArticle, {"event_id": a.id, "news_id": nid})
    assert la.needs_review is False


def test_reject_keeps_auto_method_and_evaluate_counts_it(fresh):
    """ev-8: 관리자 해제는 link_method 를 바꾸지 않고 auto_decision 도 남아 평가가 파이프라인 결정을 센다."""
    from app.main import app

    session, pipe, settings = fresh
    ev = _event_by_key(session, "nuri-landslide")
    l = next(l for l in ev.links if l.is_active)
    with TestClient(app) as tc:
        r = tc.post(f"/api/admin/links/{ev.id}/{l.news_id}", json={"action": "reject"})
        assert r.status_code == 200 and r.json()["link_method"] in ("rule", "embedding")
    session.expire_all()
    l = session.get(EventArticle, {"event_id": ev.id, "news_id": l.news_id})
    assert l.rejected and l.auto_decision == "link" and "+manual" in l.link_reason


def test_pipeline_run_rejects_huge_event_id(fresh):
    from app.main import app

    with TestClient(app) as tc:
        assert tc.post("/api/pipeline/run", json={"stage": "judge", "event_id": 10**30}).status_code == 422


def test_card_priority_matches_judged_priority(fresh):
    """우선순위 불일치: 후속 기사 0건 사건도 카드 값과 DB 값이 같다."""
    from app.api.serializers import event_card

    session, pipe, settings = fresh
    for ev in session.query(Event).all():
        card = event_card(session, ev, settings.today())
        assert card["priority_score"] == ev.priority_score, ev.key


def _event_by_key(session, key: str) -> Event:
    return session.query(Event).filter_by(key=key).one()
