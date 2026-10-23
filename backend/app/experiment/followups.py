"""실험용 약속 키워드 재검색 (pipeline.collect.collect_promise_followups 의 호출 예산판).

파이프라인 판은 추적 약속마다 (행동 명사 OR …) AND (기관 OR 지역 OR 시설) 을 약속 발언일 ~ 기준일로 따로 부른다.
실데이터에서는 '건축물'·'정밀점검' 같은 명사와 지역명만으로 수천 건이 걸려 월·순별 분할 수집이 약속당 100회 넘게 나온다
(150 사건 × 약속 5건 ≈ 7만 회). 실험은 같은 목적(시설명 대신 다른 표현을 쓰는 후속 기사 찾기)을 사건당 한 질의로 한다:

  질의   = (약속 행동 명사 상위 4개 OR) AND (지역 OR 시설 OR 지역 기관)
  건수   = time_line 으로 먼저 본다. 상한(400) 넘으면 재난어를 AND 로 더하고, 그래도 넘으면 이 사건은 건너뛴다(기록).
  수집   = 상한 안이면 collect.fetch_windowed(mode="narrow") — 같은 time_line 인자라 녹화 재생, 실제 호출은 검색 1회.
결과 기사는 EventCandidate(source="promise") 로 넣고 Event.promise_terms 를 채운다 (연결 게이트·강한 규칙이 그대로 쓴다).
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import date, timedelta

from sqlalchemy.orm import Session

from ..bigkinds.schemas import PublishedAt, TimeLineArgument
from ..config import Settings
from ..db.models import Article, Event
from ..pipeline.collect import add_candidate, fetch_windowed, upsert_article
from ..pipeline.judge import trackable_promises
from ..rules.regions import ADMIN_NAMES
from ..rules.terms import action_terms

log = logging.getLogger("migyeol.experiment")

_VERBISH = re.compile(r"(고|다|며|면|서|지|게|요|은|는|을|를|한|된|던|할|될|겠|도록|으로|에서|부터|까지)$")
_GENERIC = {"시설", "대책", "점검", "안전", "사업", "지원", "추진", "조치", "관리", "정부", "피해", "사고", "현장", "주민", "기관", "관계", "부분", "필요", "방안", "문제"}


def _q(t: str) -> str:
    return '"' + re.sub(r'["“”()]', " ", t).strip() + '"'


def promise_query_terms(event: Event, limit: int = 4) -> list[str]:
    scopes = [s for s in [event.responsible_org, event.region, event.facility, *(event.org_aliases or [])] if s]
    cnt: Counter = Counter()
    for p in trackable_promises(event):
        for t in action_terms(p.action, exclude=scopes, limit=4):
            if len(t) >= 2 and not _VERBISH.search(t) and t not in _GENERIC:
                cnt[t] += 1
    return [t for t, _ in cnt.most_common(limit)]


def _scope(event: Event) -> list[str]:
    org = event.responsible_org or ""
    local_org = org in ADMIN_NAMES or org.endswith(("해양경찰서", "소방서", "소방본부", "경찰서"))
    return [t for t in dict.fromkeys([event.region, event.facility, org if local_org else ""]) if t]


def promise_followups(session: Session, client, event: Event, settings: Settings, embedder, today: date, limit: int = 400) -> dict:
    session.refresh(event)
    terms = promise_query_terms(event)
    event.promise_terms = terms
    stats = {"event": event.key, "promise_terms": terms, "query": "", "hits": None, "fetched": 0, "new": 0, "skipped": ""}
    scope = _scope(event)
    promises = trackable_promises(event)
    if not terms or not scope or not promises:
        stats["skipped"] = "약속 키워드 또는 범위 없음"
        session.commit()
        return stats
    starts = [a.published_at for a in (session.get(Article, p.evidence_news_id) for p in promises) if a is not None and a.published_at]
    start = min(starts) if starts else event.occurred_at
    until = today + timedelta(days=1)
    base = "(" + " OR ".join(_q(t) for t in terms) + ") AND (" + " OR ".join(_q(s) for s in scope) + ")"
    cands = [base] + ([base + " AND (" + " OR ".join(_q(k) for k in event.keywords[:2]) + ")"] if event.keywords else [])
    chosen = None
    for q in cands:
        tl = client.time_line(TimeLineArgument(query=q, published_at=PublishedAt.of(start, until), interval="month"))
        hits = sum(int(p.hits or 0) for p in tl.time_line) or int(tl.total_hits or 0)
        stats["query"], stats["hits"] = q, hits
        if hits <= limit:
            chosen = q
            break
    if chosen is None:
        stats["skipped"] = f"건수 {stats['hits']} > {limit}"
        session.commit()
        return stats
    if stats["hits"]:
        docs, _ = fetch_windowed(client, chosen, start, today, settings, mode="narrow")
        for d in docs:
            _, created = upsert_article(session, d, embedder)
            stats["fetched"] += 1
            stats["new"] += int(created)
            add_candidate(session, event, d.news_id, "promise")
    session.commit()
    return stats
