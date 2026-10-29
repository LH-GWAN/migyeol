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
from ..rules import actor_search
from ..rules.actors import is_public_actor, looks_like_person, normalize_actor_org
from ..rules.orgs import canonical_org
from .judge import clear_llm_failure, note_llm_failure, occurrence_gate_link
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
    re.compile(r"^(\d{4})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})"),  # 2026-06-30, 2026.06.30, 2026. 6. 30., 2026/6/30
    re.compile(r"^(\d{4})\s*[-./]\s*(\d{1,2})(?![\d])"),  # 2026-06, 2026.6, 2026. 6
    re.compile(r"^(\d{4})(\d{2})(\d{2})$"),  # 20260630
    re.compile(r"^(\d{4})\s+(\d{1,2})\s+(\d{1,2})"),  # 2026 6 30 (공백만)
    re.compile(r"^(\d{4})\s+(\d{1,2})(?![\d])"),  # 2026 6
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
    """같은 기관 + 유사 행동(코사인 ≥ 0.85 또는 동일)은 하나로 합친다. 근거 news_id 는 모두 보존.
    대표는 근거 기사가 활성 연결인 약속을 우선한다 (대표 근거만 해제돼도 약속 전체가 추적에서 빠지지 않게)."""
    merged = 0
    active = {l.news_id for l in event.links if l.is_active}
    for p in event.promises:
        if p.merged_into_id is not None and p.strength in ("확약", "계획") and p.action:
            p.is_trackable, p.merged_into_id = True, None  # 다시 대표를 고른다 (새 약속이 더 구체적일 수 있다)
    promises = sorted([p for p in event.promises if p.is_trackable], key=lambda p: (0 if p.evidence_news_id in active else 1, *_rank(p)))
    kept: list[Promise] = []
    vecs: dict[int, np.ndarray] = {}
    if embedder is not None and promises:
        for p, v in zip(promises, embedder.encode([p.action for p in promises])):
            vecs[p.id] = v
    for p in promises:
        dup = None
        for k in kept:
            if k.actor_org != p.actor_org or not p.actor_org:
                continue  # 기관 미상("")은 서로 다른 기관일 수 있어 병합하지 않는다
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


def count_pending_quotations(event: Event) -> int:
    active = {l.news_id for l in event.links if l.is_active}
    return sum(1 for q in event.quotations if q.is_public_actor and not q.promises and q.news_id in active)


def resolve_person_title(event: Event, source: str, published: date | None, lexicon: list[str]) -> str | None:
    """'김동연 지사' 처럼 기관명 없는 이름+직함 주체 → 뉴스 검색 캐시로 찾은 기관 (rules/actor_search.py). 없거나 보류면 None.
    찾은 기관이 사건 책임기관과 같은 기관이면 책임기관 표기를 쓴다 (병합 키 일관성)."""
    org = actor_search.lookup(source, published, event.region, lexicon)
    if org and event.responsible_org and canonical_org(event.responsible_org) == org:
        return event.responsible_org
    return org


