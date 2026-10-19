"""파이프라인 오케스트레이터. 단계별 실행 가능, 모든 실행은 pipeline_runs 에 기록한다.

- 사건 단위 try/except: 한 사건의 API·LLM 실패가 다른 사건을 막지 않는다 (stats["failures"] 에 남긴다).
- 프로세스 전역 Lock + 파일 Lock(data/pipeline.lock): 스케줄러·API·CLI 가 동시에 실행하지 않는다 (API 는 409).
  파일 Lock 은 같은 호스트의 다른 프로세스(CLI 와 서버, uvicorn --workers N)까지 막는다.
- 실행당 LLM 호출 상한(llm_max_calls_per_run)을 넘으면 나머지는 다음 실행으로 미룬다 (사건에 분류 대기 건수를 남긴다).
- 오류 기록(pipeline_runs.error, failures)은 비밀값(접근 키)을 가린 뒤 저장한다.
"""

from __future__ import annotations

import fcntl
import logging
import threading
import traceback
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..bigkinds.protocol import BigKindsClient
from ..config import Settings
from ..db.models import Event, PipelineRun, _now
from ..llm.client import LLMClient
from ..redact import mask
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


def _lock_path() -> Path:
    from ..config import BACKEND_DIR
    from ..db.session import db_path

    p = db_path()
    base = p.parent if p is not None and p.name != ":memory:" else BACKEND_DIR / "data"  # :memory:·비 sqlite 도 실행 위치가 아니라 backend/data 에
    base.mkdir(parents=True, exist_ok=True)
    return base / "pipeline.lock"


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
        fh = None
        try:
            try:
                fh = open(_lock_path(), "a+")
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if isinstance(exc, BlockingIOError):
                    raise PipelineBusy("파이프라인이 다른 프로세스에서 실행 중이다") from exc
                log.warning("파일 Lock 을 잡지 못했다(%s) — 프로세스 Lock 만으로 진행", exc)
            return self._run_locked(stage, event_id, dates)
        finally:
            if fh is not None:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                finally:
                    fh.close()
            RUN_LOCK.release()

    def _run_locked(self, stage: str, event_id: int | None, dates: list[date] | None) -> PipelineRun:
        run = PipelineRun(stage=stage, mode=f"{self.client.mode}/{self.llm.mode}/{self.embedder.name}", event_id=event_id, started_at=_now())
        self.session.add(run)
        self.session.commit()
        try:
            stats = self._dispatch(stage, event_id, dates)
            run.ok = True
            run.stats = stats
            run.error = None  # 다른 프로세스가 기동하며 '중단' 으로 닫았다가 실제로는 끝난 경우를 덮는다
        except Exception as exc:  # 기록 후 재전파 (API 응답에는 요약만)
            self.session.rollback()
            run.ok = False
            run.error = mask(f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-1500:]}")
            log.error("pipeline %s failed: %s", stage, run.error.splitlines()[0])
        run.finished_at = _now()
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
                err = mask(f"{type(exc).__name__}: {exc}")
                log.error("%s failed for %s: %s", label, e.key, err[:300])
                stats.setdefault("failures", []).append({"stage": label, "event": e.key, "error": err[:200]})
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
            stats = {"events": 0}
            self._tracker(stats, isolate=False)  # tracker 만 실행했으면 실패는 실행 실패다 (all 안에서는 격리)
            stats["summary"] = summarize(stats)
            return stats

        all_events = self.ensure_events()
        all_facilities = [e.facility for e in all_events]
        evs = self.events(event_id)
        stats: dict[str, Any] = {"events": len(evs)}
        if stage in ("collect", "all"):
            stats["collect"] = self._per_event(
                "collect", evs, lambda e: collect_mod.collect_event(self.session, self.client, e, self.settings, self.embedder, self.today), stats
            )
            for c in stats["collect"]:
                for w in c.get("warnings", []):
                    stats.setdefault("warnings", []).append({"stage": "collect", "event": c.get("event"), "warning": w})
        if stage in ("link", "all"):
            stats["link"] = link_mod.link_all(self.session, evs, self.settings, self.embedder, all_facilities=all_facilities, all_events=all_events)
            # 연결이 바뀌면 근거·약속(근거 기사가 활성 연결이어야 추적)도 바뀐다 — LLM 없이 규칙 판정만 다시.
            # 판정 전 사건(judge 미실행)은 그대로 두고, 사건 간 배타 처리로 연결이 바뀐 다른 사건도 포함한다.
            stats["rejudge"] = self._rejudge_after_link(stats["link"], evs if stage == "link" else [], all_events, stats)
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
            stats["relink"] = link_mod.link_all(self.session, evs, self.settings, self.embedder, all_facilities=all_facilities, all_events=all_events)
            # 단일 사건 실행의 relink 로 다른 사건이 강등되면 그 사건도 재판정한다 (이 실행의 judge 는 evs 만 다룬다)
            stats["rejudge_others"] = self._rejudge_after_link(stats["relink"], [], all_events, stats, exclude=evs)
        if stage in ("judge", "all"):
            stats["judge"] = self._per_event(
                "judge",
                evs,
                lambda e: judge_mod.judge_event(self.session, self.client, e, self.llm, self.settings, self.today, self.call_budget),
                stats,
            )
        if stage == "all" and event_id is None:
            self._tracker(stats)
        stats["llm_calls_remaining"] = self.call_budget["remaining"]
        stats["summary"] = summarize(stats)
        return stats

    def _rejudge_after_link(self, link_stats: list[dict], direct: list[Event], all_events: list[Event], stats: dict[str, Any], exclude: list[Event] | None = None) -> list[dict]:
        touched_ids = set(link_stats[0].get("multi_event_events", [])) if link_stats else set()
        skip = {e.id for e in (exclude or [])}
        targets = [e for e in all_events if (e in direct or e.id in touched_ids) and e.id not in skip and judge_mod.is_judged(self.session, e)]
        return self._per_event(
            "rejudge", targets, lambda e: {"event": e.key, "status": judge_mod.rejudge_event(self.session, e, self.settings, self.today).status}, stats
        )

    def _tracker(self, stats: dict[str, Any], isolate: bool = True) -> None:
        """변경이력 확인. all 안에서는 실패해도 전체를 깨지 않는다 (failures 에 남긴다). 단독 실행이면 실패를 올린다."""
        try:
            stats["tracker"] = tracker_mod.check_changes(
                self.session, self.client, self._tracker_from(), settings=self.settings, today=self.today, llm=self.llm, call_budget=self.call_budget
            )
        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            if not isolate:
                raise
            err = mask(f"{type(exc).__name__}: {exc}")
            log.error("tracker failed: %s", err[:300])
            stats.setdefault("failures", []).append({"stage": "tracker", "event": "*", "error": err[:200]})

    def _tracker_from(self):
        from datetime import datetime

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
