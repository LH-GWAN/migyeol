"""v2 §7.1 대시보드 집계 API (트랙 B). 읽기 전용 — 모델을 바꾸지 않는다.

집계 규칙 (목록 화면 숫자와 어긋나지 않게 judge·serializers 의 함수를 그대로 쓴다):
- 사건 = Event.active=true.
- 후속 기사 = judge.followup_articles (활성 연결, 해제·검토 대기·삭제 제외, FOLLOWUP_CUTOFF 기준일 뒤, 재순위 확률 바닥) — 카드의 followup_count 와 같다.
- 유효 신호 = 현재 prompt_version · 현재 추적 약속 집합 해시로 분류된 후속 기사 신호 중 rules.status.is_valid
  (일치 + 신뢰도 ≥ SIGNAL_MIN_CONF + 완료·진행·새로운문제) — rejudge_event 가 판정에 쓰는 집합과 같다.
- "후속보도 확인" 사건 = 상태가 FOLLOWUP_STATUSES(완료·진행·새로운 문제)이고 유효 신호 ≥ 1 (2026-10-30 — 상태 분포와 합이 맞게).
- 기한 경과 = 카드의 deadline_passed_days 가 있고 상태가 '조치 완료 근거 확인' 이 아님.
- 기관 키 = rules.orgs.canonical_org(event.responsible_org), 비면 "기관 미상".
- 실험 리포트(reports/experiment_latest.json, §4.6)는 있으면 그대로 내려 주고, 없으면 404 / summary.experiment=null.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..config import DISCLAIMER, FOLLOWUP_STATUSES, STATUS_DONE, STATUSES, Settings, get_settings
from ..db.models import Article, Event, EventArticle, FollowupSignal, PipelineRun, Promise, Report
from ..db.session import get_session, session_factory
from ..llm.client import PROMPT_VERSIONS
from ..pipeline.judge import followup_articles, overdue_promise, promise_set_hash, trackable_promises
from ..rules.orgs import canonical_org
from ..rules.status import SignalIn, Thresholds, is_valid
from . import serializers as ser

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

UNKNOWN_ORG = "기관 미상"
MAX_DAY_BUCKETS = 1500  # 일별 구간 상한 (약 4년)
# 실험 JSON 에서 summary 에 넣지 않는 큰 목록 (전체는 /experiment 로)
_EXPERIMENT_LISTS = ("by_incident_type", "by_org", "timeseries", "daily")


# ---------------------------------------------------------------------------
# 사건별 사실 (한 번 계산해 모든 집계가 같이 쓴다)
# ---------------------------------------------------------------------------


@dataclass
class EventFacts:
    event: Event
    org: str
    followups: list[Article]
    confirmed: list[tuple[FollowupSignal, Article]] = field(default_factory=list)  # 유효 신호와 그 기사 — 후속보도 확인 상태인 사건만
    overdue: bool = False

    @property
    def has_followup(self) -> bool:
        return bool(self.confirmed)

    @property
    def latest_confirmed_at(self) -> date | None:
        dates = [a.published_at for _, a in self.confirmed if a.published_at]
        return max(dates) if dates else None


def _thresholds(settings: Settings) -> Thresholds:
    return Thresholds(settings.signal_min_conf, settings.completion_min_conf, settings.stale_followup_days)


def confirmed_signals(session: Session, event: Event, settings: Settings, followups: list[Article] | None = None) -> list[tuple[FollowupSignal, Article]]:
    """rejudge_event 와 같은 '현재 유효 신호' 집합 — 판정 화면과 대시보드가 같은 근거를 센다."""
    if followups is None:
        followups = followup_articles(session, event, settings)
    by_news = {a.news_id: a for a in followups}
    pset = promise_set_hash(trackable_promises(event))
    version = PROMPT_VERSIONS["followup"]
    th = _thresholds(settings)
    out = []
    for s in sorted(event.signals, key=lambda s: s.id):
        if s.prompt_version != version or s.news_id not in by_news or s.promise_set_hash != pset:
            continue
        a = by_news[s.news_id]
        sig_in = SignalIn(id=s.id, news_id=s.news_id, signal=s.signal, matches_promise=s.matches_promise, confidence=s.confidence, published_at=a.published_at, promise_id=s.promise_id, evidence_span=s.evidence_span)
        if is_valid(sig_in, th):
            out.append((s, a))
    return out


def event_org(event: Event) -> str:
    return canonical_org(event.responsible_org) or UNKNOWN_ORG


def _data_stamp(session: Session) -> tuple:
    """집계 입력이 바뀌었는지 싸게 보는 도장 — 사건·연결·약속·신호·신고·게시판 건수와 마지막 파이프라인 실행."""
    from ..notices.models import OrgNotice

    from sqlalchemy import func

    last = session.query(PipelineRun.id, PipelineRun.finished_at).order_by(PipelineRun.id.desc()).first()
    counts = tuple(session.query(m).count() for m in (Event, EventArticle, Promise, FollowupSignal, Report, OrgNotice))
    # 건수가 같아도 바뀌는 것: 연결 플래그(확정·해제·검토), 사건 상태·후속 집계, 신고 처리 — 몇 ms 짜리 집계 질의
    link_flags = tuple(
        session.query(
            func.sum(EventArticle.rejected), func.sum(EventArticle.needs_review), func.sum(EventArticle.confirmed)
        ).one()
    )
    events = tuple(
        session.query(Event.id, Event.status, Event.followup_count, Event.last_followup_at, Event.org_notice_found)
        .filter(Event.active.is_(True))
        .order_by(Event.id)
        .all()
    )
    reports_open = session.query(Report).filter(Report.resolved.is_(False)).count()
    return (tuple(last) if last else None, counts, link_flags, events, reports_open)


_FACTS_LOCK = threading.Lock()
_FACTS_CACHE: dict = {}  # database_url -> {"key": (today, stamp), "at": time, "facts": [...], "session": Session}
FACTS_TTL_SEC = 60.0


def collect_facts(session: Session, settings: Settings | None = None, today: date | None = None) -> list[EventFacts]:
    """대시보드 사실 목록. 사건 150건 기준 1초 남짓이라 한 화면이 엔드포인트 6개를 동시에 부르면 GIL 경합으로 20초를 넘긴다 —
    (기준일, 데이터 도장)이 같고 TTL(60초) 안이면 재사용하고, 계산은 Lock 으로 한 번만 한다. 도장은 몇 ms 짜리 집계 질의라 매번 본다.
    사실 목록의 ORM 객체는 캐시가 쥔 전용 세션에 붙어 있어 요청 세션이 닫혀도 읽을 수 있다."""
    settings = settings or get_settings()
    today = today or settings.today()
    url = settings.database_url
    now = time.monotonic()
    with _FACTS_LOCK:
        entry = _FACTS_CACHE.get(url)
        stamp = _data_stamp(session)
        if entry is not None and entry["key"] == (today, stamp) and now - entry["at"] < FACTS_TTL_SEC:
            return entry["facts"]
        own = session_factory()()
        facts = _collect_facts(own, settings, today)
        if entry is not None:
            entry["session"].close()
        _FACTS_CACHE[url] = {"key": (today, stamp), "at": now, "facts": facts, "session": own}
        return facts


def invalidate_facts_cache() -> None:
    with _FACTS_LOCK:
        for entry in _FACTS_CACHE.values():
            entry["session"].close()
        _FACTS_CACHE.clear()


def _collect_facts(session: Session, settings: Settings, today: date) -> list[EventFacts]:
    out = []
    for ev in session.query(Event).filter(Event.active.is_(True)).order_by(Event.id).all():
        fups = followup_articles(session, ev, settings)
        out.append(
            EventFacts(
                event=ev,
                org=event_org(ev),
                followups=fups,
                # 상태가 완료·진행·새로운 문제가 아니면 신호가 있어도 '후속보도 확인'으로 세지 않는다 — 집계·최근 피드·사례가 상태 분포와 맞게
                confirmed=confirmed_signals(session, ev, settings, fups) if ev.status in FOLLOWUP_STATUSES else [],
                overdue=overdue_promise(ev, today) is not None and ev.status != STATUS_DONE,
            )
        )
    return out


def _rate(num: int, den: int) -> float:
    return round(num / den, 3) if den else 0.0


# ---------------------------------------------------------------------------
# 실험 리포트
# ---------------------------------------------------------------------------


class ExperimentUnreadable(Exception):
    pass


def load_experiment(settings: Settings | None = None) -> dict | None:
    """experiment_latest.json 을 읽는다. 없으면 None, 깨졌으면 ExperimentUnreadable."""
    path = (settings or get_settings()).experiment_latest_path
    if not path or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ExperimentUnreadable(f"{path.name} 을 읽을 수 없음") from exc
    if not isinstance(data, dict):
        raise ExperimentUnreadable(f"{path.name} 형식이 객체가 아님")
    return data


def experiment_summary(data: dict | None) -> dict | None:
    """summary 용 요약 = 실험 JSON 에서 큰 목록(기관별·유형별·시계열)을 뺀 나머지. 키 이름은 §4.6 그대로."""
    if data is None:
        return None
    return {k: v for k, v in data.items() if k not in _EXPERIMENT_LISTS}


def _safe_experiment() -> dict | None:
    try:
        return load_experiment()
    except ExperimentUnreadable:
        return None


# ---------------------------------------------------------------------------
# 집계 함수 (API 와 scripts/export_dashboard.py 가 같이 쓴다)
# ---------------------------------------------------------------------------


def build_summary(session: Session, facts: list[EventFacts], settings: Settings, today: date, experiment: dict | None) -> dict:
    by_status = {s: 0 for s in STATUSES}
    for f in facts:
        by_status[f.event.status if f.event.status in STATUSES else STATUSES[-1]] += 1
    total = len(facts)
    with_followup = sum(1 for f in facts if f.has_followup)
    last = (
        session.query(PipelineRun)
        .filter(PipelineRun.ok.is_(True), PipelineRun.stage.in_(("all", "judge")), PipelineRun.event_id.is_(None))
        .order_by(PipelineRun.id.desc())
        .first()
    )
    return {
        "events_total": total,
        "with_followup": with_followup,
        "followup_rate": _rate(with_followup, total),
        "overdue": sum(1 for f in facts if f.overdue),
        "with_org_notice": sum(1 for f in facts if getattr(f.event, "org_notice_found", False)),
        "followup_articles": sum(len(f.followups) for f in facts),
        "orgs": len({f.org for f in facts}),
        "by_status": by_status,
        "last_run": ser._iso(last.finished_at) if last else None,
        "as_of": today.isoformat(),
        "bigkinds_mode": settings.bigkinds_mode,
        "experiment": experiment_summary(experiment),
        "disclaimer": DISCLAIMER,
    }


def _bucket(d: date, interval: str) -> str:
    return d.strftime("%Y-%m") if interval == "month" else d.isoformat()


def _bucket_range(start: date, end: date, interval: str) -> list[str]:
    out: list[str] = []
    if interval == "month":
        y, m = start.year, start.month
        while (y, m) <= (end.year, end.month):
            out.append(f"{y:04d}-{m:02d}")
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return out
    d = start
    while d <= end:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _sum_or_none(values: list[int | None]) -> int | None:
    vals = [v for v in values if v is not None]
    return sum(vals) if vals else None


def build_timeseries(facts: list[EventFacts], interval: str, start: date | None, end: date | None, today: date, experiment: dict | None) -> list[dict]:
    if start is None:
        occurred = [f.event.occurred_at for f in facts if f.event.occurred_at]
        start = min(occurred) if occurred else today.replace(day=1)
        if interval == "day" and (end or today) - start > timedelta(days=MAX_DAY_BUCKETS - 1):
            start = (end or today) - timedelta(days=MAX_DAY_BUCKETS - 1)
    end = end or today
    if start > end:
        raise HTTPException(status_code=422, detail="from 이 until 보다 늦다")
    if interval == "day" and (end - start).days + 1 > MAX_DAY_BUCKETS:
        raise HTTPException(status_code=422, detail=f"일별 구간은 최대 {MAX_DAY_BUCKETS}일")
    buckets = _bucket_range(start, end, interval)
    rows = {b: {"bucket": b, "occurred": 0, "followup_articles": 0, "confirmed_followups": 0, "_dead": [], "_injured": []} for b in buckets}
    for f in facts:
        ev = f.event
        b = _bucket(ev.occurred_at, interval) if ev.occurred_at else None
        if b in rows:
            rows[b]["occurred"] += 1
            rows[b]["_dead"].append(getattr(ev, "dead", None))
            rows[b]["_injured"].append(getattr(ev, "injured", None))
        for a in f.followups:
            b = _bucket(a.published_at, interval) if a.published_at else None
            if b in rows:
                rows[b]["followup_articles"] += 1
        for _, a in f.confirmed:
            b = _bucket(a.published_at, interval) if a.published_at else None
            if b in rows:
                rows[b]["confirmed_followups"] += 1
    # 피해 규모: 실험 JSON 의 월별 값이 있으면 그것을 쓴다 (추정 금지 — 없으면 null)
    exp_casualties: dict[str, tuple] = {}
    if experiment and interval == "month":
        for r in experiment.get("timeseries") or []:
            if isinstance(r, dict) and r.get("bucket"):
                exp_casualties[str(r["bucket"])] = (r.get("dead"), r.get("injured"))
    out = []
    for b in buckets:
        r = rows[b]
        dead, injured = _sum_or_none(r.pop("_dead")), _sum_or_none(r.pop("_injured"))
        if b in exp_casualties:
            dead, injured = exp_casualties[b]
        out.append({**r, "dead": dead, "injured": injured})
    return out


def incident_family(incident_type: str | None) -> str:
    """'재해>자연재해>홍수' → '재해>자연재해' (실험 리포트의 계열 단위)."""
    parts = [p for p in (incident_type or "").split(">") if p]
    return ">".join(parts[:2]) if parts else "분류 없음"


def build_by_incident(facts: list[EventFacts]) -> list[dict]:
    groups: dict[str, list[EventFacts]] = {}
    for f in facts:
        groups.setdefault(incident_family(f.event.incident_type), []).append(f)
    out = []
    for fam, fs in groups.items():
        n, k = len(fs), sum(1 for f in fs if f.has_followup)
        out.append(
            {
                "incident_type": fam,
                "label": fam.split(">")[-1],
                "events": n,
                "with_followup": k,
                "rate": _rate(k, n),
                "subtypes": sorted({f.event.incident_type for f in fs if f.event.incident_type}),
            }
        )
    out.sort(key=lambda r: (-r["events"], -r["with_followup"], r["incident_type"]))
    return out


OrgSort = Literal["followup", "rate", "events"]


def build_by_org(facts: list[EventFacts], sort: str = "followup") -> list[dict]:
    groups: dict[str, list[EventFacts]] = {}
    for f in facts:
        groups.setdefault(f.org, []).append(f)
    out = []
    for org, fs in groups.items():
        n, k = len(fs), sum(1 for f in fs if f.has_followup)
        confirmed_dates = [f.latest_confirmed_at for f in fs if f.latest_confirmed_at]
        last_article = [f.event.last_followup_at for f in fs if f.event.last_followup_at]
        out.append(
            {
                "org": org,
                "events": n,
                "with_followup": k,
                "rate": _rate(k, n),
                "latest_followup_at": max(confirmed_dates).isoformat() if confirmed_dates else None,
                "last_article_at": max(last_article).isoformat() if last_article else None,
                "confirmed_signals": sum(len(f.confirmed) for f in fs),
                "event_ids": [f.event.id for f in fs],
                "raw_names": sorted({f.event.responsible_org for f in fs if f.event.responsible_org}),
            }
        )
    keys = {
        "followup": lambda r: (-r["with_followup"], -r["rate"], -r["events"], r["org"]),
        "rate": lambda r: (-r["rate"], -r["with_followup"], -r["events"], r["org"]),
        "events": lambda r: (-r["events"], -r["with_followup"], -r["rate"], r["org"]),
    }
    out.sort(key=keys.get(sort, keys["followup"]))
    return out


def build_recent_followups(facts: list[EventFacts], limit: int) -> list[dict]:
    rows = []
    for f in facts:
        for s, a in f.confirmed:
            rows.append((a.published_at or date.min, s.id, f, s, a))
    rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
    return [
        {
            "event_id": f.event.id,
            "event_title": f.event.title,
            "event_status": f.event.status,
            "org": f.org,
            "signal": s.signal,
            "confidence": s.confidence,
            "evidence_span": s.evidence_span,
            "news_id": a.news_id,
            "title": a.title,
            "provider": a.provider,
            "published_at": ser._iso(a.published_at),
            "link": a.provider_link_page,
        }
        for _, _, f, s, a in rows[:limit]
    ]


# ---------------------------------------------------------------------------
# 기관 발표로 확인된 사례 (v3 — 2026-10-27 멘토 피드백: 실제 사례 2건을 있는 그대로)
# ---------------------------------------------------------------------------

CASE_KINDS = {
    "notice_without_followup": "후속보도는 없지만 기관 발표가 있는 사건",
    "notice_before_followup": "기관 발표 뒤 다음 후속보도까지 한참 걸린 사건",
}
CASE_MIN_GAP_DAYS = 30  # 기관 발표 → 후속보도 사이가 이 이상 벌어져야 '한참 뒤'로 센다


def _site_names() -> dict[str, str]:
    try:
        from ..notices.sites import load_sites

        return {s.key: s.name for s in load_sites()}
    except Exception:  # 등록부를 못 읽어도 사례 집계는 계속
        return {}


def _notice_out(n, site_names: dict[str, str]) -> dict:
    return {
        "title": n.title,
        "published_at": ser._iso(n.published_at),
        "url": n.url,
        "site_key": n.site_key,
        "site_name": site_names.get(n.site_key) or n.site_key,
        "site_url": n.site_url,
        "snippet": n.snippet,
        "match_score": n.match_score,
        "matched_terms": list(n.matched_terms or []),
    }


def _norm(t: str) -> str:
    return "".join(str(t or "").split())


def _facility_hit(facility: str | None, terms: list) -> bool:
    """매칭된 용어(가점 태그 제외)나 '+요약:' 태그가 사건 시설명과 겹치면 True. 시설명이 없는 사건은 통과."""
    fac = _norm(facility)
    if len(fac) < 2:
        return True
    for raw in terms or []:
        t = str(raw)
        if t.startswith("+요약:"):
            t = t.split(":", 1)[1]
        elif t.startswith("+"):
            continue
        tn = _norm(t)
        if len(tn) >= 2 and (tn in fac or fac in tn):
            return True
    return False


def build_notice_cases(session: Session, facts: list[EventFacts], per_kind: int = 1) -> list[dict]:
    """기관 공식 게시판에서 발표가 확인된 사건 가운데 두 종류의 사례를 고른다 (보조 근거, 상태는 그대로).

    - notice_without_followup: 발생일 이후의 기관 발표가 있는데, 그 발표일 이후로 확인된 후속보도(유효 신호)가 없는 사건.
      사건에 시설명이 있으면 발표 제목·요약이 시설명과 겹쳐야 한다(다른 곳의 같은 종류 사고 발표를 거른다).
      시설명 일치 → 매칭 점수 → 책임기관 자체 게시판 → 최근 발표 순.
    - notice_before_followup: 발생일 이후의 기관 발표로부터 30일 이상 지나서야 다음 후속보도(유효 신호)가 확인된 사건.
      발표→보도 간격이 긴 사건부터.
    """
    from ..notices.models import OrgNotice

    site_names = _site_names()
    by_event: dict[int, list] = {}
    for n in session.query(OrgNotice).order_by(OrgNotice.match_score.desc(), OrgNotice.published_at.desc()).all():
        by_event.setdefault(n.event_id, []).append(n)

    without: list[tuple[tuple, dict]] = []
    before: list[tuple[tuple, dict]] = []
    for f in facts:
        ev = f.event
        notices = [n for n in by_event.get(f.event.id, []) if n.published_at and (ev.occurred_at is None or n.published_at >= ev.occurred_at)]
        if not notices:
            continue
        confirmed_dates = sorted(a.published_at for _, a in f.confirmed if a.published_at)
        base = {
            "event_id": ev.id,
            "title": ev.title,
            "status": ev.status,
            "org": f.org,
            "incident_type": ev.incident_type,
            "occurred_at": ser._iso(ev.occurred_at),
            "last_article_at": ser._iso(ev.last_followup_at),
            "articles_after": len(f.followups),
            "latest_confirmed_at": ser._iso(f.latest_confirmed_at),
            "notices_count": len(notices),
        }
        # ① 발표 뒤 확인된 후속보도가 없는 사건
        cands = [n for n in notices if not any(d >= n.published_at for d in confirmed_dates) and _facility_hit(ev.facility, n.matched_terms)]
        if cands:
            cands.sort(key=lambda n: (-n.match_score, 0 if n.site_key.startswith("korea-") else -1, -n.published_at.toordinal()))
            n = cands[0]
            gap = (n.published_at - ev.occurred_at).days if ev.occurred_at else None
            without.append(
                ((-n.match_score, 0 if n.site_key.startswith("korea-") else -1, -n.published_at.toordinal()),
                 {**base, "kind": "notice_without_followup", "notice": _notice_out(n, site_names), "gap_days": gap, "followup_after_at": None})
            )
        # ② 발표 → 한참 뒤에야 다음 후속보도
        best = None
        for n in sorted(notices, key=lambda n: n.published_at):
            nxt = next((d for d in confirmed_dates if d >= n.published_at), None)
            if nxt is None:
                continue
            gap = (nxt - n.published_at).days
            if gap >= CASE_MIN_GAP_DAYS and (best is None or gap > best[0]):
                best = (gap, n, nxt)
        if best:
            gap, n, nxt = best
            before.append(((-gap, -n.match_score), {**base, "kind": "notice_before_followup", "notice": _notice_out(n, site_names), "gap_days": gap, "followup_after_at": ser._iso(nxt)}))
    without.sort(key=lambda r: r[0])
    before.sort(key=lambda r: r[0])
    out = [c for _, c in without[:per_kind]] + [c for _, c in before[:per_kind]]
    for c in out:
        c["kind_label"] = CASE_KINDS[c["kind"]]
    return out

# ---------------------------------------------------------------------------
# 엔드포인트
# ---------------------------------------------------------------------------


@router.get("/summary")
def summary(session: Session = Depends(get_session)):
    settings = get_settings()
    today = settings.today()
    return build_summary(session, collect_facts(session, settings, today), settings, today, _safe_experiment())


@router.get("/timeseries")
def timeseries(
    interval: Literal["month", "day"] = "month",
    from_: date | None = Query(default=None, alias="from"),
    until: date | None = None,
    session: Session = Depends(get_session),
):
    settings = get_settings()
    today = settings.today()
    return build_timeseries(collect_facts(session, settings, today), interval, from_, until, today, _safe_experiment())


@router.get("/by-incident")
def by_incident(session: Session = Depends(get_session)):
    return build_by_incident(collect_facts(session))


@router.get("/by-org")
def by_org(sort: OrgSort = "followup", session: Session = Depends(get_session)):
    return build_by_org(collect_facts(session), sort)


@router.get("/recent-followups")
def recent_followups(limit: int = Query(default=10, ge=1, le=50), session: Session = Depends(get_session)):
    return build_recent_followups(collect_facts(session), limit)


@router.get("/notice-cases")
def notice_cases(per_kind: int = Query(default=1, ge=1, le=5), session: Session = Depends(get_session)):
    """기관 발표로 확인된 사례 — 종류별 per_kind 건. 보조 근거이며 사건 상태(5종)를 바꾸지 않는다."""
    return {"items": build_notice_cases(session, collect_facts(session), per_kind), "kinds": CASE_KINDS, "disclaimer": DISCLAIMER}


@router.get("/experiment")
def experiment():
    try:
        data = load_experiment()
    except ExperimentUnreadable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if data is None:
        raise HTTPException(status_code=404, detail="experiment_latest.json 이 아직 없다 (scripts/experiment.py 실행 후 생성)")
    return data


def build_dashboard(session: Session, settings: Settings | None = None) -> dict:
    """export_dashboard.py 용 — 대시보드의 모든 집계를 한 번에."""
    settings = settings or get_settings()
    today = settings.today()
    facts = collect_facts(session, settings, today)
    exp = _safe_experiment()
    return {
        "generated_for": today.isoformat(),
        "summary": build_summary(session, facts, settings, today, exp),
        "daily": build_timeseries(facts, "day", None, None, today, exp),
        "monthly": build_timeseries(facts, "month", None, None, today, exp),
        "by_incident": build_by_incident(facts),
        "by_org": build_by_org(facts, "followup"),
        "recent_followups": build_recent_followups(facts, 50),
        "disclaimer": DISCLAIMER,
    }
