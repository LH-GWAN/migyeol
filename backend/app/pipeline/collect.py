"""2. 관련 기사 수집·정제 — 연관어로 질의를 보강하고 뉴스 검색 API 를 구간별로 호출해 Article 로 저장한다.

- 초기 구간(발생일-1 ~ +14일): 키워드 트렌드(time_line)로 날짜별 건수를 보고, 상한 안이면 한 번에, 넘치면 날짜별로 상한을 고르게 배분한다.
- 이후 구간(그 이후 ~ 오늘), 3차 검증 1-4: 판정에 필요한 기사를 **직접 찾는 질의**를 쓴다.
    조치 상태 질의 = ("완료" OR "준공" OR "개통" OR "착공" OR …) AND (시설·핵심어) AND (지역·기관)
  범위가 좁아 대부분 상한 안에 들어오고, 넘칠 때만 월 단위(큰 달은 상·중·하순)로 나눈다.
  그 밖의 일반 후속 보도는 분기별 소량 표본(collect_tail_sample, 0 이면 생략)만 받는다 — 보도량 추이는 time_line 이 이미 제공한다.
  고른 표본 추출(월별 물 채우기)은 큰 사건에서 호출량만 늘리고 완료·착공 기사를 보장하지 못해 버렸다.
- 거의 같은 기사(전재)는 상한을 소모하지 않는다. 쓰지 못한 예산은 잘린 구간에 이어 받는다.
- 수집한 기사는 (event_id, news_id, 질의 종류) 로 EventCandidate 에 기록해 link 단계의 후보로 쓴다.
  이번 실행의 시드 질의에서 빠진 후보(시드를 좁힌 경우)는 지운다. 0건 수집이면 지우지 않고 경고한다.
- 사람 지정 질의(query_override)는 연관어 확장을 하지 않는다.
- 시드에 occurrence_query 가 있으면 발생 창(발생−1 ~ +14일)만 그 질의로 한 페이지 더 받는다 (후보 출처 'occurrence', collect_occurrence).
- 정렬은 지침서의 객체 형식(`{"date": "asc"}`) 만 쓴다 (실서버는 리스트 형식도 받지만 쓰지 않는다).
"""

from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from difflib import SequenceMatcher

from sqlalchemy.orm import Session

from ..bigkinds.codes import normalize_incident
from ..bigkinds.protocol import DEFAULT_NEWS_FIELDS, BigKindsClient
from ..bigkinds.schemas import NewsDocument, NewsSearchArgument, PublishedAt, TimeLineArgument, WordCloudArgument
from ..config import Settings
from ..db.models import Article, Event, EventCandidate
from .embeddings import EmbeddingBackend, needs_recompute, to_bytes
from .seeds import Seed, _quote, expand_query, seed_from_event

log = logging.getLogger("migyeol.collect")

# 연관어 확장에서 제외하는 일반어
_STOP = {
    "사고", "차량", "주민", "발생", "지역", "관계자", "오전", "오후", "이날", "구조", "당국", "대책", "점검",
    "조사", "결과", "피해", "안전", "시민", "현장", "부상", "사망", "사람", "이번", "지난", "올해", "내년",
}
_SORT = {"date": "asc"}  # 지침서 형식. 구간 상한이 한 페이지(≤ PAGE_SIZE) 안이라 동률 페이징 문제가 생기지 않는다
PAGE_SIZE = 300  # 지침서 return_size 상한(10000) 안. 총상한(300)까지 한 페이지로 받아 페이징 경계의 동률 문제를 피한다
INITIAL_DAYS = 15  # 발생일 ~ +14일
# 조치 상태 질의의 행동어 — 완료·진행·재발 기사를 직접 찾는다
STATUS_TERMS = ("완료", "준공", "개통", "착공", "설치", "공사", "재발", "또", "마무리", "교체", "보강", "복구", "점검")


def article_text(a: Article) -> str:
    return " ".join([a.title or "", a.hilight or "", a.content_200 or "", " ".join(a.tms_keywords or [])]).replace("\n", " ")


