"""라벨 기준 전후 비교·스윕 (scripts/evaluate.py --labels / --label-sweep).

라벨: gold/cluster_sample.csv(사건–기사 쌍, 사람_OK) · gold/experiment_labels.csv(사건별 사람_후속보도).
  - 군집 정밀도 = 라벨 행 중 **지금도 자동 연결(rule/embedding, 검토 대기·해제 아님)인 행**의 O 비율.
    표본은 바꾸기 전 자동 연결에서 뽑았으므로 '연결을 지워서 좋아졌는가' 를 잰다. 새로 붙은 연결은 라벨이 없어 세지 않는다
    (O 유지율 = 남은 O / 전체 O, X 제거율 = 빠진 X / 전체 X 를 함께 낸다).
  - 후속보도 precision/recall = 시스템 '있음'(유효 신호 ≥1) 대 사람_후속보도.

스윕은 DB 를 바꾸지 않는다: 후보마다 게이트·강한 규칙·임베딩 코사인·weighted_overlap 을 한 번 계산해 두고,
설정 조합마다 link.decide 로 자동 연결을 다시 정한 뒤, 그 연결로 후속 기사를 고르고 규칙 어댑터로 다시 분류한다.
근사: 사건 간 배타 처리(multi-event)·약속 재추출·약속 키워드 재검색은 다시 하지 않는다 (현재 DB 의 약속을 쓴다).
"""

from __future__ import annotations

import csv
import itertools
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from ..config import Settings
from ..db.models import Article, Event, EventArticle, EventCandidate
from ..llm.schemas import FollowupInput, PromiseRef
from ..pipeline.collect import article_text
from ..pipeline.embeddings import cosine, from_bytes, needs_recompute
from ..pipeline.link import decide, gate, is_strong
from ..pipeline.seeds import seed_from_event
from ..rules import weights as W
from ..rules.status import POSITIVE_SIGNALS


def _ox(v: str | None) -> bool | None:
    s = (v or "").strip().upper()
    return True if s == "O" else False if s == "X" else None


def load_labels(gold_dir: Path) -> tuple[dict[tuple[str, str], bool], dict[str, bool]]:
    cl: dict[tuple[str, str], bool] = {}
    with (gold_dir / "cluster_sample.csv").open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            v = _ox(r.get("사람_OK(O/X)"))
            if v is not None:
                cl[(r["사건키"], r["news_id"])] = v
    fu: dict[str, bool] = {}
    with (gold_dir / "experiment_labels.csv").open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            v = _ox(r.get("사람_후속보도(O/X)"))
            if v is not None:
                fu[r["사건키"]] = v
    return cl, fu


def cluster_scores(kept: set[tuple[str, str]], cl: dict[tuple[str, str], bool]) -> dict[str, Any]:
    n_o = sum(cl.values())
    n_x = len(cl) - n_o
    k = [v for key, v in cl.items() if key in kept]
    return {
        "cluster_rows": len(cl),
        "cluster_kept": len(k),
        "cluster_precision": round(sum(k) / len(k), 3) if k else None,
        "o_retained": round(sum(k) / n_o, 3) if n_o else None,
        "x_removed": round((n_x - (len(k) - sum(k))) / n_x, 3) if n_x else None,
    }


def followup_scores(system: dict[str, bool], fu: dict[str, bool]) -> dict[str, Any]:
    keys = [k for k in fu if k in system]
    tp = sum(1 for k in keys if system[k] and fu[k])
    fp = sum(1 for k in keys if system[k] and not fu[k])
    fn = sum(1 for k in keys if not system[k] and fu[k])
    p = round(tp / (tp + fp), 3) if tp + fp else None
    r = round(tp / (tp + fn), 3) if tp + fn else None
    f1 = round(2 * p * r / (p + r), 3) if p and r else None
    return {"system_positive": tp + fp, "tp": tp, "fp": fp, "fn": fn, "followup_precision": p, "followup_recall": r, "followup_f1": f1}


