"""§4.2 원천 기사 수집 — 공공 주체의 대응 발언이 인용된 재난·안전 기사.

1. 인용문 검색 경로(주): search/quotation 에 category_incident(계열별) + 대응 발언 질의를 **월별 구간**으로 보낸다.
   return_from 상한이 1000 이라 구간당 최대 (MAX_PAGES × 100)건을 _score 내림차순(대응 발언어가 많이 겹치는 인용문 먼저)으로 받는다.
   실서버는 인용문 검색이 본문까지 매칭해 계열·월당 1만 건이 넘는다 — 전수가 아니라 점수 상위 표본이다(리포트에 잘린 구간 기록).
   source 가 공공 주체(rules/actors)이고, 규칙 어댑터가 확약·계획으로 본 발언만 남긴다.
   남은 news_id 는 뉴스 상세 조회(news_ids)로 제목·분류를 받아, 제목에 재난 어휘가 있고 해외 사건이 아닌 기사만 원천 기사로 본다.
2. 이슈 랭킹 경로(보조): issue_ranking(date) 를 날짜별로 순회하고, 토픽·토픽 키워드에 재난 어휘가 있는 토픽만 news_cluster 상세 조회로
   3계열 비율을 본다(≥ 0.3). 인용문 경로 후보와 날짜·지역·시설이 맞으면 '이슈 확인' 표시, 안 맞으면 토픽 키워드로 인용문을 한 번 더 찾는다.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ..bigkinds.codes import normalize_incident
from ..bigkinds.protocol import DEFAULT_QUOTATION_FIELDS, BigKindsClient, BigKindsError
from ..bigkinds.schemas import PublishedAt, QuotationDocument, QuotationSearchArgument
from ..llm.rules_adapter import RulesLLM
from ..llm.schemas import PromiseInput
from ..redact import mask
from ..rules.actors import is_public_actor, normalize_actor_org
from ..rules.orgs import canonical_org
from .text import casualties, find_facility, find_regions, has_disaster_vocab, has_hazard, hazard_terms, is_foreign

log = logging.getLogger("migyeol.experiment")

# §4.2-1 대응 발언 질의 (지침서 그대로)
RESPONSE_QUERY = '("하겠다" OR "할 계획" OR "추진" OR "마련" OR "전수조사" OR "보강" OR "점검" OR "설치" OR "교체" OR "재발 방지" OR "대책")'
PAGE_SIZE = 100
MAX_PAGES = 4  # (월, 계열)당 페이지 상한 — 400건. 대응 발언어가 많이 겹치는 점수 상위부터
DETAIL_BATCH = 100
DETAIL_FIELDS = ["news_id", "title", "published_at", "provider", "category", "category_incident", "provider_link_page", "content", "hilight"]
ISSUE_MIN_RATIO = 0.3
ISSUE_DETAIL_IDS = 20  # 토픽 클러스터 앞 20건으로 3계열 비율을 본다 (100건 상세 조회는 실서버에서 30초 타임아웃이 잦다)
# 원천 발언이 수사·감식 경위 설명뿐이면 대응 약속이 아니다
_INVESTIGATION_ONLY = re.compile(r"(수사|감식|입건|경위를?\s*조사|사고\s*원인을?\s*조사|조사할\s*(계획|방침|예정))")
_EXCLUDE_STRENGTH = ("의사표명", "해당없음")


@dataclass
class SourceQuote:
    news_id: str
    published: str  # YYYY-MM-DD
    source: str
    quotation: str
    org: str
    category_incident: list[str]
    incident_type: str  # 계열 (사고>산업사고 …)
    incident_detail: str  # 세부 분류 (사고>산업사고>붕괴 …)
    strength: str
    action: str
    deadline: str | None
    via: str = "quotation"  # quotation | issue
    title: str = ""
    content: str = ""
    provider: str = ""
    link: str = ""
    regions: list[str] = field(default_factory=list)
    facility: str = ""
    hazards: list[str] = field(default_factory=list)
    dead: int | None = None
    injured: int | None = None
    issue_topic: str = ""


@dataclass
class IssueEvent:
    date: str
    topic: str
    keywords: list[str]
    news_ids: list[str]
    ratio: float
    incident_type: str
    titles: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    facility: str = ""
    hazards: list[str] = field(default_factory=list)


@dataclass
class DiscoverResult:
    quotes: list[SourceQuote] = field(default_factory=list)
    issues: list[IssueEvent] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"quotes": [asdict(q) for q in self.quotes], "issues": [asdict(i) for i in self.issues], "stats": self.stats}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        return path

    @classmethod
    def load(cls, path: Path) -> "DiscoverResult":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls([SourceQuote(**q) for q in raw.get("quotes", [])], [IssueEvent(**i) for i in raw.get("issues", [])], raw.get("stats", {}))


def _merge(into: dict, part: dict) -> None:
    for k, v in part.items():
        if isinstance(v, list):
            into.setdefault(k, []).extend(v)
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            into[k] = into.get(k, 0) + v
        elif isinstance(v, dict):
            d = into.setdefault(k, {})
            for kk, vv in v.items():
                d[kk] = d.get(kk, 0) + vv
        else:
            into[k] = v


def _pmap(fn, items: list, workers: int) -> list:
    """순서를 지키는 스레드 풀 map. workers ≤ 1 이면 순차."""
    if workers <= 1 or len(items) <= 1:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, items))


def month_windows(start: date, until: date) -> list[tuple[date, date]]:
    """[start, until] 포함 범위를 달력 월 구간 (from 포함, until 제외) 으로."""
    out = []
    cur = date(start.year, start.month, 1)
    end = until + timedelta(days=1)
    while cur < end:
        nxt = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
        out.append((max(cur, start), min(nxt, end)))
        cur = nxt
    return out


def _incident(cats: list[str], prefixes: tuple[str, ...]) -> tuple[str, str]:
    """인용문 분류 목록에서 실험 계열(2단계)과 가장 구체적인 세부 분류."""
    names = [normalize_incident(c) for c in cats or []]
    hits = [n for n in names if n.startswith(prefixes)]
    if not hits:
        return "", ""
    detail = max(hits, key=lambda n: n.count(">"))
    fam = next(p for p in prefixes if detail.startswith(p))
    return ">".join(fam.split(">")[:2]), detail


def _quote_candidate(d: QuotationDocument, rules: RulesLLM, prefixes: tuple[str, ...], start: date, until: date) -> SourceQuote | None:
    pub = d.published_date
    if pub is None or not (start <= pub <= until):
        return None
    source = (d.source or "").strip()
    quotation = re.sub(r"\s+", " ", d.quotation or "").strip()
    if not source or len(quotation) < 8 or not is_public_actor(source):
        return None
    fam, detail = _incident(d.category_incident, prefixes)
    if not fam:
        return None
    res = rules.extract_promise(PromiseInput(event_summary="", responsible_org="", source=source, quotation=quotation, article_title="", published_at=pub.isoformat()))
    if not res.is_promise or res.strength in _EXCLUDE_STRENGTH or not res.action:
        return None
    if _INVESTIGATION_ONLY.search(res.action):
        return None
    org = canonical_org(normalize_actor_org(source) or "") or ""
    return SourceQuote(
        news_id=d.news_id, published=pub.isoformat(), source=source, quotation=quotation[:300], org=org,
        category_incident=[normalize_incident(c) for c in d.category_incident], incident_type=fam, incident_detail=detail,
        strength=res.strength, action=res.action, deadline=res.deadline_date,
    )


def search_quotes(client: BigKindsClient, query: str, start: date, until_excl: date, prefix: str | None, max_pages: int, stats: dict) -> list[QuotationDocument]:
    out: list[QuotationDocument] = []
    total = 0
    for page in range(max_pages):
        offset = page * PAGE_SIZE
        if offset > 1000:
            break
        try:
            res = client.search_quotation(
                QuotationSearchArgument(
                    query=query, published_at=PublishedAt.of(start, until_excl), category_incident=[prefix] if prefix else [],
                    sort={"_score": "desc"}, hilight=300, return_from=offset, return_size=PAGE_SIZE, fields=list(DEFAULT_QUOTATION_FIELDS),
                )
            )
        except BigKindsError as exc:
            stats.setdefault("errors", []).append(mask(str(exc))[:200])
            break
        total = res.total_hits
        out.extend(res.documents)
        if not res.documents or offset + len(res.documents) >= res.total_hits:
            break
    if total > len(out):
        stats.setdefault("truncated_windows", []).append(f"{start}~{until_excl}:{prefix or '*'}:{total}건 중 {len(out)}건")
    stats["quotation_total_hits"] = stats.get("quotation_total_hits", 0) + total
    return out


def attach_details(client: BigKindsClient, quotes: list[SourceQuote], stats: dict) -> list[SourceQuote]:
    """news_ids 상세 조회로 제목·본문 200자·분류를 붙이고, 재난 기사 제목이 아닌 것·해외 사건을 거른다."""
    ids = list(dict.fromkeys(q.news_id for q in quotes if not q.title))
    docs: dict[str, Any] = {}
    for i in range(0, len(ids), DETAIL_BATCH):
        try:
            res = client.get_news_by_ids(ids[i : i + DETAIL_BATCH], fields=DETAIL_FIELDS)
        except BigKindsError as exc:
            stats.setdefault("errors", []).append(mask(str(exc))[:200])
            continue
        for d in res.documents:
            docs[d.news_id] = d
    kept: list[SourceQuote] = []
    dropped = Counter()
    for q in quotes:
        d = docs.get(q.news_id)
        if d is not None and not q.title:
            q.title = re.sub(r"\s+", " ", d.title or "").strip()
            q.content = re.sub(r"\s+", " ", (d.content or d.hilight or "")).strip()[:200]
            q.provider = d.provider or ""
            link = d.provider_link_page or ""
            q.link = ("https:" + link) if link.startswith("//") else link
            if any(str(c).startswith("국제") for c in (d.category or [])) and not any(str(c).startswith(("사회", "지역")) for c in (d.category or [])):
                q.title = ""  # 국제 기사
                dropped["international"] += 1
                continue
        if not q.title:
            dropped["no_detail"] += 1
            continue
        if not has_hazard(q.title):
            dropped["title_not_hazard"] += 1
            continue
        if is_foreign(q.title):
            dropped["foreign"] += 1
            continue
        text = q.title + " " + q.content
        q.regions = find_regions(text)[:3]
        q.facility = find_facility(q.title)  # 본문 200자는 '통제선'·'지시사항' 같은 잡음이 많아 제목만
        q.hazards = hazard_terms(q.title)[:4]
        q.dead, q.injured = casualties(text)
        kept.append(q)
    stats["detail_dropped"] = dict(dropped)
    return kept


def discover_quotation_path(
    client: BigKindsClient, start: date, until: date, prefixes: tuple[str, ...], max_pages: int = MAX_PAGES, query: str = RESPONSE_QUERY, workers: int = 1
) -> DiscoverResult:
    rules = RulesLLM()
    stats: dict[str, Any] = {"windows": 0, "raw_quotations": 0, "public_promise_quotes": 0}
    tasks = [(ws, we, prefix) for ws, we in month_windows(start, until) for prefix in prefixes]

    def one(task):
        ws, we, prefix = task
        st: dict[str, Any] = {}
        docs = search_quotes(client, query, ws, we, prefix, max_pages, st)
        found = [q for q in (_quote_candidate(d, rules, prefixes, start, until) for d in docs) if q is not None]
        log.info("quotation %s %s: 인용문 %d → 후보 %d", ws.strftime("%Y-%m"), prefix, len(docs), len(found))
        return len(docs), found, st

    cands: dict[tuple[str, str], SourceQuote] = {}
    for n, found, st in _pmap(one, tasks, workers):
        stats["windows"] += 1
        stats["raw_quotations"] += n
        _merge(stats, st)
        for q in found:
            cands.setdefault((q.news_id, q.quotation[:60]), q)
    stats["public_promise_quotes"] = len(cands)
    quotes = attach_details(client, list(cands.values()), stats)
    stats["source_quotes"] = len(quotes)
    stats["source_articles"] = len({q.news_id for q in quotes})
    return DiscoverResult(quotes=quotes, stats=stats)


def _issue_day(client: BigKindsClient, d: date, prefixes: tuple[str, ...]) -> tuple[list[IssueEvent], dict[str, Any]]:
    st: dict[str, Any] = {"days": 1, "topics": 0, "disaster_topics": 0}
    out: list[IssueEvent] = []
    try:
        ranking = client.issue_ranking(d.isoformat())
    except BigKindsError as exc:
        st["errors"] = [mask(str(exc))[:200]]
        return out, st
    for topic in ranking.topics:
        st["topics"] += 1
        text = f"{topic.topic} {topic.topic_keyword}"
        if not topic.news_cluster or not has_disaster_vocab(text) or is_foreign(topic.topic):
            continue
        st["disaster_topics"] += 1
        try:
            docs = client.get_news_by_ids(topic.news_cluster[:ISSUE_DETAIL_IDS], fields=["news_id", "title", "published_at", "category_incident"]).documents
        except BigKindsError as exc:
            st.setdefault("errors", []).append(mask(str(exc))[:200])
            continue
        if not docs:
            continue
        hit = [f for f in (_incident(doc.category_incident, prefixes)[0] for doc in docs) if f]
        ratio = len(hit) / len(docs)
        if ratio < ISSUE_MIN_RATIO:
            continue
        titles = [doc.title for doc in docs[:8]]
        joined = " ".join([topic.topic, *titles])
        out.append(
            IssueEvent(
                date=d.isoformat(), topic=topic.topic, keywords=topic.keywords[:8], news_ids=list(topic.news_cluster), ratio=round(ratio, 2),
                incident_type=Counter(hit).most_common(1)[0][0], titles=titles, regions=find_regions(joined)[:3],
                facility=find_facility(topic.topic) or next((f for f in (find_facility(t) for t in titles) if f), ""), hazards=hazard_terms(joined)[:4],
            )
        )
    return out, st


def discover_issue_path(client: BigKindsClient, start: date, until: date, prefixes: tuple[str, ...], stride_days: int = 1, workers: int = 1) -> DiscoverResult:
    days = []
    d = start
    while d <= until:
        days.append(d)
        d += timedelta(days=stride_days)
    stats: dict[str, Any] = {"days": 0, "topics": 0, "disaster_topics": 0, "issue_events": 0}
    issues: list[IssueEvent] = []
    for found, st in _pmap(lambda day: _issue_day(client, day, prefixes), days, workers):
        issues.extend(found)
        _merge(stats, st)
    stats["issue_events"] = len(issues)
    log.info("issue_ranking: %d일, 재난 토픽 %d, 3계열 이슈 사건 %d", stats["days"], stats["disaster_topics"], len(issues))
    return DiscoverResult(issues=issues, stats=stats)


def issue_followup_quotes(client: BigKindsClient, issue: IssueEvent, prefixes: tuple[str, ...], until: date, stats: dict) -> list[SourceQuote]:
    """인용문 경로가 못 찾은 이슈 사건: (토픽 핵심어 AND 대응 발언어) 를 발생일~+30일에 한 번 찾는다."""
    rules = RulesLLM()
    keys = [k for k in [issue.facility, *issue.regions[:1], *issue.hazards[:1]] if k] or issue.keywords[:2]
    if not keys:
        return []
    q = "(" + " AND ".join(f'"{k}"' for k in keys[:2]) + ") AND " + RESPONSE_QUERY
    start = date.fromisoformat(issue.date)
    docs = search_quotes(client, q, start, min(start + timedelta(days=31), until + timedelta(days=1)), None, 1, stats)
    out = []
    for d in docs:
        sq = _quote_candidate(d, rules, prefixes, start, until)
        if sq is not None:
            sq.via = "issue"
            sq.issue_topic = issue.topic
            out.append(sq)
    return out


def issue_followups(client: BigKindsClient, issues: list[IssueEvent], prefixes: tuple[str, ...], until: date, stats: dict, workers: int = 1) -> list[SourceQuote]:
    def one(iss):
        st: dict[str, Any] = {}
        return issue_followup_quotes(client, iss, prefixes, until, st), st

    out: list[SourceQuote] = []
    for found, st in _pmap(one, issues, workers):
        out.extend(found)
        _merge(stats, st)
    return out


def call_plan(start: date, until: date, prefixes: tuple[str, ...], max_pages: int, issue: bool, max_events: int, stride_days: int = 1) -> dict[str, int]:
    """--dry-run: 실제 호출 없이 예상 호출 수 (§4.7). 상한(페이지 끝까지 다 받는 경우) 기준."""
    months = len(month_windows(start, until))
    days = ((until - start).days // stride_days) + 1
    plan = {
        "search_quotation(월별×계열×페이지)": months * len(prefixes) * max_pages,
        "search_news(news_ids 상세, 원천 후보)": 120,
        "issue_ranking(날짜별)": days if issue else 0,
        "search_news(news_ids 상세, 재난 토픽 ≈ 1.2/일)": int(days * 1.2) if issue else 0,
        "search_quotation(이슈 사건 보강 ≈ 0.3/일)": int(days * 0.3) if issue else 0,
        "time_line(시드 질의 건수 보정 ≈ 1.5/사건)": int(max_events * 1.5),
        "collect+extract+judge(사건당 ≈ 25)": max_events * 25,
    }
    plan["total"] = sum(plan.values())
    return plan
