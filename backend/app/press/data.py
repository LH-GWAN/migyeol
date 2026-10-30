"""보도자료 초안 자료원 — DB 집계(활성 사건·활성 연결·현재 약속 집합으로 분류된 유효 신호) + 실험 리포트 JSON(있으면).

- 유효 신호 = matches_promise AND confidence ≥ SIGNAL_MIN_CONF AND signal ∈ {완료, 진행, 새로운문제},
  현재 prompt_version·현재 추적 약속 집합 해시로 분류됐고, 근거 기사가 활성 연결·삭제 아님 (judge 와 같은 기준).
- "후속보도 확인" 사건 = 유효 신호가 1건 이상인 사건 (v2 §4.4).
- 기관 키 = rules/orgs.canonical_org(event.responsible_org).
- 실험 리포트(`settings.experiment_latest_path`, §4.6 스키마)가 있으면 리드·유형별·기관별 숫자는 그 값을 쓰고 출처를 밝힌다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import FOLLOWUP_STATUSES, STATUS_NO_FOLLOWUP, STATUSES, Settings
from ..db.models import Article, Event, StatusJudgment
from ..llm.client import PROMPT_VERSIONS
from ..notices.service import notices_out
from ..pipeline.judge import followup_articles, promise_set_hash, trackable_promises
from ..rules.orgs import canonical_org

log = logging.getLogger("migyeol.press")

VALID_SIGNALS = ("완료", "진행", "새로운문제")
SIGNAL_RANK = {"완료": 0, "진행": 1, "새로운문제": 2}


@dataclass
class ArticleRef:
    news_id: str
    title: str
    provider: str
    published_at: date | None
    link: str | None


@dataclass
class SignalRef:
    signal: str
    span: str
    confidence: float
    article: ArticleRef


@dataclass
class PromiseRef:
    actor_org: str
    action: str
    deadline_raw: str | None
    strength: str
    source: str | None
    quotation: str | None
    spoken_at: date | None
    article: ArticleRef | None


@dataclass
class EventFacts:
    id: int
    title: str
    incident_type: str
    region: str
    facility: str
    org: str
    occurred_at: date
    status: str
    judged: bool
    summary: str
    last_followup_at: date | None
    followup_count: int
    promises: list[PromiseRef] = field(default_factory=list)
    signals: list[SignalRef] = field(default_factory=list)
    notices: dict = field(default_factory=dict)  # notices_out()

    @property
    def has_followup(self) -> bool:
        return bool(self.signals)

    @property
    def best_signal(self) -> SignalRef | None:
        if not self.signals:
            return None
        return sorted(self.signals, key=lambda s: (SIGNAL_RANK.get(s.signal, 9), -(s.article.published_at or date.min).toordinal()))[0]


@dataclass
class Overview:
    as_of: date
    events: list[EventFacts]
    source_articles: int  # 원천 기사(공공 주체 대응 발언 근거 기사) 수
    source_articles_collected: int | None = None
    experiment: dict | None = None  # experiment_latest.json 그대로
    experiment_path: str | None = None

    @property
    def total(self) -> int:
        return len(self.events)

    @property
    def with_followup(self) -> list[EventFacts]:
        return [e for e in self.events if e.has_followup]

    @property
    def without_followup(self) -> list[EventFacts]:
        return [e for e in self.events if not e.has_followup]


def _article_ref(a: Article | None) -> ArticleRef | None:
    if a is None:
        return None
    return ArticleRef(news_id=a.news_id, title=a.title or "", provider=a.provider or "", published_at=a.published_at, link=a.provider_link_page)


def valid_signals(session: Session, event: Event, settings: Settings) -> list[SignalRef]:
    promises = trackable_promises(event)
    if not promises:
        return []
    pset = promise_set_hash(promises)
    version = PROMPT_VERSIONS["followup"]
    by_news = {a.news_id: a for a in followup_articles(session, event, settings)}  # 활성 연결·삭제 제외·후속 기간
    out = []
    for s in event.signals:
        if s.prompt_version != version or s.promise_set_hash != pset or s.news_id not in by_news:
            continue
        if s.signal not in VALID_SIGNALS or not s.matches_promise or s.confidence < settings.signal_min_conf:
            continue
        ref = _article_ref(by_news[s.news_id])
        assert ref is not None
        out.append(SignalRef(signal=s.signal, span=s.evidence_span or "", confidence=s.confidence, article=ref))
    out.sort(key=lambda r: r.article.published_at or date.min)
    return out


def event_facts(session: Session, event: Event, settings: Settings, today: date) -> EventFacts:
    judged = session.query(StatusJudgment.id).filter_by(event_id=event.id).first() is not None
    promises = []
    for p in trackable_promises(event):
        if p.merged_into_id is not None or p.strength == "해당없음":
            continue
        q = p.quotation
        art = _article_ref(session.get(Article, p.evidence_news_id)) if p.evidence_news_id else None
        promises.append(
            PromiseRef(
                actor_org=p.actor_org or "기관 미상",
                action=p.action,
                deadline_raw=p.deadline_raw,
                strength=p.strength,
                source=q.source if q else None,
                quotation=q.quotation if q else None,
                spoken_at=(q.published_at if q else None) or (art.published_at if art else None),
                article=art,
            )
        )
    return EventFacts(
        id=event.id,
        title=event.title,
        incident_type=event.incident_type or "",
        region=event.region or "",
        facility=event.facility or "",
        org=canonical_org(event.responsible_org) or (event.responsible_org or ""),
        occurred_at=event.occurred_at,
        status=event.status if event.status in STATUSES else STATUSES[-1],
        judged=judged,
        summary=event.summary or "",
        last_followup_at=event.last_followup_at,
        followup_count=event.followup_count or 0,
        promises=promises,
        # 대시보드와 같은 기준 — 후속보도 확인 상태(완료·진행·새로운 문제)인 사건만 신호를 근거로 쓴다
        signals=valid_signals(session, event, settings) if event.status in FOLLOWUP_STATUSES else [],
        notices=notices_out(session, event, settings, today),
    )


def load_experiment(path: Path) -> dict | None:
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("events"), dict) else None
    except (OSError, ValueError) as exc:
        log.warning("실험 리포트를 읽지 못함 %s: %s — DB 집계만 쓴다", path, exc)
        return None


def overview(session: Session, settings: Settings, today: date | None = None) -> Overview:
    today = today or settings.today()
    events = session.query(Event).filter(Event.active.is_(True)).order_by(Event.occurred_at, Event.id).all()
    facts = [event_facts(session, ev, settings, today) for ev in events]
    sources = {p.article.news_id for f in facts for p in f.promises if p.article is not None}
    exp = load_experiment(Path(settings.experiment_latest_path))
    ov = Overview(as_of=today, events=facts, source_articles=len(sources), experiment=exp, experiment_path=str(settings.experiment_latest_path) if exp else None)
    if exp:
        sa = exp.get("source_articles") or {}
        ov.source_articles_collected = sa.get("collected")
    return ov


def incident_group(incident_type: str) -> str:
    parts = [p for p in (incident_type or "").split(">") if p]
    return ">".join(parts[:2]) if parts else "분류 없음"


def by_incident(ov: Overview) -> list[dict]:
    """[{incident_type, events, with_followup, rate}] — 실험 리포트가 있으면 그 값."""
    if ov.experiment and ov.experiment.get("by_incident_type"):
        return [dict(r) for r in ov.experiment["by_incident_type"]]
    groups: dict[str, list[EventFacts]] = {}
    for e in ov.events:
        groups.setdefault(incident_group(e.incident_type), []).append(e)
    rows = [
        {"incident_type": k, "events": len(v), "with_followup": sum(1 for e in v if e.has_followup), "rate": round(sum(1 for e in v if e.has_followup) / len(v), 3)}
        for k, v in groups.items()
    ]
    return sorted(rows, key=lambda r: (-r["events"], r["incident_type"]))


def by_org(ov: Overview) -> list[dict]:
    """[{org, events, with_followup, rate, latest_followup_at, event_ids}] — 후속보도 확인 건수·비율 내림차순."""
    if ov.experiment and ov.experiment.get("by_org"):
        rows = [dict(r) for r in ov.experiment["by_org"]]
    else:
        groups: dict[str, list[EventFacts]] = {}
        for e in ov.events:
            groups.setdefault(e.org or "기관 미상", []).append(e)
        rows = []
        for org, v in groups.items():
            fu = [e for e in v if e.has_followup]
            latest = max((s.article.published_at for e in fu for s in e.signals if s.article.published_at), default=None)
            rows.append(
                {
                    "org": org,
                    "events": len(v),
                    "with_followup": len(fu),
                    "rate": round(len(fu) / len(v), 3),
                    "latest_followup_at": latest.isoformat() if latest else None,
                    "event_ids": [e.id for e in v],
                }
            )
    return sorted(rows, key=lambda r: (-(r.get("with_followup") or 0), -(r.get("rate") or 0.0), r.get("org") or ""))


def notice_summary(ov: Overview) -> dict:
    triggered = [e for e in ov.events if e.notices.get("triggered")]
    found = [e for e in ov.events if e.notices.get("items")]
    return {
        "triggered": len(triggered),
        "found": len(found),
        "no_site": sum(1 for e in triggered if e.notices.get("state") == "no_site"),
        "not_found": sum(1 for e in triggered if e.notices.get("state") == "not_found"),
        "not_checked": sum(1 for e in triggered if e.notices.get("state") == "not_checked"),
        "found_events": found,
    }


def gap_cases(ov: Overview, n: int = 3) -> list[EventFacts]:
    """후속보도가 확인되지 않은 대표 사례: 후속보도 부족 상태 → 약속이 있는 사건 → 오래된 순."""
    rows = [e for e in ov.without_followup if e.judged]
    return sorted(rows, key=lambda e: (e.status != STATUS_NO_FOLLOWUP, not e.promises, e.last_followup_at or e.occurred_at))[:n]


def followup_cases(ov: Overview, n: int = 3) -> list[EventFacts]:
    """후속보도가 확인된 대표 사례: 완료 > 진행 > 새로운문제, 같은 등급이면 최근 근거 기사 순."""
    rows = ov.with_followup
    return sorted(rows, key=lambda e: (SIGNAL_RANK.get(e.best_signal.signal, 9) if e.best_signal else 9, -((e.best_signal.article.published_at or date.min).toordinal() if e.best_signal else 0)))[:n]