def _clean_hilight(s: str | None) -> str | None:
    if s is None:
        return None
    import re

    return re.sub(r"</?b>", "", s)[:200]


def _absolute_link(url: str | None) -> str | None:
    """실서버는 provider_link_page 를 '//www.example.com/...' 처럼 스킴 없이 돌려주기도 한다 (첫 실호출 확인). 화면 링크용으로 https 를 붙인다."""
    if not url:
        return url
    u = url.strip()
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("www."):
        return "https://" + u
    return u


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


def fetch_articles(
    client: BigKindsClient, query: str, start: date, until: date, cap: int, start_offset: int = 0
) -> tuple[list[NewsDocument], int, bool, int]:
    """[start, until) 구간을 날짜 오름차순으로 cap 건까지. 반환: (문서, total_hits, 잘림 여부, 다음 offset).

    거의 같은 기사(전재)는 건너뛰고 상한을 소모하지 않는다 — 상한은 저장할 기사 수 기준이다.
    start_offset 을 주면 그 위치부터 이어 받는다 (남은 예산 이월용).
    """
    docs: list[NewsDocument] = []
    offset = start_offset
    total = 0
    while len(docs) < cap:
        res = client.search_news(
            NewsSearchArgument(
                query=query,
                published_at=PublishedAt.of(start, until),
                sort=dict(_SORT),
                hilight=200,
                return_from=offset,
                return_size=min(PAGE_SIZE, cap - len(docs)),
                fields=list(DEFAULT_NEWS_FIELDS),
            )
        )
        total = res.total_hits
        for d in res.documents:
            if len(docs) >= cap:
                break
            if any(d.news_id == k.news_id for k in docs) or _near_duplicate(d, docs):
                continue
            docs.append(d)
        offset += len(res.documents)
        if not res.documents or offset >= res.total_hits:
            break
    return docs, total, total > offset, offset


def _month_windows(start: date, end_exclusive: date) -> list[tuple[date, date]]:
    out = []
    cur = start
    while cur < end_exclusive:
        nxt = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
        out.append((cur, min(nxt, end_exclusive)))
        cur = nxt
    return out


def _day_windows(start: date, end_exclusive: date) -> list[tuple[date, date]]:
    return [(start + timedelta(days=i), start + timedelta(days=i + 1)) for i in range((end_exclusive - start).days)]


def _bucket_label(start: date, interval: str) -> str:
    if interval == "day":
        return start.strftime("%Y%m%d")
    if interval == "quarter":
        return f"{start.year:04d}{((start.month - 1) // 3) * 3 + 1:02d}"
    return start.strftime("%Y%m")


