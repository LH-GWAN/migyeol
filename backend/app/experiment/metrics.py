"""§4.6 정량 지표 — DB 에서 계산해 experiment_latest.json 스키마(키 고정)로 낸다.

후속보도 있음(§4.4) = 유효 신호(완료·진행·새로운문제, matches_promise, 신뢰도 ≥ SIGNAL_MIN_CONF)가 1건 이상인 사건.
유효 신호는 rejudge_event 와 같은 집합(현재 약속 집합 해시로 분류된 신호, 활성 연결 후속 기사)만 센다 — 대시보드(트랙 B)와 같은 기준.
경과 창(30/90/180/365일)은 첫 유효 신호 기사의 발행일이 발생일 + N일 안에 있는 사건 수(누적).
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from ..config import FOLLOWUP_STATUSES, STATUSES, Settings
from ..db.models import Article, Event, FollowupSignal
from ..llm.client import PROMPT_VERSIONS
from ..pipeline.judge import followup_articles, promise_set_hash, trackable_promises, signal_in_scope
from ..rules.orgs import canonical_org
from ..rules.status import POSITIVE_SIGNALS

WINDOWS = (30, 90, 180, 365)
UNKNOWN_ORG = "기관 미상"
SIGNAL_KEYS = ("완료", "진행", "새로운문제")


@dataclass
class EventFacts:
    event: Event
    org: str
    linked: list[Article]
    followups: list[Article]
    valid: list[tuple[FollowupSignal, Article]] = field(default_factory=list)

    @property
    def has_followup(self) -> bool:
        # 상태 분포와 같은 기준 — 유효 신호가 있어도 판단 불가·후속보도 부족이면 세지 않는다 (신호 수 집계는 valid 그대로)
        return self.event.status in FOLLOWUP_STATUSES and bool(self.valid)

    @property
    def first_valid(self) -> tuple[FollowupSignal, Article] | None:
        return min(self.valid, key=lambda t: (t[1].published_at, t[0].id)) if self.valid else None

    @property
    def latest_valid_at(self) -> date | None:
        return max((a.published_at for _, a in self.valid if a.published_at), default=None)


def valid_signals(session: Session, event: Event, settings: Settings, followups: list[Article]) -> list[tuple[FollowupSignal, Article]]:
    by_news = {a.news_id: a for a in followups}
    promises = trackable_promises(event)
    pset = promise_set_hash(promises)
    version = PROMPT_VERSIONS["followup"]
    out = []
    for s in sorted(event.signals, key=lambda s: s.id):
        if s.prompt_version != version or s.news_id not in by_news or s.promise_set_hash != pset or not signal_in_scope(s, promises, settings):
            continue
        if s.matches_promise and s.confidence >= settings.signal_min_conf and s.signal in POSITIVE_SIGNALS:
            out.append((s, by_news[s.news_id]))
    return out


def collect_facts(session: Session, settings: Settings, keys: set[str] | None = None) -> list[EventFacts]:
    q = session.query(Event).filter(Event.active.is_(True))
    out = []
    for ev in q.order_by(Event.occurred_at, Event.id).all():
        if keys is not None and ev.key not in keys:
            continue
        linked = [l.article for l in ev.links if l.is_active and l.article is not None]
        fups = followup_articles(session, ev, settings)
        out.append(EventFacts(event=ev, org=canonical_org(ev.responsible_org) or UNKNOWN_ORG, linked=linked, followups=fups, valid=valid_signals(session, ev, settings, fups)))
    return out


def _rate(n: int, d: int) -> float:
    return round(n / d, 3) if d else 0.0


def _month(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _month_range(start: date, end: date) -> list[str]:
    out, cur = [], date(start.year, start.month, 1)
    while cur <= end:
        out.append(_month(cur))
        cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
    return out


def _sum_or_none(vals: list[int | None]) -> int | None:
    v = [x for x in vals if x is not None]
    return sum(v) if v else None


def compute(facts: list[EventFacts], *, as_of: date, start: date, until: date) -> dict[str, Any]:
    """experiment_latest.json 의 events / windows_days / signals / by_* / timeseries / daily 부분."""
    total = len(facts)
    with_f = [f for f in facts if f.has_followup]
    by_status = {s: 0 for s in STATUSES}
    for f in facts:
        if f.event.status in by_status:
            by_status[f.event.status] += 1
    n_links = [len(f.linked) for f in facts]
    windows = {}
    for w in WINDOWS:
        windows[str(w)] = sum(1 for f in with_f if f.first_valid and f.first_valid[1].published_at <= f.event.occurred_at + timedelta(days=w))
    signals = {k: 0 for k in SIGNAL_KEYS}
    for f in facts:
        for s, _ in f.valid:
            if s.signal in signals:
                signals[s.signal] += 1

    inc: dict[str, list[EventFacts]] = defaultdict(list)
    for f in facts:
        inc[f.event.incident_type or "(미분류)"].append(f)
    by_incident = sorted(
        ({"incident_type": k, "events": len(v), "with_followup": sum(1 for f in v if f.has_followup), "rate": _rate(sum(1 for f in v if f.has_followup), len(v))} for k, v in inc.items()),
        key=lambda r: (-r["events"], r["incident_type"]),
    )

    orgs: dict[str, list[EventFacts]] = defaultdict(list)
    for f in facts:
        orgs[f.org].append(f)
    by_org = []
    for org, v in orgs.items():
        wf = [f for f in v if f.has_followup]
        latest = max((f.latest_valid_at for f in wf if f.latest_valid_at), default=None)
        by_org.append({"org": org, "events": len(v), "with_followup": len(wf), "rate": _rate(len(wf), len(v)), "latest_followup_at": latest.isoformat() if latest else None, "event_ids": [f.event.id for f in v]})
    # 후속보도 확인 건수 → 비율 → 사건 수 내림차순 (§6 화면 0: 후속보도가 확인된 기관을 위에)
    by_org.sort(key=lambda r: (-r["with_followup"], -r["rate"], -r["events"], r["org"]))

    end = max(as_of, until)
    months = {m: {"bucket": m, "occurred": 0, "followup_articles": 0, "_dead": [], "_injured": []} for m in _month_range(start, end)}
    daily: dict[str, dict[str, Any]] = {}
    for f in facts:
        m = _month(f.event.occurred_at)
        if m in months:
            months[m]["occurred"] += 1
            months[m]["_dead"].append(f.event.dead)
            months[m]["_injured"].append(f.event.injured)
        dk = f.event.occurred_at.isoformat()
        daily.setdefault(dk, {"date": dk, "occurred": 0, "followup_articles": 0})["occurred"] += 1
        for a in f.followups:
            if a.published_at is None:
                continue
            m = _month(a.published_at)
            if m in months:
                months[m]["followup_articles"] += 1
            dk = a.published_at.isoformat()
            daily.setdefault(dk, {"date": dk, "occurred": 0, "followup_articles": 0})["followup_articles"] += 1
    timeseries = []
    for m in sorted(months):
        r = months[m]
        timeseries.append({"bucket": m, "occurred": r["occurred"], "followup_articles": r["followup_articles"], "dead": _sum_or_none(r["_dead"]), "injured": _sum_or_none(r["_injured"])})

    return {
        "events": {
            "total": total,
            "with_followup": len(with_f),
            "followup_rate": _rate(len(with_f), total),
            "by_status": by_status,
            "with_org_notice": sum(1 for f in facts if getattr(f.event, "org_notice_found", False)),
            "articles_per_event": {"median": statistics.median(n_links) if n_links else 0, "max": max(n_links) if n_links else 0},
        },
        "windows_days": windows,
        "signals": signals,
        "by_incident_type": by_incident,
        "by_org": by_org,
        "timeseries": timeseries,
        "daily": [daily[k] for k in sorted(daily)],
    }


def empty_latest() -> dict[str, Any]:
    """§4.6 스키마의 빈 틀 — 키는 항상 남긴다."""
    return {
        "generated_at": None, "as_of": None, "bigkinds_mode": None, "seeds_file": None, "embedding_backend": None, "llm_mode": None,
        "source_articles": {"collected": 0, "unique": 0},
        "events": {"total": 0, "with_followup": 0, "followup_rate": 0.0, "by_status": {s: 0 for s in STATUSES}, "with_org_notice": 0, "articles_per_event": {"median": 0, "max": 0}},
        "windows_days": {str(w): 0 for w in WINDOWS},
        "signals": {k: 0 for k in SIGNAL_KEYS},
        "by_incident_type": [], "by_org": [], "timeseries": [], "daily": [],
        "labels": {"labeled": 0, "followup_precision": None, "followup_recall": None, "cluster_precision": None, "org_accuracy": None},
        "cost": {"api_calls": {"search_news": 0, "search_quotation": 0, "issue_ranking": 0, "time_line": 0, "word_cloud": 0}, "elapsed_sec": 0, "recordings": 0},
        "success": {"min_followup_events": 5, "target_followup_events": 20, "met_min": False, "met_target": False},
    }


def incident_counter(facts: list[EventFacts]) -> Counter:
    return Counter(f.event.incident_type for f in facts)
