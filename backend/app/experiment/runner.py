"""§4 실험 오케스트레이터 — discover → seed → collect → link → followup → report.

- 빅카인즈 호출은 CountingClient 로 감싸 엔드포인트별로 센다(녹화 재생 포함 '요청 수'와 실제 네트워크 호출 수를 따로).
- 단계별 비용은 work_dir/state.json 에 누적해 report 단계가 전체 비용을 낸다.
- collect/link/followup 은 기존 Pipeline 을 자동 시드 파일로 돌린다(게이트·임계값 그대로, 규칙 어댑터면 LLM 호출 상한 해제).
- 원천 기사는 사건의 기준 기사다: 수집 후보에 넣고, 연결 게이트를 통과하지 못하면 '원천 기사' 사유로 확정 연결한다(리포트에 건수).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from ..bigkinds.codes import MVP_INCIDENT_PREFIXES
from ..bigkinds.protocol import DEFAULT_NEWS_FIELDS, BigKindsError
from ..config import BACKEND_DIR, KST, Settings
from ..db.models import Article, Event, EventArticle, ExperimentRun
from ..redact import mask
from . import autoseed, labels, metrics, report, sources

log = logging.getLogger("migyeol.experiment")

STAGES = ("discover", "seed", "collect", "link", "followup", "report", "all")
BUDGET = 5000  # §4.7 예상 호출 수
_ENDPOINT_OF = {
    "search_news": "search_news", "get_news_by_ids": "search_news", "search_quotation": "search_quotation", "issue_ranking": "issue_ranking",
    "time_line": "time_line", "word_cloud": "word_cloud", "change_tracker": "change_tracker",
}


class CountingClient:
    """BigKindsClient 를 감싸 엔드포인트별 요청 수(녹화 재생 포함)를 센다. 스레드 몇 개가 함께 불러도 된다."""

    def __init__(self, inner):
        self.inner = inner
        self.mode = inner.mode
        self.calls: dict[str, int] = {}
        self._lock = threading.Lock()

    def __getattr__(self, name: str):
        attr = getattr(self.inner, name)
        ep = _ENDPOINT_OF.get(name)
        if ep is None or not callable(attr):
            return attr

        def wrapped(*args, **kwargs):
            with self._lock:
                self.calls[ep] = self.calls.get(ep, 0) + 1
            return attr(*args, **kwargs)

        return wrapped


@dataclass
class ExperimentConfig:
    start: date
    until: date
    today: date
    prefixes: tuple[str, ...] = MVP_INCIDENT_PREFIXES
    max_pages: int = sources.MAX_PAGES
    issue: bool = True
    issue_stride: int = 1
    issue_followup_cap: int = 300
    workers: int = 4  # 원천 수집 동시 요청 수 (시작 간격은 BIGKINDS_RPS 가 지킨다)
    query_hit_limit: int = 400  # 시드 질의의 전체 기간 기사 수 상한 (넘으면 좁힌다, 0 이면 보정 안 함)
    max_articles_per_event: int = 600  # 실험 수집 총상한 — 보정된 질의는 collect 의 '한 번에 받기' 경로로 끝난다
    max_events: int = 150
    min_unique: int = 100
    work_dir: Path = field(default_factory=lambda: BACKEND_DIR / "data" / "experiment")
    draft_path: Path = field(default_factory=lambda: BACKEND_DIR / "seeds" / "experiment.draft.yaml")
    seeds_path: Path | None = None  # --seeds: 사람이 고친 시드로 collect 이후를 돌린다
    only_keys: tuple[str, ...] = ()  # --events: collect 를 이 사건들만 (시드 발생일·발생 창 질의를 고친 사건만 다시 받을 때)
    gold_dir: Path = field(default_factory=lambda: BACKEND_DIR / "gold")
    reports_dir: Path | None = None
    embedding_note: str = ""

    @property
    def active_seeds(self) -> Path:
        return self.seeds_path or self.draft_path

    def as_dict(self) -> dict[str, Any]:
        return {
            "from": self.start.isoformat(), "until": self.until.isoformat(), "today": self.today.isoformat(), "incident_prefixes": list(self.prefixes),
            "max_pages": self.max_pages, "issue_ranking": self.issue, "issue_stride_days": self.issue_stride, "max_events": self.max_events,
            "seeds_file": str(self.active_seeds.relative_to(BACKEND_DIR) if self.active_seeds.is_relative_to(BACKEND_DIR) else self.active_seeds),
        }


def prefixes_from(settings: Settings) -> tuple[str, ...]:
    raw = [p.strip() for p in (settings.incident_prefixes or "").split(",") if p.strip()]
    return tuple(p for p in raw if not p.startswith("범죄")) or MVP_INCIDENT_PREFIXES


class Experiment:
    def __init__(self, session: Session, settings: Settings, client, llm, embedder, cfg: ExperimentConfig):
        self.session = session
        self.cfg = cfg
        self.client = client if isinstance(client, CountingClient) else CountingClient(client)
        self.llm = llm
        self.embedder = embedder
        # 기존 파이프라인 설정: 시드 파일 = 자동 시드(또는 --seeds), 기준일 고정, 규칙 어댑터면 LLM 호출 상한 해제(외부 호출 없음)
        upd: dict[str, Any] = {"seeds_file": cfg.active_seeds, "today_override": cfg.today}
        upd["max_articles_per_event"] = max(settings.max_articles_per_event, cfg.max_articles_per_event)
        upd["collect_initial_cap"] = max(settings.collect_initial_cap, cfg.max_articles_per_event // 2)  # 발생 직후 2주도 날짜별로 쪼개지 않고 받게
        if getattr(llm, "mode", "") == "rules":
            upd["llm_max_calls_per_run"] = 1_000_000
        self.settings = settings.model_copy(update=upd)
        self.cfg.work_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ state
    @property
    def _state_path(self) -> Path:
        return self.cfg.work_dir / "state.json"

    def _load_state(self) -> dict[str, Any]:
        try:
            return json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _recordings(self) -> int:
        d = self.settings.fixtures_dir / "bigkinds" / "recordings"
        return sum(1 for _ in d.rglob("*.json")) if d.exists() else 0

    def _begin(self) -> float:
        self.client.calls = {}
        self._rec_before = self._recordings()
        return time.monotonic()

    def _save_stage(self, stage: str, started: float, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        state = self._load_state()
        calls = dict(self.client.calls)
        # 네트워크 호출 = record 모드에서 이번 단계에 새로 생긴 녹화 파일 수 (재생은 0), live 는 요청 수 전부
        network = self._recordings() - getattr(self, "_rec_before", 0) if self.client.mode == "record" else (sum(calls.values()) if self.client.mode == "live" else 0)
        entry = {
            "finished_at": datetime.now(KST).isoformat(timespec="seconds"),
            "elapsed_sec": round(time.monotonic() - started, 1),
            "api_calls": calls,
            "network_calls": int(network),
            **(extra or {}),
        }
        state.setdefault("stages", {})[stage] = entry
        self._state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        self.client.calls = {}
        return entry

    # ------------------------------------------------------------ stages
    def run(self, stage: str) -> dict[str, Any]:
        if stage not in STAGES:
            raise ValueError(stage)
        order = ["discover", "seed", "collect", "link", "followup", "report"] if stage == "all" else [stage]
        if self.cfg.seeds_path is not None:
            order = [s for s in order if s not in ("discover", "seed")]  # 사람 확정 시드로는 수집 이후만
        out = {}
        for s in order:
            log.info("experiment stage %s", s)
            out[s] = getattr(self, f"stage_{s}")()
        return out

    def stage_discover(self) -> dict[str, Any]:
        t0 = self._begin()
        c = self.cfg
        res = sources.discover_quotation_path(self.client, c.start, c.until, c.prefixes, c.max_pages, workers=c.workers)
        if c.issue:
            iss = sources.discover_issue_path(self.client, c.start, c.until, c.prefixes, c.issue_stride, workers=c.workers)
            res.issues = iss.issues
            res.stats["issue"] = iss.stats
            groups, unmatched = autoseed.build_groups(res)
            # 인용문 경로에 없던 이슈 사건 — 시설·지역이 특정되는 것부터 토픽 핵심어로 인용문을 한 번 더 찾는다
            unmatched.sort(key=lambda i: (0 if i.facility else 1 if i.regions else 2, i.date))
            st: dict[str, Any] = {}
            extra = sources.issue_followups(self.client, unmatched[: c.issue_followup_cap], c.prefixes, c.until, st, workers=c.workers)
            known = {(q.news_id, q.quotation[:60]) for q in res.quotes}
            extra = [q for q in extra if (q.news_id, q.quotation[:60]) not in known]
            extra = sources.attach_details(self.client, extra, st)
            res.quotes.extend(extra)
            res.stats["issue"].update(unmatched=len(unmatched), followup_searched=min(len(unmatched), c.issue_followup_cap), followup_quotes=len(extra), followup_detail_dropped=st.get("detail_dropped"))
        res.stats["source_quotes"] = len(res.quotes)
        res.stats["source_articles"] = len({q.news_id for q in res.quotes})
        res.save(c.work_dir / "discover.json")
        entry = self._save_stage("discover", t0, {"stats": {k: v for k, v in res.stats.items() if k != "truncated_windows"}, "truncated_windows": len(res.stats.get("truncated_windows", []))})
        return entry

    def stage_seed(self) -> dict[str, Any]:
        t0 = self._begin()
        res = sources.DiscoverResult.load(self.cfg.work_dir / "discover.json")
        groups, _ = autoseed.build_groups(res)
        autoseed.save_groups(groups, self.cfg.work_dir / "groups.json")
        # 질의 보정: 후보를 넉넉히(1.5배) 골라 건수를 본 뒤, 상한 안으로 좁혀진 사건만으로 다시 층별 선택한다
        pool_n = int(self.cfg.max_events * 1.5) if self.cfg.query_hit_limit > 0 else self.cfg.max_events
        pre = autoseed.select_groups(groups, pool_n)
        pre_seeds = {id(g): autoseed.seed_dict(g, 0) for g in pre}
        cal: dict[str, Any] = {}
        if self.cfg.query_hit_limit > 0:
            autoseed.calibrate_queries(self.client, list(pre_seeds.values()), self.cfg.today, self.cfg.query_hit_limit, cal)
            ok = [g for g in pre if (pre_seeds[id(g)].get("_query_hits") or 0) <= self.cfg.query_hit_limit]
            cal["pool"], cal["within_limit"] = len(pre), len(ok)
            picked = autoseed.select_groups(ok, self.cfg.max_events)
            if len(picked) < self.cfg.max_events:  # 모자라면 상한을 넘는 사건으로 채운다 (수집 호출이 늘어난다)
                rest = [g for g in sorted(pre, key=lambda g: pre_seeds[id(g)].get("_query_hits") or 0) if g not in picked]
                picked = sorted(picked + rest[: self.cfg.max_events - len(picked)], key=lambda g: g.occurred)
        else:
            picked = pre
        header = {
            "generated_at": datetime.now(KST).isoformat(timespec="seconds"), "from": self.cfg.start.isoformat(), "until": self.cfg.until.isoformat(),
            "source_quotes": len(res.quotes), "source_articles_collected": len({q.news_id for q in res.quotes}), "groups": len(groups), "selected": len(picked),
        }
        seeds = []
        for i, g in enumerate(picked, 1):
            sd = autoseed.seed_dict(g, i)
            pre_sd = pre_seeds.get(id(g)) or {}
            sd["query"], sd["_query_hits"] = pre_sd.get("query", sd["query"]), pre_sd.get("_query_hits")
            seeds.append(sd)
        header["query_calibration"] = {"limit": self.cfg.query_hit_limit, **cal}
        seeds = autoseed.write_draft(picked, self.cfg.draft_path, header, seeds)
        if len(picked) < self.cfg.min_unique:
            log.warning("유니크 원천 기사 %d건 — 목표 %d건 미달", len(picked), self.cfg.min_unique)
        return self._save_stage("seed", t0, {"collected": header["source_articles_collected"], "groups": len(groups), "unique": len(seeds), "query_calibration": header["query_calibration"]})

    def _pipeline(self):
        from ..pipeline.runner import Pipeline

        return Pipeline(self.session, self.settings, self.client, self.llm, self.embedder, today=self.cfg.today)

    def _draft_meta(self) -> dict[str, dict[str, Any]]:
        with Path(self.cfg.active_seeds).open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return {str(e["key"]): e for e in data.get("events", []) or []}

    def _events(self) -> list[Event]:
        keys = set(self._draft_meta())
        return [e for e in self.session.query(Event).filter(Event.active.is_(True)).all() if e.key in keys]

    def ensure_source_articles(self) -> dict[str, int]:
        """원천 기사를 기사 테이블·수집 후보에 넣는다 (상세 조회 1회/100건)."""
        from ..pipeline.collect import add_candidate, upsert_article

        evs = [e for e in self._events() if e.source_news_id]
        need = [e.source_news_id for e in evs if self.session.get(Article, e.source_news_id) is None]
        fetched = 0
        for i in range(0, len(need), 100):
            try:
                docs = self.client.get_news_by_ids(need[i : i + 100], fields=list(DEFAULT_NEWS_FIELDS)).documents
            except BigKindsError as exc:
                log.warning("원천 기사 상세 조회 실패: %s", mask(str(exc))[:200])
                continue
            for d in docs:
                upsert_article(self.session, d, self.embedder)
                fetched += 1
        added = 0
        for e in evs:
            if self.session.get(Article, e.source_news_id) is not None:
                added += int(add_candidate(self.session, e, e.source_news_id, "seed"))
        self.session.commit()
        return {"source_fetched": fetched, "source_candidates_added": added}

    def _apply_meta(self) -> None:
        meta = self._draft_meta()
        for e in self._events():
            m = meta.get(e.key, {})
            if m.get("_dead") is not None:
                e.dead = int(m["_dead"])
            if m.get("_injured") is not None:
                e.injured = int(m["_injured"])
        self.session.commit()

    def pin_source_links(self) -> dict[str, int]:
        """원천 기사가 게이트·임계값으로 연결되지 않았으면 확정 연결로 붙인다 (사건의 정의 기사)."""
        auto = forced = 0
        for e in self._events():
            if not e.source_news_id or self.session.get(Article, e.source_news_id) is None:
                continue
            link = next((l for l in e.links if l.news_id == e.source_news_id), None)
            if link is not None and link.is_active:
                auto += 1
                continue
            if link is None:
                link = EventArticle(event_id=e.id, news_id=e.source_news_id, phase="대응발표")
                e.links.append(link)
            link.confirmed, link.needs_review, link.rejected = True, False, False
            link.link_method = link.link_method or "rule"
            link.link_reason = ((link.link_reason or "") + "+source-article").lstrip("+")
            link.phase = "대응발표"
            forced += 1
        from ..pipeline.review import recompute_needs_review

        for e in self._events():
            recompute_needs_review(self.session, e)
        self.session.commit()
        return {"source_linked_auto": auto, "source_linked_pinned": forced}

    def stage_collect(self) -> dict[str, Any]:
        t0 = self._begin()
        if self.cfg.only_keys:
            pipe = self._pipeline()
            ids = {e.key: e.id for e in pipe.ensure_events()}
            missing = [k for k in self.cfg.only_keys if k not in ids]
            if missing:
                raise ValueError(f"시드에 없는 사건: {missing}")
            runs = [pipe.run("collect", event_id=ids[k]) for k in self.cfg.only_keys]
            summary = [c for r in runs for c in r.stats.get("collect", [])]
            failures = sum(len(r.stats.get("failures", [])) for r in runs)
            extra = self.ensure_source_articles()
            self._apply_meta()
            # 전체 collect 기록(비용)은 덮지 않고 따로 남긴다 — 리포트 비용 표의 collect_events 행
            return self._save_stage("collect_events", t0, {"only": list(self.cfg.only_keys), "per_event": [
                {k: c.get(k) for k in ("event", "fetched", "new", "total_hits", "api_calls", "occurrence")} for c in summary
            ], "failures": failures, **extra})
        run = self._pipeline().run("collect")
        extra = self.ensure_source_articles()
        self._apply_meta()
        return self._save_stage("collect", t0, {"summary": run.stats.get("summary"), "failures": len(run.stats.get("failures", [])), **extra})

    def stage_link(self) -> dict[str, Any]:
        t0 = self._begin()
        self.ensure_source_articles()
        run = self._pipeline().run("link")
        extra = self.pin_source_links()
        return self._save_stage("link", t0, {"summary": run.stats.get("summary"), **extra})

    def stage_followup(self) -> dict[str, Any]:
        """extract(인용문 → 약속) → 약속 키워드 재검색(호출 예산판, experiment/followups.py) → 재연결 → judge.
        파이프라인의 extract 단계는 약속마다 넓은 재검색을 하므로 실험은 같은 함수들을 직접 순서대로 부른다."""
        from ..pipeline import extract as extract_mod
        from ..pipeline import link as link_mod
        from .followups import promise_followups

        t0 = self._begin()
        pipe = self._pipeline()
        all_events = pipe.ensure_events()
        evs = [e for e in all_events if e.key in set(self._draft_meta())]
        cleared = self._clear_rules_outputs(evs)
        failures: list[dict[str, str]] = []
        ex_stats, pf_stats = [], []
        for e in evs:
            try:
                ex_stats.append(extract_mod.extract_event(self.session, self.client, e, self.llm, self.settings, self.embedder, pipe.call_budget))
            except Exception as exc:  # noqa: BLE001 — 사건 단위 격리
                self.session.rollback()
                failures.append({"stage": "extract", "event": e.key, "error": mask(f"{type(exc).__name__}: {exc}")[:200]})
        src_stats = self.ensure_source_promises(evs)
        for e in evs:
            try:
                pf_stats.append(promise_followups(self.session, self.client, e, self.settings, self.embedder, self.cfg.today, self.cfg.query_hit_limit or 400))
            except Exception as exc:  # noqa: BLE001
                self.session.rollback()
                failures.append({"stage": "promise_followups", "event": e.key, "error": mask(f"{type(exc).__name__}: {exc}")[:200]})
        relink = link_mod.link_all(self.session, all_events, self.settings, self.embedder, all_facilities=[e.facility for e in all_events], all_events=all_events)
        extra = self.pin_source_links()
        jd = pipe.run("judge")
        return self._save_stage(
            "followup", t0,
            {
                "extract": {"events": len(ex_stats), "quotes": sum(x.get("linked_quotes", 0) for x in ex_stats), "public": sum(x.get("public", 0) for x in ex_stats), "promises": sum(x.get("promises", 0) for x in ex_stats)},
                "promise_followups": {"events": len(pf_stats), "searched": sum(1 for x in pf_stats if not x["skipped"]), "skipped": sum(1 for x in pf_stats if x["skipped"]), "fetched": sum(x["fetched"] for x in pf_stats)},
                "relink": {"links": sum(x.get("linked", 0) for x in relink), "review": sum(x.get("review", 0) for x in relink)},
                "source_promises": src_stats,
                "rules_outputs_cleared": cleared, "judge": jd.stats.get("summary"), "failures": len(failures) + len(jd.stats.get("failures", [])), "failure_samples": failures[:5], **extra,
            },
        )

    def ensure_source_promises(self, evs: list[Event]) -> dict[str, int]:
        """원천 기사(대응 발언)의 인용문은 반드시 약속 추출을 받게 한다.
        인용문 검색 창(발생 +90일) 밖이거나 이번 검색에 다시 나오지 않으면 extract_event 가 건너뛰므로,
        원천 기사의 인용문이 없으면 시드의 source_quotation 으로 만들고, 추출 결과가 없는 인용문은 여기서 추출한다."""
        from ..db.models import Quotation
        from ..pipeline.extract import _merge_duplicates, store_promise
        from ..llm.schemas import PromiseInput

        out = {"events": 0, "created_quotes": 0, "extracted": 0, "trackable": 0}
        for e in evs:
            src = self.session.get(Article, e.source_news_id) if e.source_news_id else None
            if src is None:
                continue
            out["events"] += 1
            qs = [q for q in e.quotations if q.news_id == src.news_id]
            if not qs and e.source_quotation:
                q = Quotation(event_id=e.id, news_id=src.news_id, source=e.responsible_org or "", quotation=e.source_quotation,
                              published_at=src.published_at, provider=src.provider, is_public_actor=True)
                e.quotations.append(q)
                qs = [q]
                out["created_quotes"] += 1
            lexicon = [e.region, e.responsible_org, *(e.org_aliases or [])]
            for q in qs:
                if any(p.model == self.llm.model for p in q.promises):
                    continue
                inp = PromiseInput(event_summary=e.summary or e.title, responsible_org=e.responsible_org, source=q.source, quotation=q.quotation,
                                   article_title=src.title or "", published_at=(q.published_at or src.published_at or e.occurred_at).isoformat())
                res = self.llm.extract_promise(inp, key=f"{q.news_id}||{q.source}||{q.quotation}")
                store_promise(e, q, res, self.llm, lexicon)
                out["extracted"] += 1
            out["trackable"] += sum(1 for q in qs for p in q.promises if p.model == self.llm.model and p.is_trackable)
            self.session.flush()
            self.session.refresh(e)
            _merge_duplicates(e, self.embedder)
        self.session.commit()
        return out

    def _clear_rules_outputs(self, evs: list[Event]) -> dict[str, int]:
        """규칙 어댑터 산출물(약속·후속 신호)은 결정적이고 공짜라 followup 마다 다시 만든다 — 규칙을 고친 뒤 재실행이 옛 결과를 캐시로 쓰지 않게.
        LLM(live) 산출물은 지우지 않는다."""
        from ..db.models import FollowupSignal, Promise

        if getattr(self.llm, "mode", "") != "rules":
            return {"signals": 0, "promises": 0}
        ids = [e.id for e in evs]
        # 이전 규칙 버전(rules-v1 …) 산출물도 지운다 — 버전이 바뀐 뒤 옛 약속·신호가 섞이지 않게
        n_sig = self.session.query(FollowupSignal).filter(FollowupSignal.event_id.in_(ids), FollowupSignal.model.like("rules-%")).delete(synchronize_session=False)
        n_pro = self.session.query(Promise).filter(Promise.event_id.in_(ids), Promise.model.like("rules-%")).delete(synchronize_session=False)
        self.session.commit()
        self.session.expire_all()
        return {"signals": int(n_sig), "promises": int(n_pro)}

    def stage_report(self) -> dict[str, Any]:
        t0 = self._begin()
        facts = metrics.collect_facts(self.session, self.settings, keys=set(self._draft_meta()))
        self._refresh_casualties(facts)
        g = self.cfg.gold_dir
        n_lab = labels.write_experiment_labels(self.session, facts, g / "experiment_labels.csv")
        n_cl = labels.write_cluster_sample(self.session, facts, g / "cluster_sample.csv")
        org_rows = labels.write_org_sample(self.session, facts, g / "org_sample.csv")
        lab = labels.label_metrics(g)
        self._save_stage("report", t0)
        state = self._load_state()
        latest = self.latest_json(facts, state, lab)
        reports_dir = self.cfg.reports_dir or self.settings.reports_dir
        stamp = datetime.now(KST).strftime("%Y%m%d_%H%M")
        md_path = reports_dir / f"experiment_{stamp}.md"
        reports_dir.mkdir(parents=True, exist_ok=True)
        md_path.write_text(report.render(latest, facts, state, lab, org_rows, self.cfg, self.settings, self.session), encoding="utf-8")
        latest_path = reports_dir / "experiment_latest.json"
        tmp = latest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(latest, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(latest_path)
        self.session.add(ExperimentRun(run_id=stamp, stage="report", config=self.cfg.as_dict(), metrics=latest, report_path=str(md_path), ok=True))
        self.session.commit()
        return {"report": str(md_path), "latest": str(latest_path), "labels": n_lab, "cluster_sample": n_cl, "org_sample": len(org_rows), "events": latest["events"]["total"], "with_followup": latest["events"]["with_followup"]}

    def _refresh_casualties(self, facts: list[metrics.EventFacts]) -> None:
        """피해 규모 = 원천 기사 그룹(같은 사건의 원천 기사 제목·본문 200자)의 정규식 최댓값. 없으면 null (추정 금지).
        연결 기사 제목까지 보면 '올해 산재 사망 457명' 같은 통계가 섞여 쓰지 않는다."""
        meta = self._draft_meta()
        for f in facts:
            m = meta.get(f.event.key, {})
            f.event.dead, f.event.injured = m.get("_dead"), m.get("_injured")
        self.session.commit()

    # ------------------------------------------------------------ json
    def latest_json(self, facts: list[metrics.EventFacts], state: dict[str, Any], lab: dict[str, Any]) -> dict[str, Any]:
        out = metrics.empty_latest()
        stages = state.get("stages", {})
        seed = stages.get("seed", {})
        meta = self._draft_meta()
        unique = len(meta)
        collected = seed.get("collected", unique)
        now = datetime.now(KST)
        seeds_file = self.cfg.active_seeds
        out.update(
            generated_at=now.isoformat(timespec="seconds"),
            as_of=self.cfg.today.isoformat(),
            bigkinds_mode=self.client.mode,
            seeds_file=str(seeds_file.relative_to(BACKEND_DIR)) if seeds_file.is_relative_to(BACKEND_DIR) else str(seeds_file),
            embedding_backend=getattr(self.embedder, "name", "").split(":")[0] or self.settings.embedding_backend,
            llm_mode=getattr(self.llm, "mode", self.settings.llm_mode),
            source_articles={"collected": int(collected), "unique": unique},
        )
        out.update(metrics.compute(facts, as_of=self.cfg.today, start=self.cfg.start, until=self.cfg.until))
        out["labels"] = {k: v for k, v in lab.items() if not k.startswith("_")}
        calls: dict[str, int] = {}
        elapsed = 0.0
        for st in stages.values():
            elapsed += float(st.get("elapsed_sec", 0) or 0)
            for k, v in (st.get("api_calls") or {}).items():
                calls[k] = calls.get(k, 0) + int(v)
        base = out["cost"]["api_calls"]
        for k in list(base):
            base[k] = calls.get(k, 0)
        out["cost"]["elapsed_sec"] = round(elapsed, 1)
        rec_dir = self.settings.fixtures_dir / "bigkinds" / "recordings"
        out["cost"]["recordings"] = sum(1 for _ in rec_dir.rglob("*.json")) if rec_dir.exists() else 0
        wf = out["events"]["with_followup"]
        out["success"].update(met_min=wf >= out["success"]["min_followup_events"], met_target=wf >= out["success"]["target_followup_events"])
        return out