def snapshot(session: Session, settings: Settings, gold_dir: Path) -> dict[str, Any]:
    """현재 DB 상태 그대로의 라벨 지표 (실행 전후 비교용)."""
    from .metrics import collect_facts

    cl, fu = load_labels(gold_dir)
    keys = {k for k, _ in cl} | set(fu)
    evs = {e.id: e.key for e in session.query(Event).filter(Event.key.in_(keys)).all()}
    kept = {
        (evs[l.event_id], l.news_id)
        for l in session.query(EventArticle).filter(EventArticle.event_id.in_(list(evs))).all()
        if l.is_active and l.link_method in ("rule", "embedding") and not l.confirmed
    }
    facts = collect_facts(session, settings, keys=set(fu))
    system = {f.event.key: f.has_followup for f in facts}
    return {**cluster_scores(kept, cl), **followup_scores(system, fu)}


# ------------------------------------------------------------------ 스윕
@dataclass
class Cand:
    news_id: str
    published: date | None
    strong: bool
    other_hit: bool
    cos: float
    overlap: float
    gate_ok: bool


@dataclass
class EventCase:
    event: Event
    pinned: set[str]  # 관리자 확정·수동·원천 기사 고정 연결 (스윕과 무관하게 활성)
    cands: list[Cand] = field(default_factory=list)


def link_features(session: Session, settings: Settings, embedder, keys: set[str]) -> list[EventCase]:
    """후보마다 게이트·강한 규칙·코사인·weighted_overlap — 현재 DB 의 Event.term_weights 를 쓴다(link 단계가 갱신)."""
    events = session.query(Event).filter(Event.active.is_(True)).all()
    facilities = [e.facility for e in events]
    out = []
    for ev in [e for e in events if e.key in keys]:
        seed_vec = np.asarray(embedder.encode([seed_from_event(ev).seed_text()])[0])
        others = [f for f in facilities if f and f != ev.facility]
        rows = session.query(EventCandidate).filter_by(event_id=ev.id).all()
        promise_ids = {c.news_id for c in rows if c.source == "promise"}
        ids = {c.news_id for c in rows} | {l.news_id for l in ev.links}
        pinned = {l.news_id for l in ev.links if l.confirmed or l.link_method == "manual"}
        case = EventCase(event=ev, pinned=pinned)
        for a in session.query(Article).filter(Article.news_id.in_(ids)).all() if ids else []:
            if a.news_id in pinned:
                continue
            ok, reasons = gate(ev, a)
            if not ok:
                case.cands.append(Cand(a.news_id, a.published_at, False, False, 0.0, 0.0, False))
                continue
            if needs_recompute(a.embedding, a.embedding_backend, embedder):
                vec = np.asarray(embedder.encode([article_text(a)])[0])
            else:
                vec = from_bytes(a.embedding)
            text = article_text(a)
            case.cands.append(Cand(
                a.news_id, a.published_at, is_strong(reasons, promise_candidate=a.news_id in promise_ids),
                any(f in text for f in others) and "facility" not in reasons, cosine(vec, seed_vec), W.weighted_overlap(ev.term_weights, text), True,
            ))
        out.append(case)
    return out


def simulate_links(cases: list[EventCase], settings: Settings) -> dict[str, set[str]]:
    """사건 key → 활성 연결 news_id (자동 연결 + 고정 연결)."""
    out = {}
    for c in cases:
        act = set(c.pinned)
        for x in c.cands:
            if x.gate_ok and decide(x.strong, x.other_hit, x.cos, x.overlap, settings)[0] == "link":
                act.add(x.news_id)
        out[c.event.key] = act
    return out


def auto_links(cases: list[EventCase], active: dict[str, set[str]]) -> set[tuple[str, str]]:
    return {(c.event.key, n) for c in cases for n in active[c.event.key] if n not in c.pinned}


