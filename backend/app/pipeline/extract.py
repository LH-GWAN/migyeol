"""4. 약속 구조화 — 인용문 검색 API 로 대응 주체의 발언을 모으고 LLM(역할 ⓐ)으로 주체·행동·기한·발언 강도를 추출한다.

- 인용문 질의는 주제어·기관명 OR 로 넓게 보내고(인용문 텍스트만 검색될 수 있음) 사건에 연결된 news_id 로 거른다.
- 공공 주체 판별은 규칙(actors.py)이 먼저 거르고, 통과한 인용문만 LLM 에 보낸다.
- LLM 호출은 DB 쓰기 트랜잭션 밖에서 한다: 인용문 저장·commit -> LLM 호출 -> 결과 저장·commit.
- 실패(LLMFailure)한 인용문은 저장하지 않고 다음 실행에서 다시 시도한다. 같은 (주체, 발언) 은 한 번만 호출한다.
- strength ∈ {확약, 계획} 이고 action 이 있으면 is_trackable=True. 중복 약속은 가장 구체적인 것(확약·기한 있음·이른 기한)이 대표.
"""

from __future__ import annotations

import calendar
import logging
import re
from datetime import date, timedelta

import numpy as np
from sqlalchemy.orm import Session

from ..bigkinds.protocol import DEFAULT_QUOTATION_FIELDS, BigKindsClient
from ..bigkinds.schemas import PublishedAt, QuotationDocument, QuotationSearchArgument
from ..config import Settings
from ..db.models import Article, Event, Promise, Quotation
from ..llm.client import PROMPT_VERSIONS, LLMClient, LLMFailure
from ..llm.schemas import PromiseInput
from ..rules.actors import is_public_actor, normalize_actor_org
from .embeddings import EmbeddingBackend
from .seeds import _quote, seed_from_event

log = logging.getLogger("migyeol.extract")

QUOTE_CHUNK_DAYS = 30
QUOTE_CHUNK_CAP = 1000  # API return_from 한도


def quotation_query(event: Event) -> str:
    """사람 지정 질의가 있으면 그대로, 아니면 (주제어 OR 책임기관 OR 별칭). 지역명은 뺀다(너무 넓음)."""
    seed = seed_from_event(event)
    if seed.query:
        return seed.query
    terms = _quote([*seed.subjects(), seed.responsible_org, *seed.org_aliases])
    return "(" + " OR ".join(terms) + ")" if terms else ""


def fetch_quotations(client: BigKindsClient, query: str, start: date, until: date) -> tuple[list[QuotationDocument], list[str]]:
    """30일 단위로 나눠 페이징한다(창당 1000건 한도). 반환: (문서, 잘린 창 목록)."""
    out: list[QuotationDocument] = []
    truncated: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    cur = start
    while cur < until:
        end = min(cur + timedelta(days=QUOTE_CHUNK_DAYS), until)
        offset = 0
        total = 0
        while offset < QUOTE_CHUNK_CAP:
            res = client.search_quotation(
                QuotationSearchArgument(
                    query=query,
                    published_at=PublishedAt.of(cur, end),
                    sort={"date": "asc"},  # 지침서 형식. 다중 정렬 리스트는 실서버 확인 전 (smoke_live.py)
                    hilight=300,
                    return_from=offset,
                    return_size=100,
                    fields=list(DEFAULT_QUOTATION_FIELDS),
                )
            )
            total = res.total_hits
            for d in res.documents:
                key = (d.news_id, d.source, d.quotation)
                if key not in seen:
                    seen.add(key)
                    out.append(d)
            offset += len(res.documents)
            if not res.documents or offset >= res.total_hits:
                break
        if total > offset:
            truncated.append(f"{cur}~{end}:{total}건 중 {offset}건")
        cur = end
    return out, truncated


_DEADLINE_PATTERNS = (
    re.compile(r"^(\d{4})[-./](\d{1,2})[-./](\d{1,2})"),  # 2026-06-30, 2026.06.30, 2026/6/30
    re.compile(r"^(\d{4})[-./](\d{1,2})(?![\d])"),  # 2026-06, 2026.6
    re.compile(r"^(\d{4})(\d{2})(\d{2})$"),  # 20260630
    re.compile(r"^(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일"),  # 2026년 6월 30일
    re.compile(r"^(\d{4})년\s*(\d{1,2})월"),  # 2026년 6월
    re.compile(r"^(\d{4})년?(?![\d])"),  # 2026, 2026년
)
_DEADLINE_MAX_YEARS_AHEAD = 15


def parse_deadline(raw: str | None, published: date | None = None) -> tuple[date | None, str | None]:
    """LLM 이 낸 기한 문자열 -> date. 'YYYY-MM' 은 말일, 'YYYY' 는 12-31, 없는 날짜(2026-06-31)는 그 달 말일.
    'YYYY.MM.DD' / 'YYYYMMDD' / 'YYYY년 M월 D일' 도 받는다. 발언일보다 앞선 기한, 터무니없는 연도(0000, 발언 15년 뒤)는 무효.
    반환: (date|None, 무효 사유|None). 예외를 던지지 않는다 (사건 extract 전체를 롤백시키지 않기 위해)."""
    if not raw:
        return None, None
    s = str(raw).strip()
    m = None
    for pat in _DEADLINE_PATTERNS:
        m = pat.match(s)
        if m:
            break
    if not m:
        return None, f"기한 형식 인식 불가: {s}"
    groups = m.groups()
    try:
        y = int(groups[0])
        mo = int(groups[1]) if len(groups) > 1 and groups[1] else 12
        if not 1 <= mo <= 12:
            return None, f"기한 월 범위 밖: {s}"
        last = calendar.monthrange(y, mo)[1]
        d = int(groups[2]) if len(groups) > 2 and groups[2] else last
        d = min(max(d, 1), last)
        out = date(y, mo, d)
    except (ValueError, OverflowError):
        return None, f"기한 날짜 해석 불가: {s}"
    base = published or date.today()
    if y < 2000 or y > base.year + _DEADLINE_MAX_YEARS_AHEAD:
        return None, f"기한 연도 범위 밖: {s}"
    if published and out < published:
        return None, f"기한({out})이 발언일({published})보다 앞섬"
    return out, None


