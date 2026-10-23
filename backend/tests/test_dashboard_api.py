"""v2 §7.1 대시보드 집계 API (트랙 B) — 6개 엔드포인트를 가상 픽스처 DB 로 고정한다.

- 숫자는 목록 API(/api/events)·판정과 같은 기준이어야 한다 (상태 분포, 후속 기사 수, 기한 경과).
- 실험 리포트(experiment_latest.json)는 없을 때와 있을 때(§4.6 예시 JSON) 둘 다 확인한다.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import DISCLAIMER, STATUS_DONE, STATUSES, get_settings
from app.db.models import EventArticle
from app.db.session import init_db, reset_engine, session_factory

FORBIDDEN = ("미" + "이행", "불" + "이행", "거짓" + "말")

# v2 §4.6 예시 JSON (키 이름 고정 — 트랙 A 가 이 스키마로 쓴다)
EXPERIMENT_EXAMPLE = {
    "generated_at": "2026-10-25T21:00:00+09:00", "as_of": "2026-10-25", "bigkinds_mode": "record",
    "seeds_file": "seeds/events.yaml", "embedding_backend": "st", "llm_mode": "rules",
    "source_articles": {"collected": 0, "unique": 0},
    "events": {"total": 0, "with_followup": 0, "followup_rate": 0.0, "by_status": {"조치 완료 근거 확인": 0, "조치 진행 정황 확인": 0, "새로운 문제 발생": 0, "후속보도 부족": 0, "판단 불가": 0},
               "with_org_notice": 0, "articles_per_event": {"median": 0, "max": 0}},
    "windows_days": {"30": 0, "90": 0, "180": 0, "365": 0},
    "signals": {"완료": 0, "진행": 0, "새로운문제": 0},
    "by_incident_type": [{"incident_type": "재해>자연재해", "events": 0, "with_followup": 0, "rate": 0.0}],
    "by_org": [{"org": "서울시", "events": 0, "with_followup": 0, "rate": 0.0, "latest_followup_at": "2025-08-01", "event_ids": []}],
    "timeseries": [{"bucket": "2023-07", "occurred": 0, "followup_articles": 0, "dead": None, "injured": None}],
    "daily": [{"date": "2023-07-15", "occurred": 0, "followup_articles": 0}],
    "labels": {"labeled": 0, "followup_precision": None, "followup_recall": None, "cluster_precision": None, "org_accuracy": None},
    "cost": {"api_calls": {"search_news": 0, "search_quotation": 0, "issue_ranking": 0, "time_line": 0, "word_cloud": 0}, "elapsed_sec": 0, "recordings": 0},
    "success": {"min_followup_events": 5, "target_followup_events": 20, "met_min": False, "met_target": False},
}


@pytest.fixture(scope="module")
def dash_db(tmp_path_factory, fixtures_ready):
    """이 모듈 전용 DB — 다른 테스트가 공유 e2e DB 를 바꿔도 숫자가 흔들리지 않게."""
    from app.bigkinds import build_client
    from app.llm.client import build_llm
    from app.pipeline.embeddings import build_embedding
    from app.pipeline.runner import Pipeline

    url = f"sqlite:///{tmp_path_factory.mktemp('dash') / 'dash.db'}"
    get_settings.cache_clear()
    os.environ["DATABASE_URL"] = url
    settings = get_settings()
    reset_engine()
    init_db(url, drop=True)
    session = session_factory()()
    Pipeline(session, settings, build_client(settings), build_llm(settings), build_embedding(settings)).run("all")
    session.close()
    return url


@pytest.fixture
def client(dash_db, tmp_path, monkeypatch):
    """실험 리포트 경로는 항상 임시 경로 (저장소 reports/ 의 실제 파일과 무관)."""
    get_settings.cache_clear()
    os.environ["DATABASE_URL"] = dash_db
    init_db(dash_db)
    monkeypatch.setattr(get_settings(), "experiment_latest_path", tmp_path / "experiment_latest.json")
    from app.api.dashboard import invalidate_facts_cache
    from app.main import app

    invalidate_facts_cache()  # 테스트가 연결을 바꿔도 TTL 캐시가 옛 숫자를 돌려주지 않게

    with TestClient(app) as c:
        yield c


def _write_experiment(c_settings_path: Path, data: dict) -> None:
    c_settings_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _all_cards(c: TestClient) -> list[dict]:
    body = c.get("/api/events?size=100").json()
    assert body["total"] == len(body["items"])
    return body["items"]


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------


def test_summary_without_experiment(client):
    r = client.get("/api/dashboard/summary")
    assert r.status_code == 200
    s = r.json()
    for k in ("events_total", "with_followup", "followup_rate", "overdue", "with_org_notice", "by_status", "last_run", "as_of", "experiment"):
        assert k in s
    assert s["experiment"] is None
    assert s["disclaimer"] == DISCLAIMER
    assert s["as_of"] == "2026-09-11"
    assert s["events_total"] == 8
    assert s["by_status"] == {"조치 완료 근거 확인": 2, "조치 진행 정황 확인": 2, "새로운 문제 발생": 1, "후속보도 부족": 2, "판단 불가": 1}
    assert list(s["by_status"]) == list(STATUSES)
    assert s["with_followup"] == 5 and s["followup_rate"] == 0.625
    assert s["overdue"] == 3
    assert s["last_run"] and s["last_run"].endswith("+09:00")


def test_summary_matches_event_list(client):
    s = client.get("/api/dashboard/summary").json()
    cards = _all_cards(client)
    assert s["events_total"] == len(cards)
    for st in STATUSES:
        assert s["by_status"][st] == sum(1 for c in cards if c["status"] == st)
    assert s["followup_articles"] == sum(c["followup_count"] for c in cards)
    # 기한 경과 = 카드의 기한 경과 일수가 있고 완료가 아닌 사건 (needs_check 기준과 같음)
    assert s["overdue"] == sum(1 for c in cards if c["deadline_passed_days"] is not None and c["status"] != STATUS_DONE)
    # R1~R3(완료·진행·새로운 문제) 사건은 판정이 유효 신호를 채택했으므로 반드시 '후속보도 확인' 이다
    r1_to_r3 = {c["id"] for c in cards if c["status"] in ("조치 완료 근거 확인", "조치 진행 정황 확인", "새로운 문제 발생")}
    confirmed = {r["event_id"] for r in client.get("/api/dashboard/recent-followups?limit=50").json()}
    assert r1_to_r3 <= confirmed and len(confirmed) == s["with_followup"]


def test_summary_with_experiment_example(client):
    path = get_settings().experiment_latest_path
    _write_experiment(path, EXPERIMENT_EXAMPLE)
    s = client.get("/api/dashboard/summary").json()
    exp = s["experiment"]
    assert exp is not None
    # 요약 = 큰 목록을 뺀 나머지, 키 이름은 §4.6 그대로
    for k in ("by_incident_type", "by_org", "timeseries", "daily"):
        assert k not in exp
    for k in ("generated_at", "as_of", "source_articles", "events", "windows_days", "signals", "labels", "cost", "success"):
        assert exp[k] == EXPERIMENT_EXAMPLE[k]
    # DB 집계는 실험 리포트와 섞이지 않는다
    assert s["events_total"] == 8


def test_summary_survives_broken_experiment_file(client):
    get_settings().experiment_latest_path.write_text("{not json", encoding="utf-8")
    assert client.get("/api/dashboard/summary").json()["experiment"] is None
    r = client.get("/api/dashboard/experiment")
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# experiment
# ---------------------------------------------------------------------------


def test_experiment_404_when_missing(client):
    r = client.get("/api/dashboard/experiment")
    assert r.status_code == 404
    assert "experiment_latest.json" in r.json()["detail"]


def test_experiment_returns_file_verbatim(client):
    _write_experiment(get_settings().experiment_latest_path, EXPERIMENT_EXAMPLE)
    r = client.get("/api/dashboard/experiment")
    assert r.status_code == 200
    assert r.json() == EXPERIMENT_EXAMPLE


# ---------------------------------------------------------------------------
# timeseries
# ---------------------------------------------------------------------------


def test_timeseries_month_is_continuous_and_matches_db(client):
    rows = client.get("/api/dashboard/timeseries?interval=month").json()
    buckets = [r["bucket"] for r in rows]
    assert buckets[0] == "2024-09" and buckets[-1] == "2026-09"  # 가장 이른 발생 월 ~ 기준일 월
    assert len(buckets) == 25 and len(set(buckets)) == 25  # 빈 달도 0 으로 채운다
    for r in rows:
        assert set(r) >= {"bucket", "occurred", "followup_articles", "dead", "injured"}
        assert r["dead"] is None and r["injured"] is None  # 실험 리포트가 없으면 피해 규모는 추정하지 않는다
    cards = _all_cards(client)
    assert sum(r["occurred"] for r in rows) == len(cards)
    assert sum(r["followup_articles"] for r in rows) == sum(c["followup_count"] for c in cards)
    by_bucket = {r["bucket"]: r for r in rows}
    assert by_bucket["2025-07"]["occurred"] == 1  # 달빛지하차도 침수 2025-07-15
    assert sum(r["confirmed_followups"] for r in rows) <= sum(r["followup_articles"] for r in rows)


def test_timeseries_day_and_range(client):
    rows = client.get("/api/dashboard/timeseries?interval=day&from=2025-07-01&until=2025-07-31").json()
    assert len(rows) == 31 and rows[0]["bucket"] == "2025-07-01" and rows[-1]["bucket"] == "2025-07-31"
    assert {r["bucket"] for r in rows if r["occurred"]} == {"2025-07-15"}
    month = client.get("/api/dashboard/timeseries?interval=month&from=2025-01-01&until=2025-12-31").json()
    assert [r["bucket"] for r in month][:2] == ["2025-01", "2025-02"] and len(month) == 12
    assert client.get("/api/dashboard/timeseries?interval=day&from=2025-08-01&until=2025-07-01").status_code == 422
    assert client.get("/api/dashboard/timeseries?interval=day&from=2020-01-01&until=2025-07-01").status_code == 422
    assert client.get("/api/dashboard/timeseries?interval=week").status_code == 422


def test_timeseries_takes_casualties_from_experiment(client):
    data = copy.deepcopy(EXPERIMENT_EXAMPLE)
    data["timeseries"].append({"bucket": "2025-07", "occurred": 1, "followup_articles": 0, "dead": 2, "injured": 5})
    _write_experiment(get_settings().experiment_latest_path, data)
    rows = {r["bucket"]: r for r in client.get("/api/dashboard/timeseries?interval=month").json()}
    assert rows["2025-07"]["dead"] == 2 and rows["2025-07"]["injured"] == 5
    assert rows["2025-08"]["dead"] is None
    assert rows["2025-07"]["occurred"] == 1  # 발생 건수는 DB 집계 그대로


# ---------------------------------------------------------------------------
# by-incident
# ---------------------------------------------------------------------------


def test_by_incident_groups_by_family(client):
    rows = client.get("/api/dashboard/by-incident").json()
    fams = {r["incident_type"]: r for r in rows}
    assert set(fams) == {"사고>산업사고", "재해>자연재해", "사고>교통사고"}
    for r in rows:
        assert set(r) >= {"incident_type", "events", "with_followup", "rate"}
        assert 0 <= r["with_followup"] <= r["events"]
        assert r["rate"] == round(r["with_followup"] / r["events"], 3)
    assert sum(r["events"] for r in rows) == 8
    assert sum(r["with_followup"] for r in rows) == client.get("/api/dashboard/summary").json()["with_followup"]
    assert fams["사고>산업사고"]["events"] == 3 and fams["사고>산업사고"]["with_followup"] == 3
    assert [r["events"] for r in rows] == sorted((r["events"] for r in rows), reverse=True)


# ---------------------------------------------------------------------------
# by-org
# ---------------------------------------------------------------------------


def test_by_org_default_sort_puts_followup_first(client):
    rows = client.get("/api/dashboard/by-org").json()
    for r in rows:
        assert set(r) >= {"org", "events", "with_followup", "rate", "latest_followup_at", "event_ids"}
        assert len(r["event_ids"]) == r["events"]
        assert (r["latest_followup_at"] is None) == (r["with_followup"] == 0)
    keys = [(-r["with_followup"], -r["rate"]) for r in rows]
    assert keys == sorted(keys)  # 후속보도 확인 건수 내림차순, 동률이면 비율
    assert rows[0]["org"] == "가온시" and rows[0]["with_followup"] == 2
    seen_zero = False
    for r in rows:  # 확인된 기관이 위, 없는 기관은 아래
        if r["with_followup"] == 0:
            seen_zero = True
        else:
            assert not seen_zero
    assert sum(r["events"] for r in rows) == 8


def test_by_org_sort_options_and_canonical_key(client):
    by_rate = client.get("/api/dashboard/by-org?sort=rate").json()
    assert [r["rate"] for r in by_rate] == sorted((r["rate"] for r in by_rate), reverse=True)
    by_events = client.get("/api/dashboard/by-org?sort=events").json()
    assert [r["events"] for r in by_events] == sorted((r["events"] for r in by_events), reverse=True)
    assert client.get("/api/dashboard/by-org?sort=bogus").status_code == 422
    from app.rules.orgs import canonical_org

    cards = {c["id"]: c for c in _all_cards(client)}
    for r in by_events:
        for eid in r["event_ids"]:
            assert (canonical_org(cards[eid]["responsible_org"]) or "기관 미상") == r["org"]


def test_by_org_merges_aliases(dash_db, client):
    """'서울특별시' 와 '서울시청' 은 같은 기관 키로 묶인다 (canonical_org)."""
    from app.db.models import Event

    session = session_factory()()
    evs = session.query(Event).order_by(Event.id).limit(2).all()
    originals = [e.responsible_org for e in evs]
    try:
        evs[0].responsible_org, evs[1].responsible_org = "서울특별시", "서울시청"
        session.commit()
        rows = client.get("/api/dashboard/by-org").json()
        seoul = [r for r in rows if r["org"] == "서울시"]
        assert len(seoul) == 1 and sorted(seoul[0]["event_ids"]) == sorted(e.id for e in evs)
        assert seoul[0]["raw_names"] == ["서울시청", "서울특별시"]
    finally:
        for e, o in zip(evs, originals):
            e.responsible_org = o
        session.commit()
        session.close()


# ---------------------------------------------------------------------------
# recent-followups
# ---------------------------------------------------------------------------


def test_recent_followups_are_valid_signals_newest_first(client):
    rows = client.get("/api/dashboard/recent-followups").json()
    assert 0 < len(rows) <= 10
    for r in rows:
        assert set(r) >= {"event_id", "event_title", "signal", "evidence_span", "news_id", "title", "provider", "published_at", "link"}
        assert r["signal"] in ("완료", "진행", "새로운문제")
        assert r["confidence"] >= get_settings().signal_min_conf
    dates = [r["published_at"] for r in rows]
    assert dates == sorted(dates, reverse=True)
    assert len(client.get("/api/dashboard/recent-followups?limit=2").json()) == 2
    assert client.get("/api/dashboard/recent-followups?limit=0").status_code == 422
    assert client.get("/api/dashboard/recent-followups?limit=51").status_code == 422
    # 피드의 사건 집합 = 후속보도 확인 사건 (전체를 받으면)
    every = client.get("/api/dashboard/recent-followups?limit=50").json()
    assert len({r["event_id"] for r in every}) == client.get("/api/dashboard/summary").json()["with_followup"]


def test_rejected_link_drops_out_of_dashboard(dash_db, client):
    """해제된 연결의 신호는 집계에서 빠진다 (활성 연결만)."""
    first = client.get("/api/dashboard/recent-followups?limit=1").json()[0]
    session = session_factory()()
    link = session.get(EventArticle, (first["event_id"], first["news_id"]))
    try:
        link.rejected = True
        session.commit()
        ids = {r["news_id"] for r in client.get("/api/dashboard/recent-followups?limit=50").json()}
        assert first["news_id"] not in ids
    finally:
        link.rejected = False
        session.commit()
        session.close()


def test_dashboard_has_no_forbidden_words(client):
    _write_experiment(get_settings().experiment_latest_path, EXPERIMENT_EXAMPLE)
    for p in ("summary", "timeseries", "by-incident", "by-org", "recent-followups", "experiment"):
        text = client.get(f"/api/dashboard/{p}").text
        assert not any(w in text for w in FORBIDDEN)


# ---------------------------------------------------------------------------
# export_dashboard.py
# ---------------------------------------------------------------------------


def test_export_dashboard_writes_aggregate_file(client, tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import export_dashboard

    out = export_dashboard.export(tmp_path)
    assert out.name == "dashboard_20260911.json"
    data = json.loads(out.read_text(encoding="utf-8"))
    for k in ("summary", "daily", "monthly", "by_incident", "by_org", "recent_followups", "disclaimer"):
        assert k in data
    assert data["summary"]["events_total"] == 8
    assert sum(r["occurred"] for r in data["daily"]) == 8
    assert data["by_org"] == client.get("/api/dashboard/by-org").json()
