"""Mock + Stub 으로 6단계를 끝까지 돌려 정답셋(gold/events.yaml) 상태와 비교한다."""

from __future__ import annotations

import json

import yaml
from fastapi.testclient import TestClient

from app.config import STATUSES, get_settings
from app.db.models import Event, Promise
from app.db.session import init_db, session_factory

FORBIDDEN = ("미이행", "불이행")


def test_statuses_match_gold(e2e_db):
    init_db(e2e_db)
    session = session_factory()()
    gold = yaml.safe_load(open(get_settings().gold_file, encoding="utf-8"))
    got = {e.key: e.status for e in session.query(Event).all()}
    expected = {g["key"]: g["expected_status"] for g in gold["events"]}
    assert got == expected
    assert set(got.values()) == set(STATUSES)  # 5개 상태가 모두 나온다
    session.close()


def test_promises_extracted_and_merged(e2e_db):
    init_db(e2e_db)
    session = session_factory()()
    ev = session.query(Event).filter_by(key="gaon-underpass-flood").one()
    ps = [p for p in ev.promises if p.is_trackable]
    assert {p.strength for p in ps} == {"확약", "계획"}
    firm = next(p for p in ps if p.strength == "확약")
    assert firm.actor_org == "가온시" and firm.deadline_date.isoformat() == "2026-10-31"
    assert firm.evidence_news_id  # 근거 기사 ID 저장
    weak = [p for p in ev.promises if p.strength == "의사표명"]
    assert weak and not weak[0].is_trackable  # '노력하겠다' 는 추적하지 않음
    session.close()


def test_priority_order_puts_deadline_passed_no_followup_first(e2e_db):
    init_db(e2e_db)
    session = session_factory()()
    top = session.query(Event).order_by(Event.priority_score.desc()).first()
    assert top.key == "nuri-landslide"  # 기한 경과 + 후속보도 부족
    session.close()


def test_tracker_flags_event_when_evidence_article_changes(e2e_db):
    init_db(e2e_db)
    session = session_factory()()
    ev = session.query(Event).filter_by(key="nuri-tunnel-collapse").one()
    assert ev.needs_review is True  # 완료 근거 기사가 Update 로 표시됨
    session.close()


def test_api_contract_and_no_forbidden_words(e2e_db):
    init_db(e2e_db)
    from app.main import app

    with TestClient(app) as c:
        r = c.get("/api/events?needs_check=true&sort=priority")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] >= 4
        card = body["items"][0]
        for k in ("id", "title", "status", "priority_score", "needs_check", "primary_promise", "trend_sparkline", "disclaimer"):
            assert k in card
        assert card["status"] in STATUSES
        assert card["deadline_passed_days"] is not None

        eid = card["id"]
        d = c.get(f"/api/events/{eid}").json()
        assert [t["phase"] for t in d["timeline"]] == ["발생", "원인조사", "대응발표", "조치진행", "결과확인", "기타"]
        for phase in d["timeline"]:
            for a in phase["articles"]:
                assert "content" not in a and len(a["hilight"]) <= 200  # 전문 없음
        assert d["trend"]["markers"]["today"] == "2026-09-11"

        ev = c.get(f"/api/events/{eid}/evidence").json()
        assert ev["rule"]["name"].startswith("R")
        assert "thresholds" in ev["rule"]

        rep = c.post(f"/api/events/{eid}/reports", json={"type": "wrong_status", "comment": "테스트 이의"})
        assert rep.status_code == 201
        q = c.get("/api/admin/review-queue").json()
        assert any(x["id"] == rep.json()["id"] for x in q["reports"])

        meta = c.get("/api/meta").json()
        assert meta["bigkinds_mode"] == "mock" and meta["llm_mode"] == "stub"
        assert c.get("/api/events/9999").status_code == 404
        assert c.get("/api/events?status=미결").status_code == 422

        blob = json.dumps([body, d, ev, q, meta], ensure_ascii=False)
        assert not any(w in blob for w in FORBIDDEN)


def test_admin_link_reject_is_sticky(e2e_db):
    init_db(e2e_db)
    from app.main import app

    from app.db.models import Article

    session = session_factory()()
    ev = session.query(Event).filter_by(key="nuri-tunnel-collapse").one()
    # 같은 지역 유사 사고(달빛터널 낙석)는 게이트를 통과해도 임베딩 점수가 낮아 연결되지 않아야 한다
    noise = session.query(Article).filter(Article.title.contains("달빛터널")).one()
    assert all(l.news_id != noise.news_id for l in ev.links)
    # 관리자가 해제한 연결은 타임라인에서 빠지고, 재실행해도 다시 붙지 않는다
    target = next(l for l in ev.links if "기소" in session.get(Article, l.news_id).title)
    session.close()
    with TestClient(app) as c:
        r = c.post(f"/api/admin/links/{ev.id}/{target.news_id}", json={"action": "reject"})
        assert r.status_code == 200 and r.json()["rejected"] is True
        d = c.get(f"/api/events/{ev.id}").json()
        assert all(a["news_id"] != target.news_id for ph in d["timeline"] for a in ph["articles"])
        c.post("/api/pipeline/run", json={"stage": "link", "event_id": ev.id})
        d = c.get(f"/api/events/{ev.id}").json()
        assert all(a["news_id"] != target.news_id for ph in d["timeline"] for a in ph["articles"])