def _rank(p: Promise) -> tuple:
    """병합 대표 선정: 확약 > 계획, 기한 있음 > 없음, 이른 기한, 먼저 생성된 것."""
    return (0 if p.strength == "확약" else 1, 0 if p.deadline_date else 1, p.deadline_date or date.max, p.id)


def _merge_duplicates(event: Event, embedder: EmbeddingBackend | None) -> int:
    """같은 기관 + 유사 행동(코사인 ≥ 0.85 또는 동일)은 하나로 합친다. 근거 news_id 는 모두 보존."""
    merged = 0
    for p in event.promises:
        if p.merged_into_id is not None and p.strength in ("확약", "계획") and p.action:
            p.is_trackable, p.merged_into_id = True, None  # 다시 대표를 고른다 (새 약속이 더 구체적일 수 있다)
    promises = sorted([p for p in event.promises if p.is_trackable], key=_rank)
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
            # 대표가 바뀌어도 근거 news_id 와 '어디로 병합됐는지' 를 잃지 않는다
            ids = list(dup.merged_news_ids or [])
            for nid in [p.evidence_news_id, *(p.merged_news_ids or [])]:
                if nid and nid not in ids and nid != dup.evidence_news_id:
                    ids.append(nid)
            dup.merged_news_ids = ids
            for other in event.promises:
                if other.merged_into_id == p.id:
                    other.merged_into_id = dup.id
            p.merged_news_ids = []
            p.is_trackable = False
            p.merged_into_id = dup.id
            merged += 1
    return merged


def extract_event(
    session: Session,
    client: BigKindsClient,
    event: Event,
    llm: LLMClient,
    settings: Settings,
    embedder: EmbeddingBackend | None = None,
    call_budget: dict | None = None,
) -> dict:
    session.refresh(event)
    linked_ids = {l.news_id for l in event.links if l.is_active}
    start = event.occurred_at
    until = event.occurred_at + timedelta(days=settings.quotation_window_days)
    query = quotation_query(event)
    docs, truncated = fetch_quotations(client, query, start, until) if query else ([], [])
    aliases = [event.responsible_org, *(event.org_aliases or [])]
    existing = {(q.news_id, q.source, q.quotation): q for q in event.quotations}
    stats = {
        "event": event.key, "quotes": len(docs), "linked_quotes": 0, "public": 0, "llm_calls": 0, "llm_failed": 0,
        "promises": 0, "merged": 0, "truncated_windows": truncated, "deadline_rejected": 0,
    }

    # 1) 인용문 저장 (짧은 트랜잭션)
    pending: list[Quotation] = []
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
            existing[key] = q
        if q.is_public_actor:
            stats["public"] += 1
            if not q.promises:
                pending.append(q)
    session.commit()

    # 2) LLM 호출 (DB 쓰기 없음). 같은 (주체, 발언) 은 한 번만.
    memo: dict[tuple[str, str], object] = {}
    results: list[tuple[Quotation, object]] = []
    for q in pending:
        mkey = (q.source.strip(), re.sub(r"\s+", " ", q.quotation.strip()))
        if mkey in memo:
            results.append((q, memo[mkey]))
            continue
        if call_budget is not None and call_budget.get("remaining", 1) <= 0:
            stats.setdefault("deferred", 0)
            stats["deferred"] += 1
            continue
        article = session.get(Article, q.news_id)
        inp = PromiseInput(
            event_summary=event.summary or event.title,
            responsible_org=event.responsible_org,
            source=q.source,
            quotation=q.quotation,
            article_title=(article.title if article else "") or "",
            published_at=(q.published_at or event.occurred_at).isoformat(),
        )
        stats["llm_calls"] += 1
        if call_budget is not None:
            call_budget["remaining"] = call_budget.get("remaining", 0) - 1
        try:
            res = llm.extract_promise(inp, key=f"{q.news_id}||{q.source}||{q.quotation}")
        except LLMFailure as exc:
            stats["llm_failed"] += 1
            log.warning("extract LLM 실패(다음 실행에서 재시도) %s: %s", event.key, exc)
            continue
        memo[mkey] = res
        results.append((q, res))

    # 3) 결과 저장
    for q, res in results:
        actor = normalize_actor_org(q.source, event.responsible_org, aliases) or res.actor_org or ""
        deadline, why = parse_deadline(res.deadline_date, q.published_at)
        if why:
            stats["deadline_rejected"] += 1
            log.warning("기한 무효 처리 %s: %s", event.key, why)
        p = Promise(
            event_id=event.id,
            quotation_id=q.id,
            actor_org=actor,
            actor_raw=res.actor_raw or q.source,
            action=res.action or "",
            deadline_raw=res.deadline_raw,
            deadline_date=deadline,
            deadline_precision=res.deadline_precision if deadline else "none",
            strength=res.strength,
            is_trackable=bool(res.is_promise and res.strength in ("확약", "계획") and res.action),
            evidence_news_id=q.news_id,
            confidence=res.confidence,
            rationale=(res.rationale or "") + (f" [기한 무효: {why}]" if why else ""),
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
