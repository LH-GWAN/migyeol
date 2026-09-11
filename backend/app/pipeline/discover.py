"""1. 사건 후보 발견 — 오늘의 이슈(issue_ranking) 를 날짜별로 순회해 재난·안전 사건 후보를 뽑는다.

각 이슈의 news_cluster 를 뉴스 상세 조회(news_ids)로 받아 category_incident 가 MVP 3계열이면 후보로 본다.
결과는 seeds/events.draft.yaml 초안으로 내보내고, 최종 시드는 사람이 확정한다.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from ..bigkinds.codes import is_mvp_incident, normalize_incident
from ..bigkinds.protocol import BigKindsClient

log = logging.getLogger("migyeol.discover")

_DETAIL_FIELDS = ["title", "published_at", "provider", "category", "category_incident"]


@dataclass
class Candidate:
    date: date
    topic: str
    topic_keywords: list[str]
    topic_content: str
    incident_types: Counter = field(default_factory=Counter)
    news_ids: list[str] = field(default_factory=list)
    sample_titles: list[str] = field(default_factory=list)
    mvp_ratio: float = 0.0

    @property
    def incident_type(self) -> str:
        return self.incident_types.most_common(1)[0][0] if self.incident_types else ""


def discover(client: BigKindsClient, dates: Iterable[date], min_ratio: float = 0.3) -> list[Candidate]:
    out: list[Candidate] = []
    for d in dates:
        ranking = client.issue_ranking(d.isoformat())
        for topic in ranking.topics:
            if not topic.news_cluster:
                continue
            docs = client.get_news_by_ids(topic.news_cluster, fields=_DETAIL_FIELDS).documents
            if not docs:
                continue
            mvp_docs = [doc for doc in docs if is_mvp_incident(doc.category_incident)]
            ratio = len(mvp_docs) / len(docs)
            if ratio < min_ratio:
                continue
            types = Counter(normalize_incident(c) for doc in mvp_docs for c in doc.category_incident)
            out.append(
                Candidate(
                    date=d,
                    topic=topic.topic,
                    topic_keywords=topic.keywords[:10],
                    topic_content=topic.topic_content,
                    incident_types=types,
                    news_ids=list(topic.news_cluster),
                    sample_titles=[doc.title for doc in mvp_docs[:5]],
                    mvp_ratio=round(ratio, 2),
                )
            )
            log.info("candidate %s %s (%.0f%% incident)", d, topic.topic, ratio * 100)
    return out


def write_seed_draft(candidates: list[Candidate], path: Path) -> Path:
    events = []
    for i, c in enumerate(candidates, 1):
        events.append(
            {
                "key": f"draft-{c.date.isoformat()}-{i}",
                "title": c.topic,
                "incident_type": c.incident_type,
                "region": "",  # 사람 확인
                "facility": "",  # 사람 확인
                "responsible_org": "",  # 사람 확인
                "org_aliases": [],
                "keywords": c.topic_keywords[:5],
                "occurred_at": c.date.isoformat(),
                "summary": c.topic_content,
                "_mvp_ratio": c.mvp_ratio,
                "_sample_titles": c.sample_titles,
                "_candidate_news_ids": c.news_ids,
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write("# discover 초안 — region/facility/responsible_org 를 사람이 채운 뒤 events.yaml 로 옮긴다.\n")
        yaml.safe_dump({"events": events}, f, allow_unicode=True, sort_keys=False)
    return path
