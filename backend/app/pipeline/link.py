"""3. 사건–기사 연결 (LLM 사용 금지) — 규칙 게이트 + 임베딩 유사도.

후보 = 수집 단계가 이 사건을 위해 가져온 기사(EventCandidate) ∪ 기존 연결. 로컬 질의 재매칭은 하지 않는다.

게이트(필수): 발행일 ≥ 발생일-1일 AND (지역 OR 시설 OR 기관 일치) AND (시설명 일치 OR 사건분류 계열 일치 OR 핵심어·연관어 2개 이상 OR 약속 행동 키워드 2개 이상)
판정:
  - 강한 규칙 일치                                            -> 자동 연결 (rule)
      · 시설명이 제목에 있고 (지역·기관 또는 사건분류) 일치
      · 시설명 + 지역·기관
      · 책임기관 + 약속 행동 키워드 2개, 또는 지역 + 약속 행동 키워드 2개 + (시설명 또는 시드 고유 핵심어 1개)
  - 게이트 통과 AND score ≥ LINK_AUTO                         -> 자동 연결 (embedding)
  - 게이트 통과 AND LINK_REVIEW ≤ score < LINK_AUTO           -> 연결하되 needs_review (검토 큐)
  - 그 외                                                     -> 폐기 (기존 자동 연결이면 삭제)
같은 지역의 다른 시드 시설명이 본문에 있고 이 사건 시설명이 없으면 score ≥ LINK_REVIEW 일 때만 검토 큐, 아니면 폐기.
한 기사가 여러 사건의 자동 연결에 들어가면 점수가 가장 높은 사건만 유지하고 나머지는 검토 큐로 보낸다.
관리자가 confirmed / rejected / manual 로 표시한 연결은 재평가하지 않는다.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta

import numpy as np
from sqlalchemy.orm import Session

from ..config import Settings
from ..db.models import Article, Event, EventArticle, EventCandidate
from .collect import article_text
from .embeddings import EmbeddingBackend, cosine, from_bytes, needs_recompute, to_bytes
from .review import recompute_needs_review
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
    # 책임기관·별칭이 지역명을 품으면(가온시·가온시청·가온시장 = 가온시) 기관 일치는 지역 일치와 같은 뜻이라 'org' 로 치지 않는다
    orgs = [o for o in [event.responsible_org, *(event.org_aliases or [])] if o and not (event.region and event.region in o)]
    if any(o in text for o in orgs):
        reasons.append("org")
    scope_ok = bool(reasons)
    prefix = incident_prefix(event.incident_type)
    if any(c.startswith(prefix) for c in (article.category_incident or [])):
        reasons.append("incident")
    seed_kws = [k for k in [event.facility, *(event.keywords or [])] if k]
    seed_hits = sum(1 for k in dict.fromkeys(seed_kws) if k in text)
    if seed_hits >= 1:
        reasons.append(f"seed-kw={seed_hits}")
    kws = [*seed_kws, *(event.expansions or [])]
    hits = sum(1 for k in dict.fromkeys(kws) if k and k in text)
    if hits >= 2:
        reasons.append(f"kw={hits}")
    p_hits = sum(1 for k in dict.fromkeys(event.promise_terms or []) if k in text)
    if p_hits >= 2:
        reasons.append(f"promise-kw={p_hits}")
    topic_ok = "incident" in reasons or hits >= 2 or p_hits >= 2 or "facility" in reasons
    return (scope_ok and topic_ok), reasons


def is_strong(reasons: list[str]) -> bool:
    scoped = "region" in reasons or "org" in reasons
    has_facility = "facility" in reasons
    seed_hit = any(r.startswith("seed-kw=") for r in reasons)
    promise_hit = any(r.startswith("promise-kw=") for r in reasons)
    if "facility-title" in reasons and (scoped or "incident" in reasons):
        return True
    if has_facility and scoped:
        return True
    if "org" in reasons and promise_hit:
        return True  # 책임기관 명의 + 약속 행동 키워드 2개 (일반어는 terms._STOP 에서 걸러진다)
    return scoped and promise_hit and (has_facility or seed_hit)


def initial_phase(event: Event, article: Article) -> str:
    if article.published_at is not None and (article.published_at - event.occurred_at).days <= 3:
        return "발생"
    if _INVESTIGATION_RE.search((article.title or "") + " " + (article.hilight or "")):
        return "원인조사"
    return "기타"


def candidate_articles(session: Session, event: Event) -> list[Article]:
    """후보 = 이 사건을 위해 수집한 기사(EventCandidate) ∪ 기존 연결."""
    ids = {c.news_id for c in session.query(EventCandidate).filter_by(event_id=event.id).all()}
    ids |= {l.news_id for l in event.links}
    if not ids:
        return []
    return session.query(Article).filter(Article.news_id.in_(ids)).all()


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
    stats = {"event": event.key, "candidates": 0, "linked": 0, "review": 0, "dropped": 0, "removed": 0, "skipped": 0}

    candidate_ids = {c.news_id for c in session.query(EventCandidate).filter_by(event_id=event.id).all()}
    for art in candidate_articles(session, event):
        stats["candidates"] += 1
        link = existing.get(art.news_id)
        if link is not None and (link.confirmed or link.rejected or link.link_method == "manual"):
            stats["skipped"] += 1
            continue
        if link is not None and art.news_id not in candidate_ids and candidate_ids:
            # 수집 후보에서 빠진(시드를 좁힌) 기사의 자동 연결은 지운다 — 게이트를 통과하더라도 더는 이 사건의 후보가 아니다
            event.links.remove(link)
            session.delete(link)
            existing.pop(art.news_id, None)
            stats["removed"] += 1
            continue

        def drop() -> None:
            # 이번 실행의 게이트·임계값을 통과하지 못한 자동 연결은 남기지 않는다 (시드 수정·질의 변화 반영)
            if link is not None:
                event.links.remove(link)
                session.delete(link)
                existing.pop(art.news_id, None)
                stats["removed"] += 1
            else:
                stats["dropped"] += 1

        ok, reasons = gate(event, art)
        if not ok:
            drop()
            continue
        if needs_recompute(art.embedding, art.embedding_backend, embedder):
            art.embedding = to_bytes(embedder.encode([article_text(art)])[0])
            art.embedding_backend = embedder.name
        score = cosine(from_bytes(art.embedding), np.asarray(seed_vec))
        text = article_text(art)
        strong = is_strong(reasons)
        other_hit = any(f in text for f in others) and "facility" not in reasons

        if other_hit:
            if score < settings.link_review:
                drop()
                continue
            decision, method, review = "review", "embedding", True
            reasons.append("other-facility")
        elif strong or score >= settings.link_auto:
            decision, method, review = "link", ("rule" if strong else "embedding"), False
        elif score >= settings.link_review:
            decision, method, review = "review", "embedding", True
        else:
            drop()
            continue

        if link is None:
            link = EventArticle(event_id=event.id, news_id=art.news_id, phase=initial_phase(event, art))
            event.links.append(link)
            existing[art.news_id] = link
        link.link_method = method
        link.link_score = round(score, 3)
        link.link_reason = "+".join(reasons)
        link.needs_review = review
        link.auto_decision = decision  # 관리자 처리와 무관한 파이프라인 결정 (평가용)
        if link.phase in ("발생", "원인조사", "기타"):
            link.phase = initial_phase(event, art)
        stats["linked" if decision == "link" else "review"] += 1

    recompute_needs_review(session, event)
    session.commit()
    log.info("link %s", stats)
    return stats


def resolve_multi_event_links(session: Session, events: list[Event]) -> list[int]:
    """한 기사가 여러 사건에 **자동 연결**(검토 대기 아님)되면 점수가 가장 높은 사건만 유지하고 나머지는 검토 큐로 보낸다.

    이미 검토 대기인 연결은 경쟁에 넣지 않는다 — 다른 사건의 정상 연결까지 강등돼 어느 사건에도 붙지 않는 일을 막는다.
    """
    by_news: dict[str, list[EventArticle]] = {}
    for ev in events:
        session.refresh(ev)
        for l in ev.links:
            if l.rejected or l.confirmed or l.link_method == "manual" or l.needs_review:
                continue
            by_news.setdefault(l.news_id, []).append(l)
    changed: list[int] = []  # 연결이 강등된 사건 id (재판정 대상)
    for links in by_news.values():
        if len(links) < 2:
            continue
        best = max(links, key=lambda l: l.link_score)
        for l in links:
            if l is best or l.needs_review:
                continue
            l.needs_review = True
            if "multi-event" not in (l.link_reason or ""):
                l.link_reason = (l.link_reason or "") + "+multi-event"
            changed.append(l.event_id)
    for ev in events:
        recompute_needs_review(session, ev)
    session.commit()
    return changed


def link_all(
    session: Session,
    events: list[Event],
    settings: Settings,
    embedder: EmbeddingBackend,
    all_facilities: list[str] | None = None,
    all_events: list[Event] | None = None,
) -> list[dict]:
    """all_events: 사건 간 배타 처리 대상 (단일 사건 실행이어도 다른 활성 사건의 연결과 비교한다)."""
    facilities = all_facilities if all_facilities is not None else [e.facility for e in events]
    stats = [link_event(session, e, settings, embedder, other_facilities=facilities) for e in events]
    multi = resolve_multi_event_links(session, all_events if all_events is not None else events)
    if stats:
        stats[0]["multi_event_review"] = len(multi)
        stats[0]["multi_event_events"] = sorted(set(multi))
    return stats
