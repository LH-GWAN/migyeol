"""미결(未結) 백엔드 — FastAPI 앱.

  uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import admin, events, pipeline
from .config import get_settings
from .db.session import init_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("migyeol")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db()
    scheduler = None
    if settings.scheduler_enabled:
        from .pipeline.scheduler import build_scheduler

        scheduler = build_scheduler(settings)
        scheduler.start()
        log.info("scheduler started (04:00 refresh / 05:00 tracker, KST)")
    log.info("bigkinds=%s llm=%s embedding=%s", settings.bigkinds_mode, settings.llm_mode, settings.embedding_backend)
    yield
    if scheduler is not None:
        scheduler.shutdown(wait=False)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="미결(未結) API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list(),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(events.router)
    app.include_router(admin.router)
    app.include_router(pipeline.router)
    return app


app = create_app()
