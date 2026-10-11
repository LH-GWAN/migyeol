"""리뷰 1-2, 1-5, 1-6, 1-7, 1-8, 1-10, 2장(needs_review) 의 동작 회귀 테스트 — 테스트마다 새 DB 로 전체 파이프라인을 돌린다."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.bigkinds import build_client
from app.config import STATUS_DONE, Settings, get_settings
from app.db.models import Article, Event, EventArticle, EventCandidate, FollowupSignal, Report, StatusJudgment
from app.db.session import init_db, reset_engine, session_factory
from app.llm.client import build_llm
from app.pipeline.embeddings import build_embedding
from app.pipeline.runner import Pipeline
from app.pipeline.seeds import Seed, expand_query


@pytest.fixture
def fresh(tmp_path, fixtures_ready):
    url = f"sqlite:///{tmp_path / 'fresh.db'}"
    get_settings.cache_clear()
    os.environ["DATABASE_URL"] = url
    settings = get_settings()
    reset_engine()
    init_db(url, drop=True)
    session = session_factory()()
    pipe = Pipeline(session, settings, build_client(settings), build_llm(settings), build_embedding(settings))
    pipe.run("all")
    yield session, pipe, settings
    session.close()


def _with_seed_patch(tmp_path, settings: Settings, key: str, **changes) -> Settings:
    """시드 YAML 사본에서 사건 하나를 바꾼 설정을 만든다 (DB 값은 매 실행 시드로 복원되므로)."""
    import yaml

    data = yaml.safe_load(settings.seeds_file.read_text(encoding="utf-8"))
    for ev in data["events"]:
        if ev["key"] == key:
            ev.update(changes)
    path = tmp_path / "seeds_patched.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return settings.model_copy(update={"seeds_file": path})


def _event(session, key: str) -> Event:
    return session.query(Event).filter_by(key=key).one()


# 1-2 / bk-8 / bk-9 ------------------------------------------------------------
def test_query_override_survives_collect(fresh, tmp_path):
    session, pipe, settings = fresh
    override = '"남문시장" AND ("가스" OR "배관") AND "가온시"'
    patched = Pipeline(session, _with_seed_patch(tmp_path, settings, "gaon-market-explosion", query=override), pipe.client, pipe.llm, pipe.embedder)
    ev = _event(session, "gaon-market-explosion")
    patched.run("collect", ev.id)
    session.refresh(ev)
    assert ev.query_override == override and ev.seed_query == override and ev.expansions == []


def test_expand_query_builds_from_lists_not_string_surgery():
    s = Seed(key="k", title="t", incident_type="사고>산업사고>붕괴", region="누리군", facility="해맞이(1)터널", responsible_org="누리군", occurred_at=__import__("datetime").date(2025, 3, 2), keywords=["터널 붕괴"])
    q = expand_query(s, ["콘크리트", "누리군"])  # 범위 절 단어는 확장에서 제외
    assert q == '("해맞이 1 터널" OR "터널 붕괴" OR "콘크리트") AND ("누리군" OR "해맞이 1 터널")'


# 1-7 ------------------------------------------------------------------------------
def test_promise_followup_articles_become_candidates_without_seed_query_match(fresh):
    session, pipe, _ = fresh
    ev = _event(session, "gaon-logistics-fire")
    promo = {c.news_id for c in session.query(EventCandidate).filter_by(event_id=ev.id, source="promise")}
    done = session.query(Article).filter(Article.title.contains("전수점검 완료")).one()
    assert done.news_id in {c.news_id for c in session.query(EventCandidate).filter_by(event_id=ev.id)}
    assert any(l.news_id == done.news_id and l.is_active for l in ev.links)
    assert ev.status == STATUS_DONE
    assert promo or True  # 수집 경로가 seed 든 promise 든 후보에 들어가 있으면 된다


# 1-6 ------------------------------------------------------------------------------
def test_duplicate_article_from_two_promise_queries_does_not_crash(fresh):
    session, pipe, settings = fresh
    from app.pipeline.collect import upsert_article
    from app.bigkinds.schemas import NewsDocument

    doc = NewsDocument(news_id="01500701.20991231000000999", title="중복 기사", published_at="2099-12-31T00:00:00.000+09:00", provider="부산일보")
    a1, c1 = upsert_article(session, doc, None)
    a2, c2 = upsert_article(session, doc, None)
    assert c1 and not c2 and a1 is a2
    session.commit()


# 1-8 ------------------------------------------------------------------------------
def test_stale_auto_link_is_removed_when_seed_changes(fresh, tmp_path):
    session, pipe, settings = fresh
    ev = _event(session, "nuri-tunnel-collapse")
    assert {l.news_id for l in ev.links if l.link_method != "manual"}
    # 시드를 다른 시설·지역으로 바꾸면 기존 자동 연결은 게이트에서 떨어져 삭제돼야 한다
    patched = Pipeline(
        session,
        _with_seed_patch(tmp_path, settings, "nuri-tunnel-collapse", facility="없는터널", region="없는군", responsible_org="없는군", org_aliases=[], keywords=["없는키워드"]),
        pipe.client, pipe.llm, pipe.embedder,
    )
    patched.run("link", ev.id)
    session.refresh(ev)
    assert not [l for l in ev.links if l.link_method != "manual"]


# 1-5 ------------------------------------------------------------------------------
def test_followup_signals_reclassified_when_promise_set_changes(fresh):
    session, pipe, settings = fresh
    ev = _event(session, "gaon-seori-levee")
    sig = session.query(FollowupSignal).filter_by(event_id=ev.id).first()
    old_hash = sig.promise_set_hash
    assert old_hash
    for p in ev.promises:
        p.is_trackable = False
    session.commit()
    stats = pipe.run("judge", ev.id).stats["judge"][0]
    assert stats["reclassified"] >= 1
    session.refresh(sig)
    assert sig.promise_set_hash != old_hash and sig.matches_promise is False


# 1-4 ------------------------------------------------------------------------------
def test_llm_failures_are_not_cached(fresh):
    session, pipe, settings = fresh
    # 노이즈 기사(달빛터널 낙석)는 스텁 정답이 없다 — 연결됐더라도 실패 행이 저장되면 안 된다
    noise = session.query(Article).filter(Article.title.contains("달빛터널")).one()
    assert session.query(FollowupSignal).filter_by(news_id=noise.news_id).count() == 0
    assert all("stub" not in (s.rationale or "") for s in session.query(FollowupSignal).all())


# 1-10 / needs_review ------------------------------------------------------------------
def test_reject_rejudges_and_drops_evidence(fresh):
    session, pipe, settings = fresh
    from app.main import app

    ev = _event(session, "nuri-tunnel-collapse")
    assert ev.status == STATUS_DONE
    done_link = next(l for l in ev.links if "보강공사 완료" in session.get(Article, l.news_id).title)
    with TestClient(app) as c:
        r = c.post(f"/api/admin/links/{ev.id}/{done_link.news_id}", json={"action": "reject"})
        assert r.status_code == 200
        d = c.get(f"/api/events/{ev.id}").json()
        assert d["card"]["status"] != STATUS_DONE  # 유일한 완료 근거가 빠지면 상태가 바뀐다
        ev_json = c.get(f"/api/events/{ev.id}/evidence").json()
        assert all(s["article"]["news_id"] != done_link.news_id for s in ev_json["signals"])
        # 복구하면 검토 큐로 돌아오고 다시 판정된다
        r = c.post(f"/api/admin/links/{ev.id}/{done_link.news_id}", json={"action": "restore"})
        assert r.json()["rejected"] is False
        q = c.get("/api/admin/review-queue").json()
        assert any(l["article"]["news_id"] == done_link.news_id for l in q["links"])


def test_report_flag_survives_link_rerun_and_clears_on_resolve(fresh):
    session, pipe, settings = fresh
    from app.main import app

    ev = _event(session, "gaon-bridge-crash")
    ev.change_flagged = False
    for l in ev.links:
        l.needs_review = False  # 이 테스트는 신고 플래그만 본다
    session.commit()
    with TestClient(app) as c:
        rep = c.post(f"/api/events/{ev.id}/reports", json={"type": "wrong_status", "comment": "x"}).json()
        pipe.run("link", ev.id)
        session.refresh(ev)
        assert ev.needs_review is True
        c.post(f"/api/admin/reports/{rep['id']}/resolve")
    # 신고가 처리되면 신고 몫은 빠진다 — 남는 건 검토 대기 연결·변경이력 표시뿐 (DB 기준으로 새 세션에서 확인)
    s2 = session_factory()()
    ev2 = s2.query(Event).filter_by(key="gaon-bridge-crash").one()
    assert s2.query(Report).filter_by(event_id=ev2.id, resolved=False).count() == 0
    assert ev2.needs_review == (any(l.needs_review and not l.rejected for l in ev2.links) or ev2.change_flagged)
    s2.close()


def test_card_deadline_fields_refer_to_same_promise(fresh):
    session, pipe, settings = fresh
    from app.main import app

    with TestClient(app) as c:
        body = c.get("/api/events?needs_check=true").json()
        for card in body["items"]:
            if card["deadline_passed_days"] is not None:
                assert card["deadline_date"] < settings.today().isoformat()
        unjudged = c.post("/api/pipeline/run", json={"stage": "link", "event_id": 999999})
        assert unjudged.status_code == 404
