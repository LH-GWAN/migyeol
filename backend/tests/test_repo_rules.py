"""저장소 수준 규칙: 금지어(명세 §2-2·§14), 가상 픽스처와 실사건 데이터셋 분리."""

from __future__ import annotations

import os
import re
import shutil
from datetime import date
from pathlib import Path

import pytest

from app.config import BACKEND_DIR, REPO_DIR, Settings
from app.pipeline.seeds import load_seeds

# 금지어를 소스에 그대로 쓰지 않는다 — 이 파일도 §14 grep 대상이다
FORBIDDEN = ("미" + "이행", "불" + "이행", "거짓" + "말")
# 의존성·빌드 산출물 (어느 깊이든 이름으로 제외)
SKIP_DIR_NAMES = {".venv", "node_modules", ".next", "__pycache__", ".pytest_cache", ".ruff_cache", ".git"}
# 실기사가 들어가는 로컬 데이터 (gitignore 대상, 저장소 경로로 제외)
# + 로컬에서 학습한 교차 인코더(gitignore) — tokenizer.json 은 제3자 한국어 어휘 사전이라 우리 문장이 아니다
SKIP_PATHS = {Path("backend/data"), Path("backend/fixtures/bigkinds/recordings"), Path("backend/models/cross_encoder")}
BINARY_SUFFIXES = {".db", ".sqlite", ".pyc", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".woff", ".woff2", ".ttf", ".otf", ".zip"}
SF_DATALESS = 0x40000000  # macOS stat 플래그


def test_no_forbidden_words_in_backend_and_frontend():
    """§14 체크리스트의 금지어 grep(backend, frontend) 결과가 비어 있어야 한다. 로컬 데이터·녹화는 제외."""
    hits = []
    for top in ("backend", "frontend"):
        for root, dirs, files in os.walk(REPO_DIR / top):
            rel_root = Path(root).relative_to(REPO_DIR)
            dirs[:] = [d for d in dirs if d not in SKIP_DIR_NAMES and rel_root / d not in SKIP_PATHS]
            for name in files:
                path = Path(root) / name
                if path.suffix.lower() in BINARY_SUFFIXES:
                    continue
                st = path.stat()
                # iCloud 가 내용을 내려받지 않은 자리표시 파일(dataless)은 읽기가 타임아웃난다 — grep 도 건너뛴다
                if st.st_size == 0 or getattr(st, "st_flags", 0) & SF_DATALESS:
                    continue
                text = path.read_bytes().decode("utf-8", errors="ignore")
                for i, line in enumerate(text.splitlines(), 1):
                    if any(w in line for w in FORBIDDEN):
                        hits.append(f"{path.relative_to(REPO_DIR)}:{i}")
    assert not hits, hits


def _py_sources(*tops: str):
    for top in tops:
        for root, dirs, files in os.walk(BACKEND_DIR / top):
            dirs[:] = [d for d in dirs if d not in SKIP_DIR_NAMES]
            for name in files:
                path = Path(root) / name
                if path.suffix != ".py":
                    continue
                st = path.stat()
                if st.st_size == 0 or getattr(st, "st_flags", 0) & SF_DATALESS:
                    continue
                yield path, path.read_bytes().decode("utf-8", errors="ignore")


# 언론사 원문 크롤링 금지(v2 §2-1) — HTML 파서는 공공기관 게시판 확인(app/notices/) 안에서만 쓴다
_HTML_PARSER_RE = re.compile(
    r"^\s*(?:import|from)\s+(?:bs4|lxml|selectolax|html5lib|scrapy|selenium|playwright|parsel|pyquery|requests_html)\b|BeautifulSoup\(|HTMLParser\b",
    re.M,
)
_URL_RE = re.compile(r"https?://[A-Za-z0-9.\-]+")
# 빅카인즈 OpenAPI 는 허용된 데이터 출처(웹 페이지가 아니라 API) — 뉴스 도메인 검사에서 이 호스트만 뺀다
_API_HOSTS = {"tools.kinds.or.kr"}


def test_html_parsing_only_inside_notices():
    """HTML 파서 import 는 backend/app/notices/ 에서만 (뉴스 원문을 파싱하는 코드가 다른 곳에 생기지 않게)."""
    hits = []
    for path, text in _py_sources("app", "scripts"):
        rel = path.relative_to(BACKEND_DIR)
        if rel.parts[:2] == ("app", "notices"):
            continue
        if _HTML_PARSER_RE.search(text):
            hits.append(str(rel))
    assert not hits, hits