def _decades(start: date, end_exclusive: date) -> list[tuple[date, date]]:
    """구간을 셋으로 나눈다. 한 달 안이면 상·중·하순, 더 길면 길이를 삼등분한다 (앞쪽 기사만 받지 않도록)."""
    span = (end_exclusive - start).days
    if span <= 0:
        return []
    if span <= 31 and start.month == (end_exclusive - timedelta(days=1)).month:
        cuts = [start, start.replace(day=11), start.replace(day=21), end_exclusive]
    else:
        third = max(1, span // 3)
        cuts = [start, start + timedelta(days=third), start + timedelta(days=2 * third), end_exclusive]
    out = []
    for a, b in zip(cuts, cuts[1:]):
        a, b = max(a, start), min(b, end_exclusive)
        if a < b and (not out or a >= out[-1][1]):
            out.append((a, b))
    return out


def _quarter_windows(start: date, end_exclusive: date) -> list[tuple[date, date]]:
    out = []
    cur = start
    while cur < end_exclusive:
        qm = ((cur.month - 1) // 3) * 3 + 1
        nxt_m = qm + 3
        nxt = date(cur.year + (nxt_m > 12), (nxt_m - 1) % 12 + 1, 1)
        out.append((cur, min(nxt, end_exclusive)))
        cur = nxt
    return out


def allocate(hits: dict[str, int], budget: int, per_bucket_cap: int | None = None) -> dict[str, int]:
    """물 채우기 배분: 작은 구간은 전부, 큰 구간은 남은 예산을 고르게 나눠 받는다. 구간별 상한(per_bucket_cap)도 적용."""
    keys = [k for k, h in hits.items() if h > 0]
    out: dict[str, int] = {}
    remaining = max(0, budget)
    for i, k in enumerate(sorted(keys, key=lambda k: hits[k])):
        share = remaining // (len(keys) - i) if len(keys) - i else 0
        take = min(hits[k], share)
        if per_bucket_cap is not None:
            take = min(take, per_bucket_cap)
        out[k] = take
        remaining -= take
    return out


def _fetch_bucket(
    client: BigKindsClient, query: str, a: date, b: date, cap: int, split_big_month: bool
) -> tuple[list[NewsDocument], int, bool, int, list[tuple[date, date, int]]]:
    """한 구간을 cap 까지. 큰 달(interval=month)은 상·중·하순으로 나눠 cap 을 고르게 쓰고 남는 몫은 다음 순으로 넘긴다.

    반환: (문서, total_hits, 잘림, 호출 수, 이어받기 지점 [(구간 시작, 끝, 다음 offset)] — 잘린 부분 구간만).
    """
    if not split_big_month:
        docs, total, truncated, nxt = fetch_articles(client, query, a, b, cap)
        return docs, total, truncated, 1, ([(a, b, nxt)] if truncated else [])
    parts = _decades(a, b)
    docs: list[NewsDocument] = []
    total = 0
    truncated = False
    calls = 0
    remaining = cap
    resume: list[tuple[date, date, int]] = []
    for i, (pa, pb) in enumerate(parts):
        share = math.ceil(remaining / (len(parts) - i)) if remaining > 0 else 0  # 올림: cap 4 → 2/1/1 (하순 치우침 방지)
        if share <= 0:
            continue
        got, t, tr, nxt = fetch_articles(client, query, pa, pb, share)
        calls += 1
        total += t
        truncated = truncated or tr
        if tr:
            resume.append((pa, pb, nxt))
        docs.extend(got)
        remaining -= len(got)
    return docs, total, truncated, calls, resume


def fetch_balanced(
    client: BigKindsClient,
    query: str,
    start: date,
    end: date,
    budget: int,
    interval: str,
    per_bucket_cap: int | None = None,
    month_hits: dict[str, int] | None = None,
) -> tuple[list[NewsDocument], int, list[str], int, list[str]]:
    """[start, end) 를 time_line 으로 먼저 보고, 예산 안이면 한 번에, 넘치면 날짜/월 구간별로 배분해 받는다.

    - time_line 이 0건이면 search 1회(return_size=1)로 교차 확인한다 (일시 장애·빈 응답으로 조용히 0건이 되지 않게).
    - time_line 이 실패하면 단일 구간 수집으로 대체하고 경고를 남긴다.
    - 상한을 넘는 달은 상·중·하순으로 나눠 받아 월초 기사만 들어오지 않게 한다. 전재만 많은 구간에서 쓰지 못한 예산은 잘린 다른 구간에 이월한다.
    반환: (문서, total_hits, 잘린 구간 설명, API 호출 수, 경고).
    """
    warnings: list[str] = []
    if start >= end or budget <= 0:
        return [], 0, [], 0, warnings
    calls = 0
    tl_interval = "month" if interval == "quarter" else interval
    try:
        if month_hits is not None and interval in ("month", "quarter"):
            hits = dict(month_hits)  # 호출자가 이미 받은 월 건수 재사용 (time_line 호출 생략)
        else:
            calls += 1
            tl = client.time_line(TimeLineArgument(query=query, published_at=PublishedAt.of(start, end), interval=tl_interval))
            hits = {p.label: int(p.hits or 0) for p in tl.time_line}
        if interval == "quarter":  # 월 건수를 분기로 묶는다
            q: dict[str, int] = {}
            for lb, h in hits.items():
                y, m = int(lb[:4]), int(lb[4:6])
                k = f"{y:04d}{((m - 1) // 3) * 3 + 1:02d}"
                q[k] = q.get(k, 0) + h
            hits = q
        total = sum(hits.values()) if hits else int(tl.total_hits or 0)
    except Exception as exc:  # noqa: BLE001 — 트렌드 실패는 단일 구간 수집으로 대체
        calls = max(calls, 1)
        warnings.append(f"time_line 실패({type(exc).__name__}) — 구간 {start}~{end} 를 한 번에 수집")
        log.warning(warnings[-1])
        hits, total = {}, -1
    if total == 0:
        if hits:
            return [], 0, [], calls, warnings  # 트렌드가 정상 형식(시리즈 있음)으로 0건이면 그대로 믿는다 — 교차 확인 호출을 아낀다
        # 응답이 비었으면(시리즈 없음) 검색 1회로 교차 확인한다 — 일시 장애·빈 응답으로 조용히 0건이 되지 않게
        probe = client.search_news(NewsSearchArgument(query=query, published_at=PublishedAt.of(start, end), sort=dict(_SORT), return_size=1, fields=["news_id"]))
        calls += 1
        if probe.total_hits > 0:
            warnings.append(f"time_line 0건이지만 검색 total_hits={probe.total_hits} — 구간 {start}~{end} 를 대체 수집")
            log.warning(warnings[-1])
            hits, total = {}, -1
        else:
            return [], 0, [], calls, warnings
    if total < 0:
        # 트렌드 없이 수집: 날짜 오름차순 한 덩어리가 아니라 상·중·하순처럼 셋으로 나눠 받는다 (1-A 이전 동작으로 돌아가지 않게)
        docs, got_total, truncated, ncalls, _ = _fetch_bucket(client, query, start, end, budget, split_big_month=True)
        calls += ncalls
        return docs, got_total, ([f"{start}~{end}:{got_total}건 중 {len(docs)}건"] if truncated else []), calls, warnings
    if total <= budget:
        docs, got_total, truncated, _ = fetch_articles(client, query, start, end, budget)
        calls += 1
        return docs, got_total, ([f"{start}~{end}:{got_total}건 중 {len(docs)}건"] if truncated else []), calls, warnings

    windows = _day_windows(start, end) if interval == "day" else (_quarter_windows(start, end) if interval == "quarter" else _month_windows(start, end))
    labelled = [(a, b, _bucket_label(a, interval)) for a, b in windows]
    plan = allocate({lb: hits.get(lb, 0) for _, _, lb in labelled}, budget, per_bucket_cap)
    docs: list[NewsDocument] = []
    seen: set[str] = set()
    truncated_info: list[str] = []
    resume: list[tuple[date, date, int]] = []  # 잘린 (부분)구간: (시작, 끝, 다음 offset) — 예산 이월용
    got_by_bucket: dict[str, int] = {}
    spent = 0
    for a, b, lb in labelled:
        cap = plan.get(lb, 0)
        bucket_hits = hits.get(lb, 0)
        if cap <= 0:
            if bucket_hits > 0:
                truncated_info.append(f"{a}~{b}:{bucket_hits}건 중 0건")
                resume.append((a, b, 0))
            continue
        big = interval == "month" and bucket_hits > cap
        got, bucket_total, truncated, ncalls, parts = _fetch_bucket(client, query, a, b, cap, split_big_month=big)
        calls += ncalls
        for d in got:
            if d.news_id not in seen:
                seen.add(d.news_id)
                docs.append(d)
        got_by_bucket[lb] = len(got)
        spent += len(got)
        if truncated:
            truncated_info.append(f"{a}~{b}:{bucket_total}건 중 {len(got)}건")
            resume.extend(parts)
    # 예산 이월: 전재만 많아 못 쓴 예산을 잘린 (부분)구간에 고르게 나눠 이어 받는다
    leftover = budget - spent
    if leftover > 0 and resume:
        extra: dict[str, int] = {}
        for i, (a, b, off) in enumerate(resume):
            share = leftover // (len(resume) - i)
            lb0 = _bucket_label(a, interval) if interval == "day" else _bucket_label(a.replace(day=1), interval)
            if per_bucket_cap is not None:
                share = min(share, per_bucket_cap - got_by_bucket.get(lb0, 0) - extra.get(lb0, 0))  # 이월도 구간 상한을 넘지 않는다
            if share <= 0:
                continue
            got, _, _, _ = fetch_articles(client, query, a, b, share, start_offset=off)
            calls += 1
            added = 0
            for d in got:
                if d.news_id not in seen:
                    seen.add(d.news_id)
                    docs.append(d)
                    added += 1
            leftover -= added
            extra[lb0] = extra.get(lb0, 0) + added
        if extra:
            truncated_info = []
            for a, b, lb in labelled:
                n = got_by_bucket.get(lb, 0) + extra.get(lb, 0)
                if hits.get(lb, 0) > n:
                    truncated_info.append(f"{a}~{b}:{hits[lb]}건 중 {n}건")
    return docs, total, truncated_info, calls, warnings


def status_query(base_query: str) -> str:
    """조치 상태 질의: (행동어 OR …) AND (<기본 질의>). 기본 질의는 '(주제) AND (범위)' 또는 사람 지정 질의."""
    return "(" + " OR ".join(_quote(list(STATUS_TERMS))) + ") AND (" + base_query + ")"


def fetch_windowed(
    client: BigKindsClient, query: str, occurred: date, today: date, settings: Settings, mode: str = "seed"
) -> tuple[list[NewsDocument], dict]:
    """mode="seed": 초기 구간(날짜별 배분, 상한 collect_initial_cap) + 이후 구간.
         이후 구간은 기본 질의의 월별 트렌드를 먼저 본다 — 남은 예산 안이면 한 번에 받고(조용한 사건: 호출 4회),
         넘치면 조치 상태 질의(월별, 월 상한 collect_month_cap)로 판정 기사를 직접 찾고 일반 후속 보도는 분기별 표본
         (collect_tail_sample, 트렌드 재사용)만 받는다. 총상한 max_articles_per_event. total_hits 는 초기 + 이후 기본 질의 합.
       mode="narrow": 약속 재검색처럼 이미 좁은 질의 — 전체 기간을 월 단위 배분으로 받는다 (초기 구간·표본 없음)."""
    until = today + timedelta(days=1)  # until 은 제외이므로 오늘을 포함하려면 내일
    info: dict = {"windows": 0, "total_hits": 0, "truncated_windows": [], "api_calls": 0, "warnings": [], "queries": {}}
    docs: list[NewsDocument] = []
    seen: set[str] = set()

    def _run(label: str, q: str, start: date, end: date, budget: int, interval: str, cap: int | None = None, month_hits: dict | None = None, count_total: bool = True) -> int:
        if start >= end or budget <= 0:
            return 0
        got, total, trunc, calls, warns = fetch_balanced(client, q, start, end, budget, interval, per_bucket_cap=cap, month_hits=month_hits)
        for d in got:
            if d.news_id not in seen:
                seen.add(d.news_id)
                docs.append(d)
        info["windows"] += 1
        if count_total:
            info["total_hits"] += max(total, 0)
        info["truncated_windows"] += trunc
        info["api_calls"] += calls
        info["warnings"] += warns
        info["queries"][label] = {"total_hits": max(total, 0), "fetched": len(got), "api_calls": calls}
        return max(total, 0)

    if mode == "narrow":
        _run("narrow", query, occurred, until, settings.max_articles_per_event, "month", cap=settings.collect_month_cap)
    else:
        init_start = occurred - timedelta(days=1)
        init_end = min(occurred + timedelta(days=INITIAL_DAYS), until)
        _run("initial", query, init_start, init_end, min(settings.collect_initial_cap, settings.max_articles_per_event), "day")
        tail_start = occurred + timedelta(days=INITIAL_DAYS)
        if tail_start < until:
            remaining = settings.max_articles_per_event - len(docs)
            month_hits: dict[str, int] | None = None
            tail_total = -1
            try:
                info["api_calls"] += 1
                tl = client.time_line(TimeLineArgument(query=query, published_at=PublishedAt.of(tail_start, until), interval="month"))
                month_hits = {p.label: int(p.hits or 0) for p in tl.time_line}
                tail_total = sum(month_hits.values()) if month_hits else int(tl.total_hits or 0)
            except Exception as exc:  # noqa: BLE001
                info["warnings"].append(f"time_line 실패({type(exc).__name__}) — 이후 구간을 상태 질의와 표본으로 수집")
            if 0 <= tail_total <= remaining and month_hits:
                # 조용한 사건: 기본 질의로 한 번에 (상태 질의 ⊂ 기본 질의)
                _run("tail", query, tail_start, until, remaining, "month", month_hits=month_hits)
            else:
                _run("status", status_query(query), tail_start, until, remaining, "month", cap=settings.collect_month_cap, count_total=False)
                remaining = settings.max_articles_per_event - len(docs)
                sample = min(settings.collect_tail_sample, remaining)
                if sample > 0:
                    _run("sample", query, tail_start, until, sample, "quarter", month_hits=month_hits)
                elif tail_total >= 0:
                    info["total_hits"] += tail_total
                else:
                    info["total_hits"] += info["queries"].get("status", {}).get("total_hits", 0)
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
    art.provider_link_page = _absolute_link(doc.provider_link_page) or art.provider_link_page
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
    current = {d.news_id for d in docs}
    for d in docs:
        _, created = upsert_article(session, d, embedder)
        new += int(created)
        add_candidate(session, event, d.news_id, "seed")
    # 시드를 좁히면 이전 질의로 들어온 후보는 더 이상 이 사건의 후보가 아니다 (link 단계가 후보 없는 자동 연결을 지운다).
    # 단, 이번 수집이 0건이면(장애 가능성) 후보를 지우지 않는다.
    stale = 0
    if docs:
        for c in session.query(EventCandidate).filter_by(event_id=event.id, source="seed").all():
            if c.news_id not in current:
                session.delete(c)
                stale += 1
    else:
        info["warnings"].append("수집 0건 — 기존 후보를 유지" + (" (시드 query 지정 — 오타 확인)" if seed.query else ""))
    info["stale_candidates_removed"] = stale
    if seed.occurrence_query:
        info["occurrence"] = collect_occurrence(session, client, event, seed.occurrence_query, embedder, today)
        new += info["occurrence"]["new"]
    session.commit()
    stats = {"event": event.key, "query": query, "expansions": expansions, "fetched": len(docs), "new": new, **info}
    log.info("collect %s", stats)
    return stats


OCCURRENCE_CAP = 300  # 발생 창 보강 질의는 한 페이지만 받는다 (사건당 호출 1회)


def collect_occurrence(
    session: Session, client: BigKindsClient, event: Event, query: str, embedder: EmbeddingBackend | None, today: date
) -> dict:
    """발생 창 보강 — 시드 질의가 사고 당일·직후 보도를 놓친 사건(첫 보도에는 시설명이 아직 없다)에서 [발생−1, 발생+14] 만 넓은 질의로 더 받는다.

    후보 출처는 'occurrence'. 시드 질의 후보와 달리 다음 수집에서 지우지 않는다(같은 질의면 같은 기사). 연결 여부는 link 의 게이트·점수가 정한다.
    """
    start = event.occurred_at - timedelta(days=1)
    until = min(event.occurred_at + timedelta(days=INITIAL_DAYS), today + timedelta(days=1))
    docs, total, truncated, _ = fetch_articles(client, query, start, until, OCCURRENCE_CAP)
    new = 0
    for d in docs:
        _, created = upsert_article(session, d, embedder)
        new += int(created)
        add_candidate(session, event, d.news_id, "occurrence")
    return {"query": query, "total_hits": total, "fetched": len(docs), "new": new, "truncated": truncated}


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
    from .judge import trackable_promises

    for p in trackable_promises(event):
        terms = action_terms(p.action, exclude=scopes, limit=3)
        if not terms:
            continue
        terms_all.extend(t for t in terms if t not in terms_all)
        evidence = session.get(Article, p.evidence_news_id)
        start = evidence.published_at if evidence and evidence.published_at else event.occurred_at
        query = "(" + " OR ".join(_quote(terms)) + ") AND (" + " OR ".join(_quote(scopes)) + ")"
        docs, info = fetch_windowed(client, query, start, today, settings, mode="narrow")
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
