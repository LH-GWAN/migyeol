"""2. 관련 기사 수집·정제 — 연관어로 질의를 보강하고 뉴스 검색 API 를 구간별로 페이징 호출해 Article 로 저장한다.

- 수집 구간: 발생일-1 ~ +14일(초기 보도, 상한 collect_initial_cap) 과 그 이후 월별 구간(상한 collect_month_cap).
  날짜 오름차순 단일 호출로는 대형 사건의 첫 주 기사가 상한을 다 채워 약속·후속 기사를 놓친다.
- 수집한 기사는 (event_id, news_id, 질의 종류) 로 EventCandidate 에 기록해 link 단계의 후보로 쓴다.
- 사람 지정 질의(query_override)는 연관어 확장을 하지 않는다.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from difflib import SequenceMatcher

from sqlalchemy.orm import Session

from ..bigkinds.codes import normalize_incident
from ..bigkinds.protocol import DEFAULT_NEWS_FIELDS, BigKindsClient
from ..bigkinds.schemas import NewsDocument, NewsSearchArgument, PublishedAt, WordCloudArgument
from ..config import Settings
from ..db.models import Article, Event, EventCandidate
from .embeddings import EmbeddingBackend, needs_recompute, to_bytes
from .seeds import Seed, expand_query, seed_from_event

log = logging.getLogger("migyeol.collect")

# 연관어 확장에서 제외하는 일반어
_STOP = {
    "사고", "차량", "주민", "발생", "지역", "관계자", "오전", "오후", "이날", "구조", "당국", "대책", "점검",
    "조사", "결과", "피해", "안전", "시민", "현장", "부상", "사망", "사람", "이번", "지난", "올해", "내년",
}
_SORT = [{"date": "asc"}, {"_score": "desc"}]  # 같은 날짜 동률에 대비한 다중 정렬


def article_text(a: Article) -> str:
    return " ".join([a.title or "", a.hilight or "", a.content_200 or "", " ".join(a.tms_keywords or [])]).replace("\n", " ")


def _clean_hilight(s: str | None) -> str | None:
    if s is None:
        return None
    import re

    return re.sub(r"</?b>", "", s)[:200]


def expansions_for(client: BigKindsClient, seed: Seed, limit: int = 3) -> list[str]:
    """연관어 분석: 사건 핵심어 주변 30일 문서에서 상위 연관어를 뽑아 질의를 보강한다.

    제외 기준은 직전 실행의 확장 질의가 아니라 '기본 질의(연관어 미포함)' 라서 실행마다 흔들리지 않는다.
    """
    focus = seed.facility or (seed.keywords[0] if seed.keywords else seed.title)
    start = seed.occurred_at - timedelta(days=30)
    until = seed.occurred_at + timedelta(days=31)
    try:
        res = client.word_cloud(WordCloudArgument(query=f'"{focus}"', published_at=PublishedAt.of(start, until)))
    except Exception as exc:  # 연관어 실패는 치명적이지 않다
        log.warning("word_cloud failed for %s: %s", seed.key, exc)
        return []
    base_terms = {t.lower() for t in [*seed.subjects(), *seed.scopes()]}
    picked: list[str] = []
    for node in sorted(res.nodes, key=lambda n: (-n.weight, n.name)):
        name = node.name.strip()
        low = name.lower()
        if len(name) < 3 or any(ch.isdigit() for ch in name) or name in _STOP or low in base_terms or name in picked:
            continue
        if any(low in t or t in low for t in base_terms):
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


def fetch_articles(client: BigKindsClient, query: str, start: date, until: date, cap: int) -> tuple[list[NewsDocument], int, bool]:
    """[start, until) 구간을 날짜 오름차순으로 cap 건까지. 반환: (문서, total_hits, 잘림 여부)."""
    docs: list[NewsDocument] = []
    offset = 0
    total = 0
    while offset < cap:
        res = client.search_news(
            NewsSearchArgument(
                query=query,
                published_at=PublishedAt.of(start, until),
                sort=_SORT,
                hilight=200,
                return_from=offset,
                return_size=min(100, cap - offset),
                fields=list(DEFAULT_NEWS_FIELDS),
            )
        )
        total = res.total_hits
        for d in res.documents:
            if any(d.news_id == k.news_id for k in docs) or _near_duplicate(d, docs):
                continue
            docs.append(d)
        offset += len(res.documents)
        if not res.documents or offset >= res.total_hits:
            break
    return docs, total, total > offset


def _month_windows(start: date, end_exclusive: date) -> list[tuple[date, date]]:
    out = []
    cur = start
    while cur < end_exclusive:
        nxt = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
        out.append((cur, min(nxt, end_exclusive)))
        cur = nxt
    return out


def fetch_windowed(client: BigKindsClient, query: str, occurred: date, today: date, settings: Settings) -> tuple[list[NewsDocument], dict]:
    """초기 구간 + 월별 구간으로 나눠 수집한다. 총 상한 max_articles_per_event."""
    until = today + timedelta(days=1)  # until 은 제외이므로 오늘을 포함하려면 내일
    windows = [(occurred - timedelta(days=1), min(occurred + timedelta(days=15), until), settings.collect_initial_cap)]
    windows += [(a, b, settings.collect_month_cap) for a, b in _month_windows(occurred + timedelta(days=15), until)]
    docs: list[NewsDocument] = []
    seen: set[str] = set()
    info = {"windows": 0, "total_hits": 0, "truncated_windows": []}
    for start, end, cap in windows:
        if start >= end:
            continue
        remaining = settings.max_articles_per_event - len(docs)
        if remaining <= 0:
            info["truncated_windows"].append(f"{start}~{end}:총상한")
            continue
        got, total, truncated = fetch_articles(client, query, start, end, min(cap, remaining))
        info["windows"] += 1
        info["total_hits"] += total
        if truncated:
            info["truncated_windows"].append(f"{start}~{end}:{total}건 중 {len(got)}건")
        for d in got:
            if d.news_id not in seen:
                seen.add(d.news_id)
                docs.append(d)
    if info["truncated_windows"]:
        log.warning("수집 잘림 %s: %s", query[:60], info["truncated_windows"])
    return docs, info


def upsert_article(session: Session, doc: NewsDocument, embedder: EmbeddingBackend | None) -> tuple[Article, bool]:
    art = session.get(Article, doc.news_id)
    created = art is None
    if art is None:
        art = Article(news_id=doc.news_id)
        session.add(art)
        session.flush()  # 같은 실행의 다음 질의가 같은 기사를 돌려줘도 중복 insert 가 되지 않게
    before = article_text(art)
    art.title = doc.title or art.title
    art.published_at = doc.published_date or art.published_at
    art.provider = doc.provider or art.provider
    art.category = list(doc.category or art.category or [])
    art.category_incident = [normalize_incident(c) for c in (doc.category_incident or art.category_incident or [])]
    if doc.hilight is not None and not art.hilight:
        # 하이라이트는 처음 받은 것을 유지한다 (질의마다 스니펫이 달라져 근거·judge 입력이 흔들리지 않게)
        art.hilight = _clean_hilight(doc.hilight)
    if doc.content is not None and not art.content_200:
        art.content_200 = doc.content[:200]
    if doc.tms_raw_stream:
        art.tms_keywords = doc.keywords[:80]
    art.byline = doc.byline or art.byline
    art.provider_link_page = doc.provider_link_page or art.provider_link_page
    if embedder is not None and (needs_recompute(art.embedding, art.embedding_backend, embedder) or article_text(art) != before):
        art.embedding = to_bytes(embedder.encode([article_text(art)])[0])
        art.embedding_backend = embedder.name
    return art, created


def add_candidate(session: Session, event: Event, news_id: str, source: str) -> bool:
    row = session.get(EventCandidate, {"event_id": event.id, "news_id": news_id})
    if row is not None:
        if row.source != source and source == "seed":
            row.source = source
        return False
    session.add(EventCandidate(event_id=event.id, news_id=news_id, source=source))
    session.flush()
    return True


def collect_event(
    session: Session,
    client: BigKindsClient,
    event: Event,
    settings: Settings,
    embedder: EmbeddingBackend | None,
    today: date,
) -> dict:
    seed = seed_from_event(event)
    if seed.query:
        expansions: list[str] = []
        query = seed.query
    else:
        expansions = expansions_for(client, seed)
        query = expand_query(seed, expansions)
    event.seed_query = query
    event.expansions = expansions
    docs, info = fetch_windowed(client, query, event.occurred_at, today, settings)
    new = 0
    for d in docs:
        _, created = upsert_article(session, d, embedder)
        new += int(created)
        add_candidate(session, event, d.news_id, "seed")
    session.commit()
    stats = {"event": event.key, "query": query, "expansions": expansions, "fetched": len(docs), "new": new, **info}
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
    기간: 약속 발언일 ~ 오늘. promise_terms 는 현재 추적 약속에서 매번 다시 계산한다.
    """
    from ..rules.terms import action_terms
    from .seeds import _quote

    session.refresh(event)
    scopes = [s for s in dict.fromkeys([event.responsible_org, event.region, event.facility, *(event.org_aliases or [])]) if s]
    terms_all: list[str] = []
    seen: set[str] = set()
    fetched = new = 0
    truncated: list[str] = []
    for p in event.promises:
        if not p.is_trackable:
            continue
        terms = action_terms(p.action, exclude=scopes, limit=3)
        if not terms:
            continue
        terms_all.extend(t for t in terms if t not in terms_all)
        evidence = session.get(Article, p.evidence_news_id)
        start = evidence.published_at if evidence and evidence.published_at else event.occurred_at
        query = "(" + " OR ".join(_quote(terms)) + ") AND (" + " OR ".join(_quote(scopes)) + ")"
        docs, info = fetch_windowed(client, query, start, today, settings)
        truncated += info["truncated_windows"]
        for d in docs:
            if d.news_id in seen:
                continue
            seen.add(d.news_id)
            _, created = upsert_article(session, d, embedder)
            fetched += 1
            new += int(created)
            add_candidate(session, event, d.news_id, "promise")
    event.promise_terms = terms_all
    session.commit()
    stats = {"event": event.key, "promise_terms": terms_all, "fetched": fetched, "new": new, "truncated_windows": truncated}
    log.info("collect_promise_followups %s", stats)
    return stats
