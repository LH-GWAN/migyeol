"""3. 사건–기사 연결 (LLM 사용 금지) — 규칙 게이트 + 임베딩 유사도.

게이트(필수): 발행일 ≥ 발생일-1일 AND (지역 OR 시설 OR 기관 일치) AND (시설명 일치 OR 사건분류 계열 일치 OR 시드 핵심어 2개 이상)
  ※ 사건분류 태그가 없는 착공·완료 후속 기사는 시설명으로만 잡히므로 시설명 일치를 주제 조건으로 인정한다.
    시설명만 있고 지역·기관이 없으면 강한 일치가 아니므로 임베딩 점수가 결정한다.
판정:
  - 강한 규칙 일치(시설명이 제목에 있음 / 시설명 + 지역·기관 / 지역·기관 + 약속 행동 키워드 2개) -> 자동 연결 (rule)
  - 게이트 통과 AND score ≥ LINK_AUTO                     -> 자동 연결 (embedding)
  - 게이트 통과 AND LINK_REVIEW ≤ score < LINK_AUTO       -> 연결하되 needs_review (검토 큐)
  - 그 외                                                 -> 폐기
같은 지역의 다른 시드 시설명이 본문에 있고 이 사건 시설명이 없으면 무조건 검토 큐로 보낸다.
관리자가 confirmed / rejected 로 표시한 연결은 재평가하지 않는다.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta

import numpy as np
from sqlalchemy.orm import Session

from ..bigkinds.query import Query
from ..config import Settings
from ..db.models import Article, Event, EventArticle
from .collect import article_text
from .embeddings import EmbeddingBackend, cosine, from_bytes, to_bytes
from .seeds import seed_from_event

log = logging.getLogger("migyeol.link")

_INVESTIGATION_RE = re.compile(r"(원인|조사|감사|감식|결론|조사위|진단)")


def incident_prefix(incident_type: str) -> str:
    parts = incident_type.split(">")
    return ">".join(parts[:2]) if len(parts) >= 2 else incident_type


def gate(event: Event, article: Article) -> tuple[bool, list[str]]:
    text = article_text(article)
    reasons: list[str] = []
    if article.published_at is None or article.published_at < event.occurred_at - timedelta(days=1):
        return False, ["date"]
    if event.region and event.region in text:
        reasons.append("region")
    if event.facility and event.facility in text:
        reasons.append("facility")
        if event.facility in (article.title or ""):
            reasons.append("facility-title")
    orgs = [event.responsible_org, *(event.org_aliases or [])]
    if any(o and o in text for o in orgs):
        reasons.append("org")
    scope_ok = bool(reasons)
    prefix = incident_prefix(event.incident_type)
    if any(c.startswith(prefix) for c in (article.category_incident or [])):
        reasons.append("incident")
    kws = [k for k in [event.facility, *(event.keywords or []), *(event.expansions or [])] if k]
    hits = sum(1 for k in dict.fromkeys(kws) if k in text)
    if hits >= 2:
        reasons.append(f"kw={hits}")
    p_hits = sum(1 for k in dict.fromkeys(event.promise_terms or []) if k in text)
    if p_hits >= 2:
        reasons.append(f"promise-kw={p_hits}")
    topic_ok = "incident" in reasons or hits >= 2 or p_hits >= 2 or "facility" in reasons
    return (scope_ok and topic_ok), reasons


def initial_phase(event: Event, article: Article) -> str:
    if article.published_at is not None and (article.published_at - event.occurred_at).days <= 3:
        return "발생"
    if _INVESTIGATION_RE.search(article.title or ""):
        return "원인조사"
    return "기타"


def candidate_articles(session: Session, event: Event) -> list[Article]:
    """수집 단계와 무관하게 재실행 가능하도록 DB 의 기사 중 시드 질의와 맞는 것을 후보로 삼는다."""
    q = Query(event.seed_query or seed_from_event(event).base_query())
    linked = {l.news_id for l in event.links}
    out = []
    for a in session.query(Article).all():
        if a.news_id in linked or q.matches(article_text(a)):
            out.append(a)
    return out


def link_event(
    session: Session,
    event: Event,
    settings: Settings,
    embedder: EmbeddingBackend,
    other_facilities: list[str] | None = None,
) -> dict:
    session.refresh(event)
    seed_vec = embedder.encode([seed_from_event(event).seed_text()])[0]
    existing = {l.news_id: l for l in event.links}
    others = [f for f in (other_facilities or []) if f and f != event.facility]
    stats = {"event": event.key, "candidates": 0, "linked": 0, "review": 0, "dropped": 0, "skipped": 0}

    for art in candidate_articles(session, event):
        stats["candidates"] += 1
        link = existing.get(art.news_id)
        if link is not None and (link.confirmed or link.rejected or link.link_method == "manual"):
            stats["skipped"] += 1
            continue
        ok, reasons = gate(event, art)
        if not ok:
            stats["dropped"] += 1
            continue
        if art.embedding is None or art.embedding_backend != embedder.name:
            art.embedding = to_bytes(embedder.encode([article_text(art)])[0])
            art.embedding_backend = embedder.name
        score = round(cosine(from_bytes(art.embedding), np.asarray(seed_vec)), 3)
        text = article_text(art)
        scoped = "region" in reasons or "org" in reasons
        strong = (
            "facility-title" in reasons
            or ("facility" in reasons and scoped)
            or (scoped and any(r.startswith("promise-kw=") for r in reasons))
        )
        other_hit = any(f in text for f in others) and "facility" not in reasons

        if other_hit:
            decision, method, review = "review", "embedding", True
            reasons.append("other-facility")
        elif strong or score >= settings.link_auto:
            decision, method, review = "link", ("rule" if strong else "embedding"), False
        elif score >= settings.link_review:
            decision, method, review = "review", "embedding", True
        else:
            stats["dropped"] += 1
            continue

        if link is None:
            link = EventArticle(event_id=event.id, news_id=art.news_id, phase=initial_phase(event, art))
            event.links.append(link)
            existing[art.news_id] = link
        link.link_method = method
        link.link_score = score
        link.link_reason = "+".join(reasons)
        link.needs_review = review
        if link.phase in ("발생", "원인조사", "기타"):
            link.phase = initial_phase(event, art)
        stats["linked" if decision == "link" else "review"] += 1

    event.needs_review = any(l.needs_review for l in existing.values() if not l.rejected)
    session.commit()
    log.info("link %s", stats)
    return stats


def link_all(session: Session, events: list[Event], settings: Settings, embedder: EmbeddingBackend) -> list[dict]:
    facilities = [e.facility for e in events]
    return [link_event(session, e, settings, embedder, other_facilities=facilities) for e in events]
