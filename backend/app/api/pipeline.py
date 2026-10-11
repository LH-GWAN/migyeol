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
from ..pipeline.runner import Pipeline, PipelineBusy
from . import serializers as ser
from .deps import require_admin

router = APIRouter(prefix="/api", tags=["pipeline"])


class RunIn(BaseModel):
    stage: Literal["discover", "collect", "link", "extract", "judge", "tracker", "all"] = "all"
    event_id: int | None = None


@router.post("/pipeline/run", dependencies=[Depends(require_admin)])
def run_pipeline(body: RunIn, session: Session = Depends(get_session)):
    settings = get_settings()
    if not settings.pipeline_api_enabled:
        raise HTTPException(status_code=403, detail="pipeline API disabled (PIPELINE_API_ENABLED=false)")
    if body.event_id is not None:
        ev = session.get(Event, body.event_id)
        if ev is None or not ev.active:
            raise HTTPException(status_code=404, detail=f"event {body.event_id} not found")
    started = time.monotonic()
    client = build_client(settings)
    pipe = Pipeline(session, settings, client, build_llm(settings), build_embedding(settings))
    try:
        run = pipe.run(body.stage, body.event_id)
    except PipelineBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        # 트레이스백은 pipeline_runs.error 와 서버 로그에만. 응답에는 첫 줄 요약만.
        raise HTTPException(status_code=500, detail=str(exc).splitlines()[0][:300]) from exc
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    return {
        "run_id": run.id,
        "stage": run.stage,
        "ok": run.ok,
        "stats": run.stats.get("summary", {}),
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


def _run_out(r: PipelineRun | None) -> dict | None:
    if r is None:
        return None
    return {
        "stage": r.stage,
        "event_id": r.event_id,
        "finished_at": ser._iso(r.finished_at),
        "ok": r.ok,
        "error_summary": (r.error or "").splitlines()[0][:200] if r.error else None,
        "stats": r.stats.get("summary", {}) if isinstance(r.stats, dict) else {},
    }


@router.get("/meta")
def meta(session: Session = Depends(get_session)):
    settings = get_settings()
    last_refresh = (
        session.query(PipelineRun)
        .filter(PipelineRun.ok.is_(True), PipelineRun.stage.in_(("all", "judge")), PipelineRun.event_id.is_(None))
        .order_by(PipelineRun.id.desc())
        .first()
    )
    last_run = session.query(PipelineRun).order_by(PipelineRun.id.desc()).first()
    return {
        "service": "미결",
        "bigkinds_mode": settings.bigkinds_mode,
        "llm_mode": settings.llm_mode,
        "llm_model": settings.llm_model if settings.llm_mode == "live" else "stub",
        "embedding_backend": settings.embedding_backend,
        "event_count": session.query(Event).filter(Event.active.is_(True)).count(),
        "today": settings.today().isoformat(),
        "today_overridden": settings.today_overridden(),
        "scheduler_enabled": settings.scheduler_enabled,
        "warnings": settings.startup_warnings(),
        "last_pipeline_run": _run_out(last_refresh),
        "last_run": _run_out(last_run),
        "disclaimer": DISCLAIMER,
    }


@router.get("/health")
def health():
    return {"ok": True}
