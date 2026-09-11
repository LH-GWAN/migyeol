"""4. 약속 구조화 — 인용문 검색 API 로 대응 주체의 발언을 모으고 LLM(역할 ⓐ)으로 주체·행동·기한·발언 강도를 추출한다.

공공 주체 판별은 규칙(actors.py)이 먼저 거르고, 통과한 인용문만 LLM 에 보낸다.
strength ∈ {확약, 계획} 이고 action 이 있으면 is_trackable=True.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import numpy as np
from sqlalchemy.orm import Session

from ..bigkinds.protocol import DEFAULT_QUOTATION_FIELDS, BigKindsClient
from ..bigkinds.schemas import PublishedAt, QuotationDocument, QuotationSearchArgument
from ..config import Settings
from ..db.models import Article, Event, Promise, Quotation
from ..llm.client import PROMPT_VERSIONS, LLMClient
from ..llm.schemas import PromiseInput
from ..rules.actors import is_public_actor, normalize_actor_org
from .embeddings import EmbeddingBackend

log = logging.getLogger("migyeol.extract")


def fetch_quotations(client: BigKindsClient, query: str, start: date, until: date, max_items: int = 1000) -> list[QuotationDocument]:
    out: list[QuotationDocument] = []
    offset = 0
    while offset < max_items:
        res = client.search_quotation(
            QuotationSearchArgument(
                query=query,
                published_at=PublishedAt.of(start, until),
                sort={"date": "asc"},
                hilight=300,
                return_from=offset,
                return_size=100,
                fields=list(DEFAULT_QUOTATION_FIELDS),
            )
        )
        out.extend(res.documents)
        offset += len(res.documents)
        if not res.documents or offset >= res.total_hits:
            break
    return out


def _parse_deadline(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _merge_duplicates(event: Event, embedder: EmbeddingBackend | None) -> int:
    """같은 기관 + 유사 행동(코사인 ≥ 0.85 또는 동일)은 하나로 합친다. 근거 news_id 는 모두 보존."""
    merged = 0
    promises = sorted([p for p in event.promises if p.is_trackable], key=lambda p: p.id)
    kept: list[Promise] = []
    vecs: dict[int, np.ndarray] = {}
    if embedder is not None and promises:
        for p, v in zip(promises, embedder.encode([p.action for p in promises])):
            vecs[p.id] = v
    for p in promises:
        dup = None
        for k in kept:
            if k.actor_org != p.actor_org:
                continue
            same = k.action == p.action
            if not same and vecs:
                same = float(np.dot(vecs[k.id], vecs[p.id])) >= 0.85
            if same:
                dup = k
                break
        if dup is None:
            kept.append(p)
        else:
            ids = list(dup.merged_news_ids or [])
            if p.evidence_news_id and p.evidence_news_id not in ids and p.evidence_news_id != dup.evidence_news_id:
                ids.append(p.evidence_news_id)
            dup.merged_news_ids = ids
            p.is_trackable = False
            p.rationale = (p.rationale or "") + f" [병합: promise {dup.id} 와 동일 조치]"
            merged += 1
    return merged


def extract_event(
    session: Session,
    client: BigKindsClient,
    event: Event,
    llm: LLMClient,
    settings: Settings,
    embedder: EmbeddingBackend | None = None,
) -> dict:
    session.refresh(event)
    linked_ids = {l.news_id for l in event.links if not l.rejected and not l.needs_review}
    start = event.occurred_at
    until = event.occurred_at + timedelta(days=settings.quotation_window_days)
    docs = fetch_quotations(client, event.seed_query, start, until)
    aliases = [event.responsible_org, *(event.org_aliases or [])]
    existing = {(q.news_id, q.source, q.quotation): q for q in event.quotations}
    stats = {"event": event.key, "quotes": len(docs), "linked_quotes": 0, "public": 0, "llm_calls": 0, "promises": 0, "merged": 0}

    for d in docs:
        if d.news_id not in linked_ids:
            continue
        stats["linked_quotes"] += 1
        key = (d.news_id, d.source, d.quotation)
        q = existing.get(key)
        if q is None:
            q = Quotation(
                event_id=event.id,
                news_id=d.news_id,
                source=d.source,
                quotation=d.quotation,
                published_at=d.published_date,
                provider=d.provider,
                is_public_actor=is_public_actor(d.source, aliases),
            )
            event.quotations.append(q)
            session.flush()
            existing[key] = q
        if not q.is_public_actor:
            continue
        stats["public"] += 1
        if q.promises:
            continue  # 이미 추출됨
        article = session.get(Article, d.news_id)
        inp = PromiseInput(
            event_summary=event.summary or event.title,
            responsible_org=event.responsible_org,
            source=d.source,
            quotation=d.quotation,
            article_title=(article.title if article else d.title) or "",
            published_at=(q.published_at or event.occurred_at).isoformat(),
        )
        stats["llm_calls"] += 1
        res = llm.extract_promise(inp, key=f"{d.news_id}||{d.source}||{d.quotation}")
        actor = normalize_actor_org(d.source, event.responsible_org, aliases) or res.actor_org or ""
        deadline = _parse_deadline(res.deadline_date)
        p = Promise(
            event_id=event.id,
            quotation_id=q.id,
            actor_org=actor,
            actor_raw=res.actor_raw or d.source,
            action=res.action or "",
            deadline_raw=res.deadline_raw,
            deadline_date=deadline,
            deadline_precision=res.deadline_precision if deadline else "none",
            strength=res.strength,
            is_trackable=bool(res.is_promise and res.strength in ("확약", "계획") and res.action),
            evidence_news_id=d.news_id,
            confidence=res.confidence,
            rationale=res.rationale,
            model=llm.model,
            prompt_version=PROMPT_VERSIONS["promise"],
        )
        event.promises.append(p)
        q.promises.append(p)
        if p.is_trackable:
            stats["promises"] += 1
    session.flush()
    session.refresh(event)
    stats["merged"] = _merge_duplicates(event, embedder)

    # 약속 근거 기사는 '대응발표' 단계
    evidence_ids = {p.evidence_news_id for p in event.promises if p.is_trackable or p.strength == "의사표명"}
    for l in event.links:
        if l.news_id in evidence_ids:
            l.phase = "대응발표"
    session.commit()
    log.info("extract %s", stats)
    return stats
