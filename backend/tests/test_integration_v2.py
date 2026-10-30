"""v2 통합(공용 파일) 회귀 — 목록 API 의 org·followup 필터, 스케줄러의 게시판 확인 잡.

트랙 B(대시보드 기관 링크)와 트랙 C(게시판 확인)의 통합 요청을 공용 파일에 반영한 결과를 고정한다.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.config import FOLLOWUP_STATUSES, get_settings
from app.db.session import init_db, reset_engine, session_factory
from app.rules.orgs import canonical_org


@pytest.fixture(scope="module")
def v2_db(tmp_path_factory, fixtures_ready):
    from app.bigkinds import build_client
    from app.llm.client import build_llm
    from app.pipeline.embeddings import build_embedding
    from app.pipeline.runner import Pipeline

    url = f"sqlite:///{tmp_path_factory.mktemp('v2') / 'v2.db'}"
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
def client(v2_db):
    get_settings.cache_clear()
    os.environ["DATABASE_URL"] = v2_db
    init_db(v2_db)
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_org_filter_matches_canonical_key(client):
    all_items = client.get("/api/events?size=100").json()
    assert all_items["total"] == 8
    orgs = all_items["filters"]["orgs"]
    assert orgs and all(isinstance(o, str) and o for o in orgs)
    for org in orgs:
        body = client.get(f"/api/events?size=100&org={org}").json()
        assert body["total"] >= 1
        assert all((canonical_org(c["responsible_org"]) or "기관 미상") == org for c in body["items"])
    assert client.get("/api/events?org=없는기관").json()["total"] == 0


def test_followup_filter_splits_by_status(client):
    yes = client.get("/api/events?size=100&followup=true").json()
    no = client.get("/api/events?size=100&followup=false").json()
    assert yes["total"] + no["total"] == 8
    assert yes["total"] >= 1
    # 대시보드 '후속보도 확인' 과 같은 세 상태 (2026-10-30)
    assert all(c["status"] in FOLLOWUP_STATUSES for c in yes["items"])
    assert all(c["status"] not in FOLLOWUP_STATUSES for c in no["items"])
    assert yes["total"] == client.get("/api/dashboard/summary").json()["with_followup"]


def test_scheduler_has_notice_job():
    from app.pipeline.scheduler import build_scheduler

    sched = build_scheduler(get_settings())
    ids = {j.id for j in sched.get_jobs()}
    assert ids == {"daily_refresh", "daily_tracker", "daily_notices"}
    notices = sched.get_job("daily_notices")
    assert str(notices.trigger) == "cron[hour='5', minute='30']"
