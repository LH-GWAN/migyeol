"""2. 관련 기사 수집·정제 — 연관어로 질의를 보강하고 뉴스 검색 API 를 페이징 호출해 Article 로 저장한다."""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from difflib import SequenceMatcher

from sqlalchemy.orm import Session

from ..bigkinds.protocol import DEFAULT_NEWS_FIELDS, BigKindsClient
from ..bigkinds.schemas import NewsDocument, NewsSearchArgument, PublishedAt, WordCloudArgument
from ..config import Settings
from ..db.models import Article, Event
from .embeddings import EmbeddingBackend, to_bytes
from .seeds import expand_query, seed_from_event

log = logging.getLogger("migyeol.collect")

# 연관어 확장에서 제외하는 일반어
_STOP = {
    "사고", "차량", "주민", "발생", "지역", "관계자", "오전", "오후", "이날", "구조", "당국", "대책", "점검",
    "조사", "결과", "피해", "안전", "시민", "현장", "부상", "사망", "사람", "이번", "지난", "올해", "내년",
}


def article_text(a: Article) -> str:
    return " ".join([a.title or "", a.hilight or "", a.content_200 or "", " ".join(a.tms_keywords or [])]).replace("\n", " ")


def _clean_hilight(s: str | None) -> str | None:
    if s is None:
        return None
    return re.sub(r"</?b>", "", s)[:200]


def expansions_for(client: BigKindsClient, event: Event, limit: int = 3) -> list[str]:
    """연관어 분석: 사건 핵심어 주변 30일 문서에서 상위 연관어를 뽑아 질의를 보강한다."""
    focus = event.facility or (event.keywords[0] if event.keywords else event.title)
    start = event.occurred_at - timedelta(days=30)
    until = event.occurred_at + timedelta(days=31)
    try:
        res = client.word_cloud(WordCloudArgument(query=f'"{focus}"', published_at=PublishedAt.of(start, until)))
    except Exception as exc:  # 연관어 실패는 치명적이지 않다
        log.warning("word_cloud failed for %s: %s", event.key, exc)
        return []
    base = event.seed_query or ""
    picked: list[str] = []
    for node in sorted(res.nodes, key=lambda n: -n.weight):
        name = node.name.strip()
        if len(name) < 3 or any(ch.isdigit() for ch in name) or name in _STOP or name in base or name in picked:
            continue
        if name in event.region or name in event.responsible_org:
            continue
        picked.append(name)
        if len(picked) >= limit:
            break
    return picked


def _near_duplicate(doc: NewsDocument, kept: list[NewsDocument]) -> bool:
    for k in kept:
        if k.provider == doc.provider and k.published_date == doc.published_date:
            if SequenceMatcher(None, k.title, doc.title).ratio() >= 0.9:
                return True
    return False


def fetch_articles(client: BigKindsClient, query: str, start: date, until: date, max_articles: int) -> list[NewsDocument]:
    docs: list[NewsDocument] = []
    offset = 0
    page = 100
    while offset < max_articles:
        res = client.search_news(
            NewsSearchArgument(
                query=query,
                published_at=PublishedAt.of(start, until),
                sort={"date": "asc"},
                hilight=200,
                return_from=offset,
                return_size=min(page, max_articles - offset),
                fields=list(DEFAULT_NEWS_FIELDS),
            )
        )
        for d in res.documents:
            if any(d.news_id == k.news_id for k in docs) or _near_duplicate(d, docs):
                continue
            docs.append(d)
        offset += len(res.documents)
        if not res.documents or offset >= res.total_hits:
            break
    return docs


def upsert_article(session: Session, doc: NewsDocument, embedder: EmbeddingBackend | None) -> tuple[Article, bool]:
    art = session.get(Article, doc.news_id)
    created = art is None
    if art is None:
        art = Article(news_id=doc.news_id)
        session.add(art)
    art.title = doc.title or art.title
    art.published_at = doc.published_date or art.published_at
    art.provider = doc.provider or art.provider
    art.category = list(doc.category or art.category or [])
    art.category_incident = list(doc.category_incident or art.category_incident or [])
    if doc.hilight is not None:
        art.hilight = _clean_hilight(doc.hilight)
    if doc.content is not None:
        art.content_200 = doc.content[:200]
    if doc.tms_raw_stream:
        art.tms_keywords = doc.keywords[:40]
    art.byline = doc.byline or art.byline
    art.provider_link_page = doc.provider_link_page or art.provider_link_page
    if embedder is not None and (art.embedding is None or art.embedding_backend != embedder.name):
        art.embedding = to_bytes(embedder.encode([article_text(art)])[0])
        art.embedding_backend = embedder.name
    return art, created


def collect_event(
    session: Session,
    client: BigKindsClient,
    event: Event,
    settings: Settings,
    embedder: EmbeddingBackend | None,
    today: date,
) -> dict:
    seed = seed_from_event(event)
    expansions = expansions_for(client, event)
    query = expand_query(seed, expansions)
    event.seed_query = query
    event.expansions = expansions
    start = event.occurred_at - timedelta(days=1)
    until = today + timedelta(days=1)  # until 은 제외이므로 오늘을 포함하려면 내일
    docs = fetch_articles(client, query, start, until, settings.max_articles_per_event)
    new = 0
    for d in docs:
        _, created = upsert_article(session, d, embedder)
        new += int(created)
    session.commit()
    stats = {"event": event.key, "query": query, "expansions": expansions, "fetched": len(docs), "new": new}
    log.info("collect %s", stats)
    return stats


def collect_promise_followups(
    session: Session,
    client: BigKindsClient,
    event: Event,
    settings: Settings,
    embedder: EmbeddingBackend | None,
    today: date,
) -> dict:
    """약속의 행동 키워드로 후속 기사를 추가 검색한다 — 후속 기사가 시설명 대신 다른 표현을 쓰는 경우 대비.

    질의: ("<행동 명사1>" OR "<행동 명사2>" ...) AND ("<책임기관>" OR "<지역>" OR "<시설>")
    기간: 약속 발언일 ~ 오늘
    """
    from ..rules.terms import action_terms

    scopes = [s for s in dict.fromkeys([event.responsible_org, event.region, event.facility, *(event.org_aliases or [])]) if s]
    terms_all: list[str] = list(event.promise_terms or [])
    fetched = new = 0
    for p in event.promises:
        if not p.is_trackable:
            continue
        terms = action_terms(p.action, exclude=scopes, limit=3)
        if not terms:
            continue
        terms_all.extend(t for t in terms if t not in terms_all)
        evidence = session.get(Article, p.evidence_news_id)
        start = (evidence.published_at if evidence and evidence.published_at else event.occurred_at)
        query = "(" + " OR ".join(f'"{t}"' for t in terms) + ") AND (" + " OR ".join(f'"{s}"' for s in scopes) + ")"
        docs = fetch_articles(client, query, start, today + timedelta(days=1), settings.max_articles_per_event)
        for d in docs:
            _, created = upsert_article(session, d, embedder)
            fetched += 1
            new += int(created)
    event.promise_terms = terms_all
    session.commit()
    stats = {"event": event.key, "promise_terms": terms_all, "fetched": fetched, "new": new}
    log.info("collect_promise_followups %s", stats)
    return stats
