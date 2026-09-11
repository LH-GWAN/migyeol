"""파이프라인 오케스트레이터. 단계별 실행 가능, 모든 실행은 pipeline_runs 에 기록한다."""

from __future__ import annotations

import logging
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

    # ------------------------------------------------------------------
    def ensure_events(self) -> list[Event]:
        seeds = load_seeds(self.settings.seeds_file)
        return upsert_events(self.session, seeds)

    def events(self, event_id: int | None = None) -> list[Event]:
        evs = self.ensure_events()
        if event_id is not None:
            evs = [e for e in evs if e.id == event_id]
        return evs

    # ------------------------------------------------------------------
    def run(self, stage: str, event_id: int | None = None) -> PipelineRun:
        if stage not in STAGES:
            raise ValueError(f"unknown stage {stage}")
        run = PipelineRun(stage=stage, mode=f"{self.client.mode}/{self.llm.mode}/{self.embedder.name}", started_at=datetime.now())
        self.session.add(run)
        self.session.commit()
        try:
            stats = self._dispatch(stage, event_id)
            run.ok = True
            run.stats = stats
        except Exception as exc:  # 기록 후 재전파
            self.session.rollback()
            run.ok = False
            run.error = f"{exc}\n{traceback.format_exc()[-1500:]}"
            log.exception("pipeline %s failed", stage)
        run.finished_at = datetime.now()
        self.session.add(run)
        self.session.commit()
        if not run.ok:
            raise RuntimeError(run.error)
        return run

    def _dispatch(self, stage: str, event_id: int | None) -> dict[str, Any]:
        if stage == "discover":
            seeds = load_seeds(self.settings.seeds_file)
            dates = sorted({s.occurred_at for s in seeds})
            cands = discover_mod.discover(self.client, dates)
            draft = discover_mod.write_seed_draft(cands, self.settings.seeds_file.with_name("events.draft.yaml"))
            return {"dates": [d.isoformat() for d in dates], "candidates": len(cands), "draft": str(draft), "topics": [c.topic for c in cands]}
        if stage == "tracker":
            return tracker_mod.check_changes(self.session, self.client, datetime.combine(self.today, datetime.min.time().replace(hour=5)))

        evs = self.events(event_id)
        stats: dict[str, Any] = {"events": len(evs)}
        if stage in ("collect", "all"):
            stats["collect"] = [collect_mod.collect_event(self.session, self.client, e, self.settings, self.embedder, self.today) for e in evs]
        if stage in ("link", "all"):
            stats["link"] = link_mod.link_all(self.session, evs, self.settings, self.embedder)
        if stage in ("extract", "all"):
            stats["extract"] = [extract_mod.extract_event(self.session, self.client, e, self.llm, self.settings, self.embedder) for e in evs]
            # 약속 행동 키워드로 후속 기사를 추가 수집하고 다시 연결한다 (다른 표현을 쓰는 후속 기사 대비)
            stats["promise_followups"] = [
                collect_mod.collect_promise_followups(self.session, self.client, e, self.settings, self.embedder, self.today) for e in evs
            ]
            stats["relink"] = link_mod.link_all(self.session, evs, self.settings, self.embedder)
        if stage in ("judge", "all"):
            stats["judge"] = [judge_mod.judge_event(self.session, self.client, e, self.llm, self.settings, self.today) for e in evs]
        if stage == "all":
            stats["tracker"] = tracker_mod.check_changes(self.session, self.client, datetime.combine(self.today, datetime.min.time().replace(hour=5)))
        stats["summary"] = summarize(stats)
        return stats


def summarize(stats: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {"events": stats.get("events", 0)}
    if "collect" in stats:
        out["articles"] = sum(s["fetched"] for s in stats["collect"])
    if "relink" in stats or "link" in stats:
        ls = stats.get("relink") or stats["link"]
        out["links"] = sum(s["linked"] for s in ls)
        out["review"] = sum(s["review"] for s in ls)
    if "extract" in stats:
        out["promises"] = sum(s["promises"] for s in stats["extract"])
    if "judge" in stats:
        out["signals"] = sum(s["llm_calls"] for s in stats["judge"])
        out["followups"] = sum(s["followups"] for s in stats["judge"])
    return out