def store_promise(event: Event, q: Quotation, res, llm: LLMClient, lexicon: list[str], stats: dict | None = None) -> Promise:
    """추출 결과 하나를 Promise 로 저장한다 (extract_event 와 실험의 원천 발언 추출이 같이 쓴다)."""
    # 주체 기관: 규칙 결과가 있으면 그것을 쓴다 (LLM 값이 '충청북도'·'청주시청 도로과' 처럼 달라도 덮어쓰지 않는다 — 병합 키 일관성).
    # 규칙이 못 찾으면 LLM 값을 쓰되, 사람 이름 형태('김민구')면 버린다. 둘 다 없으면 "" = 기관 미상 (병합·추적 키에서 제외).
    actor = normalize_actor_org(q.source, event.responsible_org, lexicon)
    if actor and looks_like_person(actor, lexicon):
        actor = None
    if actor_search.wants(q.source, actor, lexicon):
        actor = resolve_person_title(event, q.source, q.published_at, lexicon) or actor
    if not actor:
        cand = (res.actor_org or "").strip()
        actor = cand if cand and not looks_like_person(cand, lexicon) else ""
    deadline, why = parse_deadline(res.deadline_date, q.published_at)
    if why:
        if stats is not None:
            stats["deadline_rejected"] = stats.get("deadline_rejected", 0) + 1
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
    return p


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
    # 발생 창 게이트 완화로만 들어온 연결(사고 직후 발생 보도)의 인용문은 약속으로 뽑지 않는다 — 그 완화는 화면·군집용이고 판정 재료를 바꾸지 않는다
    linked_ids = {l.news_id for l in event.links if l.is_active and not occurrence_gate_link(l)}
    start = event.occurred_at
    until = event.occurred_at + timedelta(days=settings.quotation_window_days)
    query = quotation_query(event)
    docs, truncated = fetch_quotations(client, query, start, until) if query else ([], [])
    aliases = [event.responsible_org, *(event.org_aliases or [])]
    lexicon = [event.region, event.responsible_org, *(event.org_aliases or [])]  # 사람 이름 판정용 사전 (3차 1-2)
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
                is_public_actor=is_public_actor(d.source, lexicon),
            )
            event.quotations.append(q)
            existing[key] = q
        if q.is_public_actor:
            stats["public"] += 1
            if not q.promises:
                pending.append(q)
    session.commit()

    # 1-1) 이름+직함 주체의 기관 검색 (캐시에 없는 (이름, 직함, 분기) 만 — record 모드는 녹화 재생)
    if settings.actor_search and getattr(client, "mode", "mock") != "mock":
        wanted = [q for q in pending if actor_search.wants(q.source, normalize_actor_org(q.source, event.responsible_org, lexicon), lexicon)]
        stats["actor_search"] = actor_search.ensure_cache(client, [(q.source, q.published_at, lexicon) for q in wanted])

    # 2) LLM 호출 (DB 쓰기 없음). 같은 (주체, 발언) 은 한 번만.
    memo: dict[tuple[str, str], object] = {}
    results: list[tuple[Quotation, object]] = []
    gave_up: list[tuple[Quotation, str]] = []
    for q in pending:
        mkey = (q.source.strip(), re.sub(r"\s+", " ", q.quotation.strip()))
        if mkey in memo:
            results.append((q, memo[mkey]))
            continue
        if call_budget is not None and call_budget.get("remaining", 1) <= 0:
            stats.setdefault("deferred", 0)
            stats["deferred"] += 1
            continue
        if int((event.llm_failures or {}).get(f"promise:{q.id}", 0)) >= 2:
            continue  # 2회 실패한 인용문은 '해당없음' 으로 기록돼 있다 — 다시 부르지 않는다
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
            n = note_llm_failure(event, f"promise:{q.id}")
            if n >= 2:
                # 명세 §8: 재시도 후에도 실패하면 기록하고 검토 큐로 — 약속 없음('해당없음')으로 남겨 재호출하지 않는다
                log.warning("extract LLM %d회 실패 — 해당없음 기록·검토 큐 %s/%s: %s", n, event.key, q.id, exc)
                gave_up.append((q, f"LLM 호출 {n}회 실패로 추출 포기 — 사람 검토 필요 ({type(exc).__name__})"))
            else:
                log.warning("extract LLM 실패(다음 실행에서 재시도) %s: %s", event.key, exc)
            continue
        clear_llm_failure(event, f"promise:{q.id}")
        memo[mkey] = res
        results.append((q, res))

    # 3) 결과 저장
    for q, res in results:
        if store_promise(event, q, res, llm, lexicon, stats).is_trackable:
            stats["promises"] += 1
    for q, why in gave_up:
        p = Promise(event_id=event.id, quotation_id=q.id, actor_org="", actor_raw=q.source, action="", strength="해당없음", is_trackable=False, evidence_news_id=q.news_id, confidence=0.0, rationale=why, model=llm.model, prompt_version=PROMPT_VERSIONS["promise"])
        event.promises.append(p)
        q.promises.append(p)
    session.flush()
    session.refresh(event)
    stats["merged"] = _merge_duplicates(event, embedder)
    # 아직 추출을 못 받은 공공 인용문 수 — 잠정 판정·검토 큐 표시 (호출 상한 이월·1회 실패). 활성 연결 기사의 인용문만 센다
    event.pending_quotations = count_pending_quotations(event)
    stats["pending_quotations"] = event.pending_quotations
    from .review import recompute_needs_review

    recompute_needs_review(session, event)

    # 약속 근거 기사는 '대응발표' 단계
    evidence_ids = {p.evidence_news_id for p in event.promises if p.is_trackable or p.strength == "의사표명"}
    for l in event.links:
        if l.news_id in evidence_ids:
            l.phase = "대응발표"
    session.commit()
    log.info("extract %s", stats)
    return stats