def test_no_news_domain_requests_in_code():
    """코드 어디에도 뉴스 도메인 URL 이 없다(빅카인즈 API 제외). 기관 게시판 요청기는 뉴스 도메인을 코드에서 거부한다."""
    from app.notices.guard import BlockedURL, check_url, host_of, is_news_host

    hits = []
    for path, text in _py_sources("app", "scripts"):
        for url in _URL_RE.findall(text):
            host = host_of(url)
            if host not in _API_HOSTS and is_news_host(host):
                hits.append(f"{path.relative_to(BACKEND_DIR)}: {url}")
    assert not hits, hits
    # 허용 접미(.or.kr)에 걸리는 뉴스 데이터 서비스도 거부된다
    for url in ("https://www.yna.co.kr/view/AKR1", "https://n.news.naver.com/article/1", "https://www.bigkinds.or.kr/v2/news", "https://www.kinds.or.kr/"):
        with pytest.raises(BlockedURL):
            check_url(url, ".go.kr,.or.kr,korea.kr")


def test_dataset_paths_follow_mode():
    fx = BACKEND_DIR / "fixtures"
    mock = Settings(_env_file=None, bigkinds_mode="mock", fixtures_dir=fx, seeds_file=None, gold_file=None)
    assert mock.seeds_file == fx / "seeds" / "events.yaml"
    assert mock.gold_file == fx / "gold" / "events.yaml"

    for mode in ("record", "live"):
        real = Settings(_env_file=None, bigkinds_mode=mode, seeds_file="", gold_file="")  # .env 의 빈 값도 기본값
        assert real.seeds_file == BACKEND_DIR / "seeds" / "events.yaml"
        assert real.gold_file == BACKEND_DIR / "gold" / "events.yaml"

    custom = Settings(_env_file=None, bigkinds_mode="record", seeds_file="/tmp/s.yaml", gold_file="gold/v2.yaml")
    assert custom.seeds_file == Path("/tmp/s.yaml")
    assert custom.gold_file == BACKEND_DIR / "gold" / "v2.yaml"  # 상대경로는 backend/ 기준


def test_real_seeds_do_not_contain_fixture_events():
    """가상 사건이 실사건 시드에 섞이면 record/live 에서 가상 지명으로 실 API 를 부른다."""
    fixture_keys = {s.key for s in load_seeds(BACKEND_DIR / "fixtures" / "seeds" / "events.yaml")}
    real_keys = {s.key for s in load_seeds(BACKEND_DIR / "seeds" / "events.yaml")}
    assert len(fixture_keys) == 8
    assert not fixture_keys & real_keys


@pytest.fixture
def discover_pipeline(tmp_path):
    """실사건 템플릿(빈 시드) 사본을 시드로 쓰는 mock 파이프라인. 초안은 tmp_path 에 쓰인다."""
    from app.bigkinds import build_client
    from app.db.session import init_db, reset_engine, session_factory
    from app.llm.client import build_llm
    from app.pipeline.embeddings import build_embedding
    from app.pipeline.runner import Pipeline

    from app.pipeline import runner as runner_mod

    seeds = tmp_path / "events.yaml"
    seeds.write_text("events: []\n", encoding="utf-8")  # 빈 실사건 시드 (실제 seeds/events.yaml 은 v2 실험 뒤 채워져 있다)
    settings = Settings(seeds_file=seeds)
    reset_engine()
    init_db(f"sqlite:///{tmp_path / 'discover.db'}", drop=True)
    session = session_factory()()
    # 실 실험이 backend/data 의 pipeline.lock 을 쥐고 있어도 테스트가 돈다 (v2 통합 — 트랙 A 요청)
    orig_lock = runner_mod._lock_path
    runner_mod._lock_path = lambda: tmp_path / "pipeline.lock"
    try:
        yield Pipeline(session, settings, build_client(settings), build_llm(settings), build_embedding(settings)), tmp_path
    finally:
        runner_mod._lock_path = orig_lock
        session.close()
    session.close()
    reset_engine()


def test_discover_without_dates_fails_loudly(discover_pipeline):
    pipe, tmp = discover_pipeline
    with pytest.raises(RuntimeError, match="날짜"):
        pipe.run("discover")
    assert not (tmp / "events.draft.yaml").exists()


def test_discover_uses_given_date_range(discover_pipeline):
    pipe, tmp = discover_pipeline
    run = pipe.run("discover", dates=[date(2025, 7, 14), date(2025, 7, 15)])
    assert run.ok and run.stats["dates"] == ["2025-07-14", "2025-07-15"]
    assert run.stats["candidates"] >= 1
    assert load_seeds(tmp / "events.draft.yaml")