class FollowupSim:
    """시뮬레이션된 연결로 후속 기사를 고르고 규칙 어댑터로 분류한다. 분류는 (사건, 기사, 약속 집합, 문장 설정)으로 캐시."""

    def __init__(self, session: Session, settings: Settings, llm):
        self.session, self.settings, self.llm = session, settings, llm
        self.cache: dict[tuple, bool] = {}
        self._articles: dict[str, Article] = {}
        self._quotes: dict[int, dict[str, list[str]]] = {}

    def _article(self, nid: str) -> Article | None:
        if nid not in self._articles:
            self._articles[nid] = self.session.get(Article, nid)
        return self._articles[nid]

    def has_followup(self, ev: Event, active: set[str], s: Settings) -> bool:
        promises = sorted(
            [p for p in ev.promises if p.is_trackable and (p.evidence_news_id in active or any(n in active for n in (p.merged_news_ids or [])))],
            key=lambda p: p.id,
        )
        if not promises:
            return False
        cutoff = ev.occurred_at + timedelta(days=s.followup_grace_days)
        dates = [a.published_at for p in promises if (a := self._article(p.evidence_news_id)) is not None and a.published_at]
        if dates:
            cutoff = max(cutoff, min(dates))
        refs = [PromiseRef(id=p.id, actor_org=p.actor_org, action=p.action, deadline_date=p.deadline_date.isoformat() if p.deadline_date else None) for p in promises]
        pkey = tuple(p.id for p in promises)
        if ev.id not in self._quotes:
            q: dict[str, list[str]] = {}
            for x in ev.quotations:
                q.setdefault(x.news_id, []).append(f"{x.source}: {x.quotation}")
            self._quotes[ev.id] = q
        self.llm.settings = s
        for nid in active:
            a = self._article(nid)
            if a is None or a.published_at is None or a.published_at <= cutoff or a.change_status == "cancelled":
                continue
            ck = (ev.key, nid, pkey, s.followup_top_sentences, s.followup_sentence_min, s.signal_min_conf, bool(ev.term_weights))
            if ck not in self.cache:
                inp = FollowupInput(
                    event_summary=ev.summary or ev.title, promises=refs, article_title=a.title or "", hilight=a.hilight or a.content_200 or "",
                    quotations=self._quotes[ev.id].get(nid, []), published_at=a.published_at.isoformat(), term_weights=ev.term_weights or {},
                )
                from ..llm.client import validate_span

                r = validate_span(self.llm.classify_followup(inp), inp)
                self.cache[ck] = bool(r.matches_promise and r.promise_id is not None and r.confidence >= s.signal_min_conf and r.signal in POSITIVE_SIGNALS)
            if self.cache[ck]:
                return True
        return False


def sweep(
    session: Session,
    settings: Settings,
    embedder,
    llm,
    gold_dir: Path,
    grid: dict[str, list[float]],
    with_followup: bool = True,
) -> list[dict[str, Any]]:
    cl, fu = load_labels(gold_dir)
    keys = {k for k, _ in cl} | set(fu)
    cases = link_features(session, settings, embedder, keys)
    sim = FollowupSim(session, settings, llm)
    names = list(grid)
    rows = []
    for combo in itertools.product(*(grid[n] for n in names)):
        s = settings.model_copy(update=dict(zip(names, combo)))
        active = simulate_links(cases, s)
        row: dict[str, Any] = {n: v for n, v in zip(names, combo)}
        row["auto_links"] = len(auto_links(cases, active))
        row.update(cluster_scores(auto_links(cases, active), cl))
        if with_followup:
            system = {c.event.key: sim.has_followup(c.event, active[c.event.key], s) for c in cases if c.event.key in fu}
            row.update(followup_scores(system, fu))
        rows.append(row)
    return rows


def render_table(rows: list[dict[str, Any]], cols: list[str]) -> str:
    head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
    body = "".join("| " + " | ".join("-" if r.get(c) is None else str(r.get(c)) for c in cols) + " |\n" for r in rows)
    return head + body
