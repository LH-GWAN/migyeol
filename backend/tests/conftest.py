from __future__ import annotations

import os
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent

# 테스트는 항상 mock/stub/hash + 고정 날짜로 돈다 (.env 와 무관)
os.environ.update(
    {
        "BIGKINDS_MODE": "mock",
        "LLM_MODE": "stub",
        "EMBEDDING_BACKEND": "hash",
        "TODAY_OVERRIDE": "2026-09-11",
        "SCHEDULER_ENABLED": "false",
        "PIPELINE_API_ENABLED": "true",
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
