"""추적 대상 사건 시드 (seeds/events.yaml). 최종 시드는 사람이 확정한다.

- 질의는 문자열 조작이 아니라 subjects/scopes 목록으로 조립한다 (괄호·따옴표가 든 이름도 안전).
- 시드 YAML 의 `query` 는 사람 지정 질의다. Event.query_override 에 보존되고, 연관어 확장 없이 그대로 쓴다.
- 시드에서 빠진 사건은 Event.active=False 로 두어 목록에서 제외한다 (DB 에서 지우지 않는다 — 신고·관리자 판단 보존).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from ..db.models import Event


class SeedError(ValueError):
    pass


def _clean(term: str) -> str:
    return re.sub(r'["“”()]', " ", term or "").strip()


def _quote(terms: list[str]) -> list[str]:
    out: list[str] = []
    for t in terms:
        c = _clean(t)
        if c and c not in out:
            out.append(c)
    return [f'"{t}"' for t in out]


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
    query: str = ""  # 사람 지정 질의 (있으면 그대로 사용, 연관어 확장 안 함)
    # v2 §4.2 실험 자동 시드 (트랙 A). 사람이 만든 시드에는 없어도 된다
    source_news_id: str = ""
    source_quotation: str = ""
    auto_seed: bool = False

    def subjects(self) -> list[str]:
        subs = [s for s in [self.facility, *self.keywords] if _clean(s)]
        if not subs:
            # 시설·핵심어가 없으면 제목 핵심어로 대체 (빈 '()' 절을 만들지 않는다)
            subs = [t for t in re.findall(r"[가-힣A-Za-z0-9]{2,}", self.title) if t not in (self.region, self.responsible_org)][:3]
        return subs

    def scopes(self) -> list[str]:
        return [s for s in [self.region, self.responsible_org, *self.org_aliases, self.facility] if _clean(s)]

    def build_query(self, expansions: list[str] | None = None) -> str:
        """('<시설명>' OR '<핵심어>' OR '<연관어>' ...) AND ('<지역명>' OR '<기관명>' OR '<시설명>')

        시설명은 범위 절에도 넣는다 — 지역·기관명 없이 시설명만 언급하는 후속 기사를 놓치지 않기 위해.
        시설명이 일반적인 경우(예: 남문시장)는 seeds 의 query 로 직접 지정해 좁힌다.
        """
        if self.query.strip():
            return self.query.strip()
        left = _quote([*self.subjects(), *(expansions or [])])
        right = _quote(self.scopes())
        if not left:
            raise SeedError(f"시드 {self.key}: 질의 주제 절이 비어 있다 (facility/keywords/title 확인)")
        if not right:
            return "(" + " OR ".join(left) + ")"
        return "(" + " OR ".join(left) + ") AND (" + " OR ".join(right) + ")"

    def base_query(self) -> str:
        return self.build_query([])

    def seed_text(self) -> str:
        return " ".join([self.title, self.facility, self.region, self.responsible_org, " ".join(self.keywords), self.summary])


def load_seeds(path: Path) -> list[Seed]:
    with Path(path).open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    seeds = []
    keys: set[str] = set()
    for raw in data.get("events", []) or []:
        occurred = raw.get("occurred_at")
        if isinstance(occurred, str):
            occurred = date.fromisoformat(occurred)
        if not isinstance(occurred, date):
            raise SeedError(f"시드 {raw.get('key')}: occurred_at 이 날짜가 아니다: {occurred!r}")
        seed = Seed(
            key=str(raw["key"]),
            title=str(raw["title"]),
            incident_type=str(raw["incident_type"]),
            region=str(raw.get("region", "") or ""),
            facility=str(raw.get("facility", "") or ""),
            responsible_org=str(raw.get("responsible_org", "") or ""),
            occurred_at=occurred,
            org_aliases=[str(a) for a in (raw.get("org_aliases", []) or [])],
            keywords=[str(k) for k in (raw.get("keywords", []) or [])],
            summary=str(raw.get("summary", "") or ""),
            query=str(raw.get("query", "") or ""),
            source_news_id=str(raw.get("source_news_id", "") or ""),
            source_quotation=str(raw.get("source_quotation", "") or "")[:300],
            auto_seed=bool(raw.get("auto_seed", False)),
        )
        if seed.key in keys:
            raise SeedError(f"시드 key 중복: {seed.key}")
        keys.add(seed.key)
        seed.build_query([])  # 질의 조립 검증 (빈 절이면 여기서 실패)
        seeds.append(seed)
    return seeds


def expand_query(seed: Seed, expansions: list[str]) -> str:
    """연관어 분석으로 얻은 표현을 주제 절에 OR 로 보강한다. 사람 지정 질의면 그대로."""
    if seed.query.strip():
        return seed.query.strip()
    base_terms = {t.lower() for t in seed.subjects()} | {t.lower() for t in seed.scopes()}
    extra = [e for e in expansions if e and e.lower() not in base_terms]
    return seed.build_query(extra)


def upsert_events(session: Session, seeds: list[Seed]) -> list[Event]:
    out = []
    seen_keys = {s.key for s in seeds}
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
        ev.query_override = s.query.strip()
        ev.active = True
        ev.source_news_id = s.source_news_id or None
        ev.source_quotation = s.source_quotation or None
        ev.auto_seed = s.auto_seed
        if ev.query_override:
            ev.seed_query = ev.query_override
        elif not ev.seed_query:
            ev.seed_query = s.base_query()
        out.append(ev)
    # 시드에서 빠진 사건은 비활성 (목록·검토 큐 제외, 데이터는 보존)
    for ev in session.query(Event).filter(Event.active.is_(True)).all():
        if ev.key not in seen_keys:
            ev.active = False
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
        query=ev.query_override or "",
    )
