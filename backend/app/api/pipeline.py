from __future__ import annotations

import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..bigkinds import build_client
from ..config import DISCLAIMER, get_settings
from ..db.models import Event, PipelineRun
from ..db.session import get_session
from ..llm.client import build_llm
from ..pipeline.embeddings import build_embedding
from ..pipeline.runner import Pipeline
from . import serializers as ser

router = APIRouter(prefix="/api", tags=["pipeline"])


class RunIn(BaseModel):
    stage: Literal["discover", "collect", "link", "extract", "judge", "tracker", "all"] = "all"
    event_id: int | None = None


@router.post("/pipeline/run")
def run_pipeline(body: RunIn, session: Session = Depends(get_session)):
    settings = get_settings()
    if not settings.pipeline_api_enabled:
        raise HTTPException(status_code=403, detail="pipeline API disabled (PIPELINE_API_ENABLED=false)")
    started = time.monotonic()
    pipe = Pipeline(session, settings, build_client(settings), build_llm(settings), build_embedding(settings))
    try:
        run = pipe.run(body.stage, body.event_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)[:500]) from exc
    return {
        "run_id": run.id,
        "stage": run.stage,
        "ok": run.ok,
        "stats": run.stats.get("summary", run.stats),
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


@router.get("/meta")
def meta(session: Session = Depends(get_session)):
    settings = get_settings()
    last = session.query(PipelineRun).filter_by(ok=True).order_by(PipelineRun.id.desc()).first()
    return {
        "service": "미결",
        "bigkinds_mode": settings.bigkinds_mode,
        "llm_mode": settings.llm_mode,
        "llm_model": settings.llm_model if settings.llm_mode == "live" else "stub",
        "embedding_backend": settings.embedding_backend,
        "event_count": session.query(Event).count(),
        "today": settings.today().isoformat(),
        "last_pipeline_run": (
            {"stage": last.stage, "finished_at": ser._iso(last.finished_at), "ok": last.ok, "stats": last.stats.get("summary", {})}
            if last
            else None
        ),
        "disclaimer": DISCLAIMER,
    }


@router.get("/health")
def health():
    return {"ok": True}
