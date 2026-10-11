"""파이프라인 오케스트레이터. 단계별 실행 가능, 모든 실행은 pipeline_runs 에 기록한다.

- 사건 단위 try/except: 한 사건의 API·LLM 실패가 다른 사건을 막지 않는다 (stats["failures"] 에 남긴다).
- 프로세스 전역 Lock: 스케줄러·API 가 동시에 실행하지 않는다 (API 는 409).
- 실행당 LLM 호출 상한(llm_max_calls_per_run)을 넘으면 나머지는 다음 실행으로 미룬다.
"""

from __future__ import annotations

import logging
import threading
import traceback
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from ..bigkinds.protocol import BigKindsClient
from ..config import Settings
from ..db.models import Event, PipelineRun
from ..llm.client import LLMClient
from . import collect as collect_mod
from . import discover as discover_mod
from . import extract as extract_mod
from . import judge as judge_mod
from . import link as link_mod
from . import tracker as tracker_mod
from .embeddings import EmbeddingBackend
from .seeds import load_seeds, upsert_events

log = logging.getLogger("migyeol.pipeline")

STAGES = ("discover", "collect", "link", "extract", "judge", "tracker", "all")
RUN_LOCK = threading.Lock()


class PipelineBusy(RuntimeError):
    pass


class Pipeline:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        client: BigKindsClient,
        llm: LLMClient,
        embedder: EmbeddingBackend,
        today: date | None = None,
    ):
        self.session = session
        self.settings = settings
        self.client = client
        self.llm = llm
        self.embedder = embedder
        self.today = today or settings.today()
        self.call_budget = {"remaining": settings.llm_max_calls_per_run}

    # ------------------------------------------------------------------
    def ensure_events(self) -> list[Event]:
        seeds = load_seeds(self.settings.seeds_file)
        return upsert_events(self.session, seeds)

    def events(self, event_id: int | None = None) -> list[Event]:
        evs = self.ensure_events()
        if event_id is not None:
            evs = [e for e in evs if e.id == event_id]
            if not evs:
                raise ValueError(f"event {event_id} 가 활성 시드에 없다")
        return evs

    # ------------------------------------------------------------------
    def run(self, stage: str, event_id: int | None = None, dates: list[date] | None = None) -> PipelineRun:
        """dates 는 discover 의 조회 날짜. 비우면 현재 시드의 발생일을 쓴다."""
        if stage not in STAGES:
            raise ValueError(f"unknown stage {stage}")
        if not RUN_LOCK.acquire(blocking=False):
            raise PipelineBusy("파이프라인이 이미 실행 중이다")
        try:
            return self._run_locked(stage, event_id, dates)
        finally:
            RUN_LOCK.release()

    def _run_locked(self, stage: str, event_id: int | None, dates: list[date] | None) -> PipelineRun:
        run = PipelineRun(stage=stage, mode=f"{self.client.mode}/{self.llm.mode}/{self.embedder.name}", event_id=event_id, started_at=datetime.now())
        self.session.add(run)
        self.session.commit()
        try:
            stats = self._dispatch(stage, event_id, dates)
            run.ok = True
            run.stats = stats
        except Exception as exc:  # 기록 후 재전파 (API 응답에는 요약만)
            self.session.rollback()
            run.ok = False
            run.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-1500:]}"
            log.exception("pipeline %s failed", stage)
        run.finished_at = datetime.now()
        self.session.add(run)
        self.session.commit()
        if not run.ok:
            raise RuntimeError((run.error or "").splitlines()[0])
        return run

    def _per_event(self, label: str, evs: list[Event], fn, stats: dict[str, Any]) -> list[dict]:
        out = []
        for e in evs:
            try:
                out.append(fn(e))
            except Exception as exc:  # noqa: BLE001 — 사건 단위 격리
                self.session.rollback()
                log.exception("%s failed for %s", label, e.key)
                stats.setdefault("failures", []).append({"stage": label, "event": e.key, "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
        return out

    def _dispatch(self, stage: str, event_id: int | None, dates: list[date] | None = None) -> dict[str, Any]:
        if stage == "discover":
            if not dates:
                dates = sorted({s.occurred_at for s in load_seeds(self.settings.seeds_file)})
            if not dates:
                # 빈 초안으로 기존 초안을 덮어쓰거나 조용히 성공하지 않는다
                raise ValueError("discover 할 날짜가 없다: 날짜 범위(--from/--until)를 주거나 시드에 occurred_at 이 있어야 한다")
            cands = discover_mod.discover(self.client, dates)
            draft = discover_mod.write_seed_draft(cands, self.settings.seeds_file.with_name("events.draft.yaml"))
            return {"dates": [d.isoformat() for d in dates], "candidates": len(cands), "draft": str(draft), "topics": [c.topic for c in cands]}
        if stage == "tracker":
            return tracker_mod.check_changes(
                self.session, self.client, self._tracker_from(), settings=self.settings, today=self.today, llm=self.llm, call_budget=self.call_budget
            )

        all_events = self.ensure_events()
        all_facilities = [e.facility for e in all_events]
        evs = self.events(event_id)
        stats: dict[str, Any] = {"events": len(evs)}
        if stage in ("collect", "all"):
            stats["collect"] = self._per_event(
                "collect", evs, lambda e: collect_mod.collect_event(self.session, self.client, e, self.settings, self.embedder, self.today), stats
            )
        if stage in ("link", "all"):
            stats["link"] = link_mod.link_all(self.session, evs, self.settings, self.embedder, all_facilities=all_facilities)
        if stage in ("extract", "all"):
            stats["extract"] = self._per_event(
                "extract",
                evs,
                lambda e: extract_mod.extract_event(self.session, self.client, e, self.llm, self.settings, self.embedder, self.call_budget),
                stats,
            )
            # 약속 행동 키워드로 후속 기사를 추가 수집하고 다시 연결한다 (다른 표현을 쓰는 후속 기사 대비)
            stats["promise_followups"] = self._per_event(
                "promise_followups",
                evs,
                lambda e: collect_mod.collect_promise_followups(self.session, self.client, e, self.settings, self.embedder, self.today),
                stats,
            )
            stats["relink"] = link_mod.link_all(self.session, evs, self.settings, self.embedder, all_facilities=all_facilities)
        if stage in ("judge", "all"):
            stats["judge"] = self._per_event(
                "judge",
                evs,
                lambda e: judge_mod.judge_event(self.session, self.client, e, self.llm, self.settings, self.today, self.call_budget),
                stats,
            )
        if stage == "all" and event_id is None:
            stats["tracker"] = tracker_mod.check_changes(
                self.session, self.client, self._tracker_from(), settings=self.settings, today=self.today, llm=self.llm, call_budget=self.call_budget
            )
        stats["llm_calls_remaining"] = self.call_budget["remaining"]
        stats["summary"] = summarize(stats)
        return stats

    def _tracker_from(self) -> datetime:
        return datetime.combine(self.today, datetime.min.time().replace(hour=5))


def summarize(stats: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {"events": stats.get("events", 0)}
    if "collect" in stats:
        out["articles"] = sum(s.get("fetched", 0) for s in stats["collect"])
    ls = stats.get("relink") if "relink" in stats else stats.get("link")
    if ls is not None:
        out["links"] = sum(s.get("linked", 0) for s in ls)
        out["review"] = sum(s.get("review", 0) for s in ls)
    if "extract" in stats:
        out["promises"] = sum(s.get("promises", 0) for s in stats["extract"])
    if "judge" in stats:
        out["signals"] = sum(s.get("llm_calls", 0) for s in stats["judge"])
        out["followups"] = sum(s.get("followups", 0) for s in stats["judge"])
    if "failures" in stats:
        out["failures"] = len(stats["failures"])
    llm_failed = sum(s.get("llm_failed", 0) for s in stats.get("extract", []) + stats.get("judge", []))
    if llm_failed:
        out["llm_failed"] = llm_failed
    return out
