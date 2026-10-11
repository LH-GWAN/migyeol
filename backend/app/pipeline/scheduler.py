"""APScheduler 잡 — 매일 04:00 KST 재조회·재판정, 05:00 KST 변경이력 확인 (SCHEDULER_ENABLED=true 일 때만 시작)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from ..config import Settings

log = logging.getLogger("migyeol.scheduler")
KST = timezone(timedelta(hours=9))


def next_scheduled_run(now: datetime | None = None) -> datetime:
    now = now or datetime.now(KST)
    candidate = now.replace(hour=4, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _run(stage: str, settings: Settings) -> None:
    from ..bigkinds import build_client
    from ..db.session import session_factory
    from ..llm.client import build_llm
    from .embeddings import build_embedding
    from .runner import Pipeline

    from .runner import PipelineBusy

    session = session_factory()()
    try:
        Pipeline(session, settings, build_client(settings), build_llm(settings), build_embedding(settings)).run(stage)
    except PipelineBusy:
        log.warning("scheduled %s skipped: pipeline busy", stage)
    except Exception:  # noqa: BLE001
        log.exception("scheduled %s failed", stage)
    finally:
        session.close()


def build_scheduler(settings: Settings) -> BackgroundScheduler:
    # 단일 워커, 동시 실행 금지, misfire 는 1시간까지 늦게라도 실행 (Pipeline 의 전역 Lock 이 2차 방어)
    sched = BackgroundScheduler(
        timezone="Asia/Seoul",
        executors={"default": {"type": "threadpool", "max_workers": 1}},
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600},
    )
    sched.add_job(_run, CronTrigger(hour=4, minute=0), args=["all", settings], id="daily_refresh", replace_existing=True)
    sched.add_job(_run, CronTrigger(hour=5, minute=0), args=["tracker", settings], id="daily_tracker", replace_existing=True)
    return sched
