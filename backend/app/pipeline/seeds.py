"""추적 대상 사건 시드 (seeds/events.yaml). 최종 시드는 사람이 확정한다."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from ..db.models import Event


@dataclass
class Seed:
    key: str
    title: str
    incident_type: str
    region: str
    facility: str
    responsible_org: str
    occurred_at: date
    org_aliases: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    summary: str = ""
    query: str = ""

    def base_query(self) -> str:
        """('<시설명>' OR '<핵심어>' ...) AND ('<지역명>' OR '<기관명>' OR '<시설명>')

        시설명은 범위 절에도 넣는다 — 지역·기관명 없이 시설명만 언급하는 후속 기사를 놓치지 않기 위해.
        시설명이 일반적인 경우(예: 남문시장)는 seeds 의 query 로 직접 지정해 좁힌다.
        """
        if self.query:
            return self.query
        subjects = [self.facility, *self.keywords]
        subjects = [s for i, s in enumerate(subjects) if s and s not in subjects[:i]]
        scopes = [self.region, self.responsible_org, *self.org_aliases, self.facility]
        scopes = [s for i, s in enumerate(scopes) if s and s not in scopes[:i]]
        left = " OR ".join(f'"{s}"' for s in subjects)
        right = " OR ".join(f'"{s}"' for s in scopes)
        return f"({left}) AND ({right})"

    def seed_text(self) -> str:
        return " ".join([self.title, self.facility, self.region, self.responsible_org, " ".join(self.keywords), self.summary])


def load_seeds(path: Path) -> list[Seed]:
    with Path(path).open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    seeds = []
    for raw in data.get("events", []):
        occurred = raw["occurred_at"]
        if isinstance(occurred, str):
            occurred = date.fromisoformat(occurred)
        seeds.append(
            Seed(
                key=raw["key"],
                title=raw["title"],
                incident_type=raw["incident_type"],
                region=raw["region"],
                facility=raw.get("facility", ""),
                responsible_org=raw.get("responsible_org", ""),
                occurred_at=occurred,
                org_aliases=list(raw.get("org_aliases", []) or []),
                keywords=list(raw.get("keywords", []) or []),
                summary=raw.get("summary", "") or "",
                query=raw.get("query", "") or "",
            )
        )
    return seeds


def expand_query(seed: Seed, expansions: list[str]) -> str:
    """연관어 분석으로 얻은 표현을 왼쪽(주제) 절에 OR 로 보강한다."""
    base = seed.base_query()
    extra = [e for e in expansions if e and e not in base]
    if not extra or not base.startswith("("):
        return base
    close = base.index(")")
    left = base[1:close]
    return "(" + left + " OR " + " OR ".join(f'"{e}"' for e in extra) + base[close:]


def upsert_events(session: Session, seeds: list[Seed]) -> list[Event]:
    out = []
    for s in seeds:
        ev = session.query(Event).filter_by(key=s.key).one_or_none()
        if ev is None:
            ev = Event(key=s.key)
            session.add(ev)
        ev.title = s.title
        ev.incident_type = s.incident_type
        ev.region = s.region
        ev.facility = s.facility
        ev.responsible_org = s.responsible_org
        ev.org_aliases = s.org_aliases
        ev.keywords = s.keywords
        ev.occurred_at = s.occurred_at
        ev.summary = s.summary
        if not ev.seed_query:
            ev.seed_query = s.base_query()
        out.append(ev)
    session.commit()
    return out


def seed_from_event(ev: Event) -> Seed:
    return Seed(
        key=ev.key,
        title=ev.title,
        incident_type=ev.incident_type,
        region=ev.region,
        facility=ev.facility,
        responsible_org=ev.responsible_org,
        occurred_at=ev.occurred_at,
        org_aliases=list(ev.org_aliases or []),
        keywords=list(ev.keywords or []),
        summary=ev.summary or "",
        query="",
    )
