"""미결(未結) 백엔드 — FastAPI 앱.

  uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import admin, dashboard, events, notices, pipeline, press
from .config import get_settings
from .db.session import init_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("migyeol")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db()  # 누락 컬럼은 간이 마이그레이션으로 보충
    _close_interrupted_runs()
    for w in settings.startup_warnings():
        log.warning("설정 경고: %s", w)
    scheduler = None
    if settings.scheduler_enabled:
        from .pipeline.scheduler import build_scheduler

        scheduler = build_scheduler(settings)
        scheduler.start()
        log.info("scheduler started (04:00 refresh / 05:00 tracker, KST)")
    log.info(
        "bigkinds=%s llm=%s embedding=%s today=%s%s",
        settings.bigkinds_mode,
        settings.llm_mode,
        settings.embedding_backend,
        settings.today(),
        " (TODAY_OVERRIDE)" if settings.today_overridden() else "",
    )
    yield
    if scheduler is not None:
        scheduler.shutdown(wait=False)


def _close_interrupted_runs() -> None:
    """서버가 죽어 finished_at 이 비어 남은 실행 기록을 실패로 닫는다 (헤더가 '실행 중' 으로 영원히 보이지 않게)."""
    import fcntl

    from .db.models import PipelineRun, _now
    from .db.session import session_factory
    from .pipeline.runner import _lock_path

    # 다른 프로세스(CLI·다른 워커)가 파일 Lock 을 쥐고 실행 중이면 그 실행은 살아 있다 — 닫지 않는다 (3차 1-7)
    try:
        fh = open(_lock_path(), "a+")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.info("파이프라인이 다른 프로세스에서 실행 중 — 미완료 실행 기록을 닫지 않는다")
            fh.close()
            return
    except OSError:
        fh = None
    session = session_factory()()
    try:
        stale = session.query(PipelineRun).filter(PipelineRun.finished_at.is_(None)).all()
        for r in stale:
            r.ok = False
            r.error = "Interrupted: 서버 종료로 중단된 실행"
            r.finished_at = _now()
        if stale:
            session.commit()
            log.warning("중단된 실행 %d건을 실패로 닫았다", len(stale))
    finally:
        session.close()
        if fh is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            finally:
                fh.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="미결(未結) API", version="0.3.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list(),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(events.router)
    app.include_router(admin.router)
    app.include_router(pipeline.router)
    app.include_router(dashboard.router)  # v2 트랙 B
    app.include_router(press.router)  # v2 트랙 C
    app.include_router(notices.router)  # v2 트랙 C
    return app


app = create_app()
