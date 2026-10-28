from __future__ import annotations

import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent

# 가상 정답셋(fixtures/gold)이 설계된 규칙 임계값. 본선 스윕 결과로 .env 나 config.py 기본값을 바꿔도 이 값은 그대로 둔다.
FIXTURE_THRESHOLDS = {
    "LINK_AUTO": "0.55",
    "LINK_REVIEW": "0.40",
    "SIGNAL_MIN_CONF": "0.6",
    "COMPLETION_MIN_CONF": "0.7",
    "STALE_FOLLOWUP_DAYS": "60",
    "MAX_ARTICLES_PER_EVENT": "300",
    "QUOTATION_WINDOW_DAYS": "90",
    "FOLLOWUP_GRACE_DAYS": "7",
}

# 테스트는 항상 mock/stub/hash + 고정 날짜 + 가상 픽스처로 돈다 (.env, 실사건 seeds/·gold/ 와 무관)
os.environ.update(
    {
        "BIGKINDS_MODE": "mock",
        "LLM_MODE": "stub",
        "EMBEDDING_BACKEND": "hash",
        "TODAY_OVERRIDE": "2026-09-11",
        "SCHEDULER_ENABLED": "false",
        "PIPELINE_API_ENABLED": "true",
        "FIXTURES_DIR": str(BACKEND / "fixtures"),
        "SEEDS_FILE": str(BACKEND / "fixtures" / "seeds" / "events.yaml"),
        "GOLD_FILE": str(BACKEND / "fixtures" / "gold" / "events.yaml"),
        # 학습 기반 재순위·2단계 후속 신호 보강(2026-10-25 기본값)은 실사건 라벨로 고른 설정이다 — 가상 정답셋은 이전 규칙으로 설계됐으므로 끈 상태로
        # (tests/test_rerank.py·test_followup_v3.py 는 설정을 직접 준다)
        "LINK_RERANKER": "none",
        "FOLLOWUP_CUTOFF": "grace",
        "FOLLOWUP_MIN_LINK_PROB": "0",
        "FOLLOWUP_EXPRESSION_FIXES": "false",
        "FOLLOWUP_HEADLINE_EXPRESSIONS": "false",
        "FOLLOWUP_EVENT_PATH": "false",
        **FIXTURE_THRESHOLDS,
    }
)


@pytest.fixture(scope="session")
def fixtures_ready() -> Path:
    """픽스처가 없으면 생성기로 만든다 (오늘 날짜 2026-09-11 기준)."""
    articles = BACKEND / "fixtures" / "bigkinds" / "articles.json"
    if not articles.exists():
        import subprocess
        import sys

        subprocess.run([sys.executable, str(BACKEND / "scripts" / "make_fixtures.py"), "--today", "2026-09-11"], check=True)
    return BACKEND / "fixtures"


@pytest.fixture(scope="session")
def e2e_db(tmp_path_factory, fixtures_ready) -> str:
    """Mock+Stub 으로 전체 파이프라인을 한 번 돌린 SQLite DB URL."""
    from app.bigkinds import build_client
    from app.config import get_settings
    from app.db.session import init_db, reset_engine, session_factory
    from app.llm.client import build_llm
    from app.pipeline.embeddings import build_embedding
    from app.pipeline.runner import Pipeline

    db = tmp_path_factory.mktemp("db") / "e2e.db"
    url = f"sqlite:///{db}"
    get_settings.cache_clear()
    os.environ["DATABASE_URL"] = url
    settings = get_settings()
    reset_engine()
    init_db(url, drop=True)
    session = session_factory()()
    Pipeline(session, settings, build_client(settings), build_llm(settings), build_embedding(settings)).run("all")
    session.close()
    return url
