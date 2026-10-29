"""3. 사건–기사 연결 (LLM 사용 금지) — 규칙 게이트 + 임베딩 유사도.

후보 = 수집 단계가 이 사건을 위해 가져온 기사(EventCandidate) ∪ 기존 연결. 로컬 질의 재매칭은 하지 않는다.

게이트(필수): 발행일 ≥ 발생일-1일 AND 제목에 시드 제외어(exclude_terms) 없음 AND (지역 OR 시설 OR 기관 일치) AND (시설명 일치 OR 사건분류 계열 일치 OR 핵심어·연관어 2개 이상 OR 약속 행동 키워드 2개 이상
  OR 발생 창(occurrence_window) 안 + 사건 키워드(occurrence_terms) 1개 — LINK_OCCURRENCE_GATE)
판정:
  - 강한 규칙 일치                                            -> 자동 연결 (rule)
      · 시설명이 제목에 있고 (지역·기관 또는 사건분류) 일치
      · 시설명 + 지역·기관
      · 책임기관 + 약속 행동 키워드 2개, 또는 지역 + 약속 행동 키워드 2개 + (시설명 또는 시드 고유 핵심어 1개)
  - 게이트 통과 AND score ≥ LINK_AUTO                         -> 자동 연결 (embedding)
  - 게이트 통과 AND LINK_REVIEW ≤ score < LINK_AUTO           -> 연결하되 needs_review (검토 큐)
  - 그 외                                                     -> 폐기 (기존 자동 연결이면 삭제)
사건별 단어 가중치(rules/weights.py, link_all 시작 때 재계산해 Event.term_weights 에 저장):
  - weighted_overlap(기사에 나온 사건 고유어 가중치 합, 0~1) < WEIGHT_MIN 이면 강한 규칙이 아닌 기사는 게이트 탈락
  - 강한 규칙이라도 weighted_overlap < WEIGHT_RULE_MIN 이면 강한 규칙으로 보지 않는다
  - score = (1-WEIGHT_ALPHA)·임베딩 코사인 + WEIGHT_ALPHA·weighted_overlap  (세 값이 모두 0 이면 이전 동작)
기간 창·지역 필수 조건(scope_checks, LINK_SCOPE_RULES — 끄면 이전 동작):
  - 발생일 −1 ~ +LINK_EARLY_DAYS(90)일: 위 게이트 그대로
  - 그 뒤 기사: 제목에 사건 고유어(가중치 ≥ LINK_UNIQUE_MIN 인 지역·일반어 아닌 단어, 시설명) — 또는 제목에 사건 지역어가 있고 하이라이트에 고유어 —
    또는 약속 행동 키워드 ≥2. 지역어 + 일반 재난어만으로는 붙지 않는다. 같은 재난어의 새 사고 속보(구조·사망·특보 …, 돌아보는 말 없음)는 검토 큐 이하
  - 기초 지역 사건: 제목·하이라이트·키워드에 사건 지역어(기초명·사건 제목의 지명·동/읍/면/리)가 있거나 시설명 일치. 광역 사건: 사건 고유어 하나 이상
  - 제목에 사건과 다른 지역 지명이 있고 사건 지역어·상위 광역·시설명이 제목에 없으면 탈락 (사건 고유어가 있고 새 사고 속보가 아니면 남긴다)
  - 제목에 사건에 없는 구체 장소명(…역·…항·…공장 등) + 재난어가 있고 사건 고유어가 제목에 없으면 검토 큐 이하
같은 지역의 다른 시드 시설명이 본문에 있고 이 사건 시설명이 없으면 score ≥ LINK_REVIEW 일 때만 검토 큐, 아니면 폐기.
학습 기반 재순위(LINK_RERANKER, pipeline/rerank.py — 기본 none): 위 판정이 link·review 인 후보를 분류기 확률로 자동 연결·검토 큐·폐기로 다시 정한다.
  판정 재료는 assess() 하나로 만들어 link 단계와 학습 스크립트(scripts/train_reranker.py)가 같이 쓴다.
한 기사가 여러 사건의 자동 연결에 들어가면 점수가 가장 높은 사건만 유지하고 나머지는 검토 큐로 보낸다 (재순위를 켜면 분류기 확률).
관리자가 confirmed / rejected / manual 로 표시한 연결은 재평가하지 않는다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db.models import Article, Event, EventArticle, EventCandidate
from ..rules import regions as R
from ..rules import weights as W
from .collect import article_text
from .embeddings import EmbeddingBackend, cosine, from_bytes, needs_recompute, to_bytes
from .review import recompute_needs_review
from .seeds import seed_from_event

log = logging.getLogger("migyeol.link")

_INVESTIGATION_RE = re.compile(r"(원인|조사|감사|감식|결론|조사위|진단)")


def incident_prefix(incident_type: str) -> str:
    parts = incident_type.split(">")
    return ">".join(parts[:2]) if len(parts) >= 2 else incident_type


@dataclass(frozen=True)
class Scope:
    """사건 하나의 기간 창·지역 조건 재료 (gate 가 기사마다 다시 만들지 않게 캐시)."""

    wide: bool  # 시드 지역이 광역(도·특별시·광역시)
    region_terms: tuple[str, ...]  # 사건 지역어 — 본문 부분 문자열 일치 (기초명·접미 뗀 꼴·사건 제목·핵심어의 지명·동/읍/면/리)
    allowed: frozenset[str]  # '다른 지역' 판정에서 같은 지역으로 보는 이름 (약칭)
    parents: frozenset[str]  # 사건 지역의 상위 광역
    unique: tuple[str, ...]  # 사건 고유어: 가중치 ≥ LINK_UNIQUE_MIN 인 지역·일반어 아닌 단어 + 시설명
    facility_terms: tuple[str, ...]  # 시설명과 그 고유 어절 (지역어 대체 근거)
    incident_words: tuple[str, ...]  # 사건 제목의 재난어 (늦은 같은 유형 새 사고 판정)
    early_end: date | None  # 이 날까지는 이전과 같은 게이트, 그 뒤는 늦은 기사 조건 (None = 기간 창 끔)


_LOCAL_RE = re.compile(r"[가-힣]{1,5}(동|읍|면|리)$")
_INCIDENT_WORDS = (
    "화재", "산불", "침수", "붕괴", "추락", "전복", "침몰", "좌초", "유출", "폭발", "충돌", "추돌", "탈선", "산사태", "범람", "매몰", "끼임",
    "유실", "균열", "단전", "갇힘", "폭설", "호우", "폭우", "지진", "태풍",
)
# 늦은 같은 유형 기사가 '새 사고 속보' 인지 — 구조·사상·수색·특보 같은 발생 보도 말
_NEW_INCIDENT_RE = re.compile(r"(인명\s?피해|구조|숨져|숨진|사망|실종|부상|다쳐|중상|화상|대피|진화|발생|속보|수색|신고|또\s|또다시|잇따|잇단|주의보|특보|운행\s?중단|결항)")
# 늦은 기사 제목에 있으면 '새 사고 속보' 가 아니라 원래 사건을 돌아보는 기사로 본다
_FOLLOWUP_FRAME_RE = re.compile(
    r"(복구|완료|준공|개통|재개|정상화|재판|기소|송치|검찰|선고|판결|보상|배상|대책|추모|주기|년\s?만|[0-9]년|당시|이후|후속|재발\s?방지|원인|요인|조사\s?결과|"
    r"책임|수사|압수수색|중대재해|입건|구속|영장|보석|석방|구상|소송|법원|패소|승소|감사|개선|백서|점검|부실|의인|지원|피해주민)"
)
# 대응 조치 명사 — 재발 기사가 조치 구간을 가리키면 원래 사건의 후속으로 본다
_ACTION_RE = re.compile(r"(보강|증설|준설|교체|정비|설치|공사\s?구간|개선\s?공사|복구\s?구간|방지\s?시설)")
_NOT_PLACE = ("지역", "구역", "영역", "전역", "권역", "해역", "유역", "수역", "부시장", "시장실", "재난지역", "취약지역")
_PLACE_SUFFIX = (
    "역", "항", "대교", "터널", "공장", "시장", "물류센터", "센터", "아파트", "병원", "학교", "초등학교", "지하차도", "고가", "주차장", "마트", "상가", "빌딩",
    "호텔", "모텔", "발전소", "제철소", "조선소", "공항", "휴게소", "나들목", "저수지", "해수욕장", "선착장", "방파제", "공사장",
)
# 시설명을 이루어도 사건을 가리키지 못하는 말 — '산사태 피해지'·'침수 도로' 같은 시설명은 지역어 대체 근거가 되지 못한다
_GENERIC_PLACE = frozenset(
    "피해지 피해지역 피해현장 현장 일대 인근 주택 주택가 마을 농경지 도로 하천 제방 어선 선박 공장 아파트 상가 건물 산림 임야 발생지 지역 해상 "
    "공사현장 지하주차장 수거차 침수 물류센터 물류창고 창고 선착장 시장 공사장 선로 열차".split()
)
# 권역 이름 → 광역 (사건 제목·핵심어에 있으면 같은 지역으로 본다)
_BLOCS = {
    "수도권": ("서울", "경기", "인천"), "충청권": ("대전", "세종", "충북", "충남"), "충청": ("대전", "세종", "충북", "충남"),
    "영남": ("부산", "대구", "울산", "경북", "경남"), "호남": ("광주", "전북", "전남"), "남부지방": ("부산", "대구", "울산", "경북", "경남", "광주", "전북", "전남"),
    "대구경북": ("대구", "경북"), "광주·전남": ("광주", "전남"),
}
_SPLIT_RE = re.compile(r"[·~/,]")


def _title_tokens(title: str) -> list[str]:
    return [t for part in _SPLIT_RE.split(title or "") for t in W.text_tokens(part)]


def _scope_key(event: Event, settings: Settings) -> tuple:
    return (
        event.id, event.region, event.title, event.facility, tuple(event.keywords or []), tuple(event.org_aliases or []),
        tuple(sorted((event.term_weights or {}).items())), event.occurred_at, event.source_news_id,
        settings.link_early_days, settings.link_unique_min, settings.link_window_from_source,
    )


_SCOPE_CACHE: dict[tuple, Scope] = {}


def event_scope(event: Event, settings: Settings, source_date: date | None = None) -> Scope:
    key = (*_scope_key(event, settings), source_date)
    if key in _SCOPE_CACHE:
        return _SCOPE_CACHE[key]
    region = event.region or ""
    wide = R.is_wide_region(region)
    parents = frozenset(R.region_parents(region))
    terms: set[str] = set()
    for part in region.split():
        terms.add(part)
        b = R.region_base(part)
        if len(b) >= 2:
            terms.add(b)
    if not wide and len(region.split()) > 1:
        terms.discard(region.split()[0])  # '인천 동구' 의 '인천' 은 상위 광역 — 기초 지역어가 아니다
    allowed = {R.region_base(p) for p in region.split()} | set(parents)
    phrases = [event.title or "", event.facility or "", *(event.keywords or [])]
    for ph in phrases:
        for tok in _title_tokens(ph):
            b = R.region_base(tok)
            if b in R.ADMIN_BASES or b in R.PARENT or b in R.WIDE_CANON:
                allowed.add(R.WIDE_CANON.get(b, b))
                if R.PARENT.get(b) in parents or b not in R.WIDE_CANON:
                    terms.add(b)  # 사건 제목·핵심어의 같은 광역 지명 ('경북 예천·봉화' → 예천, '산청·가평' → 가평)
            elif _LOCAL_RE.match(tok) and len(tok) >= 3 and not wide:
                terms.add(tok)  # 송림동·야탑동·온양읍·진평리
            else:
                stem = next((tok[: -len(x)] for x in _PLACE_SUFFIX if tok.endswith(x) and len(tok) > len(x) + 1), "")
                sb = R.region_base(stem)
                if sb in R.ADMIN_BASES or sb in R.PARENT:
                    allowed.add(sb)
                    terms.add(sb)  # '영등포역' → 영등포, '감천항' 은 지명이 아니라 그대로
    for bloc, members in _BLOCS.items():
        if any(bloc in ph for ph in phrases):
            allowed.update(members)
    for p in parents:
        allowed.add(p)
    regions_set = W.region_terms(region) | terms | set(allowed)
    weights = event.term_weights or {}
    unique = {t for t, v in weights.items() if v >= settings.link_unique_min and len(t) >= 2 and not W.is_generic(t, regions_set)}
    fac = (event.facility or "").strip()
    # '135금성호' 처럼 숫자로 시작하는 시설명은 숫자를 뗀 꼴('금성호')도 시설 어절로 본다
    fac_toks = [t for t in W.text_tokens(re.sub(r"(?<!\S)\d+", "", fac)) if t not in _GENERIC_PLACE and not W.is_generic(t, regions_set)]
    has_local = any(_LOCAL_RE.match(t) and len(t) >= 3 for t in W.text_tokens(fac))
    fac_terms = {fac} if fac and (fac_toks or has_local) and (not weights or fac in weights or " " in fac) else set()
    for tok in fac_toks:
        if tok in unique or not weights:
            fac_terms.add(tok)
    unique = {u for u in unique if not any(u in g for g in _GENERIC_PLACE)}  # '물류'(⊂ 물류센터)·'공사'(⊂ 공사장) 도 사건을 가리키지 못한다
    unique |= fac_terms
    incident = tuple(w for w in _INCIDENT_WORDS if w in (event.title or ""))
    early_end = None
    if settings.link_early_days > 0:
        anchor = event.occurred_at
        if settings.link_window_from_source and source_date and source_date > anchor:
            anchor = source_date
        early_end = anchor + timedelta(days=settings.link_early_days)
    sc = Scope(
        wide=wide, region_terms=tuple(sorted(t for t in terms if len(t) >= 2)), allowed=frozenset(allowed), parents=parents,
        unique=tuple(sorted(unique)), facility_terms=tuple(sorted(fac_terms)), incident_words=incident, early_end=early_end,
    )
    if len(_SCOPE_CACHE) > 4096:
        _SCOPE_CACHE.clear()
    _SCOPE_CACHE[key] = sc
    return sc


def new_incident_title(title: str, words: tuple[str, ...] = _INCIDENT_WORDS) -> bool:
    """제목이 새 사고 속보인가 — 재난어 + 발생 보도 말(구조·사망·실종·특보 …) 이 있고 돌아보는 말(복구·재판·원인 …)이 없다."""
    return any(w in title for w in words) and bool(_NEW_INCIDENT_RE.search(title)) and not _FOLLOWUP_FRAME_RE.search(title)


def other_region_in_title(sc: Scope, title: str) -> str | None:
    """제목에 사건과 다른 광역·기초 지명이 있으면 그 이름 (광역 사건은 그 광역 안의 기초 지명을 같은 지역으로 본다)."""
    for tok in _title_tokens(title):
        b = R.region_base(tok)
        if b not in R.ADMIN_BASES:
            continue
        b = R.WIDE_CANON.get(b, b)
        if b in sc.allowed:
            continue
        if R.PARENT.get(b) in sc.parents and sc.wide:
            continue
        if b in sc.parents:
            continue
        return b
    return None


def source_date(session: Session, event: Event) -> date | None:
    """원천 기사(대응 발언) 발행일 — LINK_WINDOW_FROM_SOURCE 일 때 창의 기준."""
    src = session.get(Article, event.source_news_id) if event.source_news_id else None
    return src.published_at if src is not None else None


def scope_checks(event: Event, article: Article, settings: Settings, source_date: date | None = None) -> list[str]:
    """기간 창·지역 필수 조건 — 탈락 사유 'x-…' 또는 검토 큐 상한 사유 'cap-…' 목록 (빈 목록 = 통과).

    ① 발생일 −1 ~ 창 끝(발생일 + LINK_EARLY_DAYS): 이전 게이트 그대로
    ② 창 뒤: 제목·하이라이트에 시설명·사건 고유어가 있거나 약속 행동 키워드 ≥2 여야 한다 (지역어 + 일반 재난어만으로는 불가)
       창 뒤 제목이 사건과 같은 재난어의 새 사고 속보(돌아보는 말 없음)면 검토 큐 이하 (cap-recur)
    ③ 기초 지역 사건: 제목·하이라이트·키워드에 사건 지역어가 있거나 시설명 일치 / 광역 사건: 사건 고유어 하나 이상
    ④ 제목에 사건과 다른 지역 지명이 있고 사건 지역어·시설명이 제목에 없으면 탈락
    ⑤ 제목에 사건에 없는 구체 장소명(…역·…항·…공장 등) + 재난어가 있고 사건 고유어가 제목에 없으면 검토 큐 이하 (cap-place)
    """
    if not settings.link_scope_rules:
        return []
    sc = event_scope(event, settings, source_date)
    title = article.title or ""
    head = " ".join([title, article.hilight or "", " ".join(article.tms_keywords or [])])
    th = title + " " + (article.hilight or "")
    full = article_text(article)
    out: list[str] = []
    fac_hit = any(f in head for f in sc.facility_terms)
    fac_title = any(f in title for f in sc.facility_terms)
    uniq_th = any(u in th for u in sc.unique)
    uniq_title = any(u in title for u in sc.unique)
    region_hit = any(t in head for t in sc.region_terms)
    # ③ 지역 필수
    if sc.wide:
        # 광역 재난(경북 산불·경기 폭설)은 시설명이 재난 자체라 고유어가 약하다 — 제목에 그 광역(또는 그 안의 기초) 지명과 사건 재난어가 함께 있으면 받는다
        wide_title = bool(sc.incident_words) and any(w in title for w in sc.incident_words) and (
            any(t in title for t in sc.region_terms) or any(R.PARENT.get(R.region_base(t)) in sc.parents for t in _title_tokens(title))
        )
        if not any(u in full for u in sc.unique) and not wide_title:
            out.append("x-wide-unique")
    elif sc.region_terms and not region_hit and not fac_hit:
        if not (settings.link_region_parent_ok and any(p in head for p in sc.parents) and uniq_th):
            out.append("x-region")
    # ④ 다른 지역
    other = other_region_in_title(sc, title)
    if other and not fac_title and not any(t in title for t in (*sc.region_terms, *sc.allowed)):
        # 다른 지자체가 이 사건을 두고 움직인 기사('부산시, 금성호 침몰 사고 피해자 지원')는 남긴다 — 사건 고유어가 제목에 있고 새 사고 속보가 아닐 때
        if not (uniq_title and not new_incident_title(title)):
            out.append(f"x-other-region={other}")
    # ② 늦은 기사
    pub = article.published_at
    if sc.early_end is not None and pub is not None and pub > sc.early_end:
        promise_kws = [k for k in dict.fromkeys(event.promise_terms or []) if len(k) >= 2 and not W.is_generic(k, set())]
        p_hits = sum(1 for k in promise_kws if k in head)
        if settings.link_late_match == "title":
            late_ok = uniq_title
        elif settings.link_late_match == "title_region":
            late_ok = uniq_title or (uniq_th and any(t in title for t in sc.region_terms))
        else:
            late_ok = uniq_th
        if not (late_ok or p_hits >= 2):
            out.append("x-late")
        elif sc.incident_words and new_incident_title(title, sc.incident_words):
            # 약속한 조치를 가리키는 재발('보강 구간 바로 옆 또 유실')은 후속(새로운 문제)이다 — 제목에 약속 행동 키워드가 없을 때만 내린다
            if not any(k in title for k in promise_kws) and not _ACTION_RE.search(title):
                out.append("cap-recur")
    # ⑤ 다른 구체 장소 — 그 장소의 새 사고 속보일 때만 (정책·지원 기사의 '…지역'·'부시장' 같은 말은 장소가 아니다)
    if not uniq_title and new_incident_title(title):
        known = set(sc.unique) | set(sc.region_terms) | set(event.term_weights or {})
        for tok in _title_tokens(title):
            stem = next((tok[: -len(x)] for x in _PLACE_SUFFIX if tok.endswith(x)), None)
            if stem is None or len(tok) < 3 or not stem or R.region_base(stem) in R.ADMIN_BASES or stem in R.WIDE_CANON:
                continue  # '서울시장'·'성남시장' 은 장소가 아니다
            if tok.endswith(_NOT_PLACE):
                continue
            if tok not in known and not W.is_generic(tok, set()) and not any(tok in f or f in tok for f in sc.facility_terms):
                out.append(f"cap-place={tok}")
                break
    return out


def gate(event: Event, article: Article, settings: Settings | None = None, source_date: date | None = None) -> tuple[bool, list[str]]:
    settings = settings or get_settings()
    text = article_text(article)
    reasons: list[str] = []
    if article.published_at is None or article.published_at < event.occurred_at - timedelta(days=1):
        return False, ["date"]
    excluded = next((t for t in (event.exclude_terms or []) if t and t in (article.title or "")), None)
    if excluded:
        return False, [f"x-exclude={excluded}"]  # 시드 제외어 — 같은 지역의 다른 사고 (관리자 확정·수동 연결은 link_event 가 재평가하지 않는다)
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
    if not topic_ok and scope_ok and settings.link_occurrence_gate and occurrence_window(event, article, settings):
        # 사고 직후 기사는 시설명이 정확하지 않고('청동초 스쿨존' 대신 '영도구 등굣길', '고현천' 대신 '거제시 폭우') 사건분류도 비어 있다 —
        # 범위 + 사건 키워드(occurrence_terms) 1개면 통과. 통과만 시킨다: 연결 여부는 점수·재순위가 정한다
        if any(k in text for k in occurrence_terms(event)):
            topic_ok = True
            reasons.append("occ-window")
    if not (scope_ok and topic_ok):
        return False, reasons
    checks = scope_checks(event, article, settings, source_date)
    reasons += checks
    return not any(c.startswith("x-") for c in checks), reasons


def capped(reasons: list[str]) -> bool:
    """기간 창·장소 조건이 검토 큐 이하로 내린 기사 (decide 의 other_hit 와 같은 취급)."""
    return any(r.startswith("cap-") for r in reasons)


def is_strong(reasons: list[str], promise_candidate: bool = False) -> bool:
    """promise_candidate: 약속 키워드 재검색으로 들어온 후보 — 지역·기관 + 약속 행동 키워드 2개면 강한 규칙 (3차 1-8).
    책임기관이 지역과 같은 사건에서 시설명 없는 기관 명의 완료 기사('가온시청 "배수펌프 증설 완료"')를 놓치지 않기 위해."""
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
    if promise_candidate and scoped and promise_hit:
        return True
    return scoped and promise_hit and (has_facility or seed_hit)


def decide(strong: bool, other_hit: bool, cos: float, overlap: float, settings: Settings) -> tuple[str, str, float, list[str]]:
    """게이트 통과 기사의 연결 결정 (순수 함수 — scripts/evaluate.py 의 라벨 스윕이 같은 함수를 쓴다).

    반환: (decision link|review|drop, method rule|embedding, score, 덧붙일 사유).
    """
    notes: list[str] = []
    score = (1.0 - settings.weight_alpha) * cos + settings.weight_alpha * overlap
    if strong and overlap < settings.weight_rule_min:
        strong = False
        notes.append("rule-weight-low")
    if not strong and overlap < settings.weight_min:
        return "drop", "embedding", score, [*notes, "weight-low"]
    if other_hit:
        if score < settings.link_review:
            return "drop", "embedding", score, notes
        return "review", "embedding", score, [*notes, "other-facility"]
    if strong or score >= settings.link_auto:
        return "link", ("rule" if strong else "embedding"), score, notes
    if score >= settings.link_review:
        return "review", "embedding", score, notes
    return "drop", "embedding", score, notes


_NO_SCOPE = Settings(link_scope_rules=False, link_occurrence_gate=False, _env_file=None)  # 가중치 핵심 문서는 이전 게이트 그대로 (결정성·다른 사건 가중치 불변)


def core_documents(session: Session, event: Event) -> list[set[str]]:
    """가중치 TF 의 핵심 문서: 시드 문장 + 원천 기사 + 발생일 ±CORE_DAYS 일 안의 게이트 통과 후보 (이전 연결 상태와 무관하게 결정적, 발생 창 보강 후보 제외)."""
    docs = [W.text_tokens(" ".join([event.title or "", event.facility or "", " ".join(event.keywords or []), event.summary or ""]))]
    src_id = event.source_news_id
    src = session.get(Article, src_id) if src_id else None
    if src is not None:
        docs.append(W.article_tokens(src.title, src.hilight, src.tms_keywords))
    # 발생 창 보강 질의(collect_occurrence)의 후보는 일부러 넓게 받은 것이라 사건 고유어를 흐린다 — 핵심 문서에 넣지 않는다 (시드 질의 후보만)
    broad = {c.news_id for c in session.query(EventCandidate).filter_by(event_id=event.id, source="occurrence").all()}
    for a in candidate_articles(session, event):
        if a.news_id == src_id or a.news_id in broad or not W.core_window(event.occurred_at, a.published_at):
            continue
        if gate(event, a, _NO_SCOPE)[0]:  # 기간 창·지역 조건은 가중치에 기대므로 핵심 문서 선정에는 쓰지 않는다 (결정성)
            docs.append(W.article_tokens(a.title, a.hilight, a.tms_keywords))
    return docs


def refresh_term_weights(session: Session, events: list[Event]) -> W.CorpusStats:
    """사건별 단어 가중치를 다시 계산해 Event.term_weights 에 저장한다 (DF = articles 테이블 전체)."""
    stats = W.corpus_stats(session.query(Article.title, Article.hilight, Article.tms_keywords).yield_per(5000))
    for ev in events:
        ev.term_weights = W.build_weights(
            core_documents(session, ev),
            stats,
            seeds=W.seed_terms(ev.facility, ev.keywords, ev.query_override or ev.seed_query),
            regions=W.region_terms(ev.region),
        )
    session.commit()
    return stats


# '발생' 단계 = 사고가 났다는 사실 자체를 알린 보도. 발생 직후(PHASE_OCCURRENCE_DAYS) 기사는 모두, 그 뒤 PHASE_CONFIRM_DAYS 까지는
# 제목이 사고 사실을 확인·보도하는 기사만 넣는다 — 사상자 집계·신원 확인·피해 규모·사고 경위의 첫 보도.
# 제목의 주된 내용이 원인조사·수사, 대응·대책, 복구·지원, 추모, 의견(사설·칼럼)이면 사실 보도가 아니다 (하이라이트는 보지 않는다:
# 발생 기사 본문에도 '경찰은 원인을 조사 중' 같은 문장이 흔하다).
_OCCURRENCE_FACT_RE = re.compile(
    r"(사망|숨져|숨진|숨지|숨졌|목숨|참변|참사|희생자|부상|다쳐|다친|중상|경상자|실종|사상자|명\s?구조|구조돼|구조됐|신원|피해\s?규모|피해액|"
    r"피해\s?(눈덩이|속출|확산|집계|늘어|커져)|재산\s?피해|이재민|침수\s?(피해|가구|주택|차량)|경위|당시\s?상황|순간|CCTV|블랙박스|목격)"
)
_NOT_OCCURRENCE_RE = re.compile(
    r"(원인|조사|감식|감사|수사|입건|송치|구속|영장|압수수색|기소|재판|선고|혐의|책임|처벌|대책|재발\s?방지|개선|점검|추진|계획|검토|예정|촉구|요구|"
    r"지원|복구|보상|배상|추모|분향|장례|발인|영결|위로|조문|방문|간담회|대응|조치|강화하|강화한|강화키|예산|법안|발의|의무화|단속|설치|교체|보강|시위|집회|국감|"
    r"국정감사|사설|칼럼|기고|시론|데스크|오피니언|기자수첩|포토)"
)


def occurrence_report(title: str) -> bool:
    """제목이 사고 사실 자체(사상자·신원·피해 규모·경위)를 보도하고, 원인조사·대응·후속·의견 기사가 아니다."""
    t = title or ""
    return bool(_OCCURRENCE_FACT_RE.search(t)) and not _NOT_OCCURRENCE_RE.search(t)


def occurrence_window(event: Event, article: Article, settings: Settings) -> bool:
    """'발생' 단계 창: 발생 −1 ~ +PHASE_OCCURRENCE_DAYS 일, 또는 +PHASE_CONFIRM_DAYS 일 안의 사고 사실 보도 (initial_phase 와 연결 게이트가 같이 쓴다)."""
    if article.published_at is None:
        return False
    days = (article.published_at - event.occurred_at).days
    if days < -1:
        return False
    return days <= settings.phase_occurrence_days or (days <= settings.phase_confirm_days and occurrence_report(article.title or ""))


def occurrence_terms(event: Event) -> tuple[str, ...]:
    """발생 창 게이트의 사건 키워드: 시드 시설명·핵심어 + 사건 제목의 고유 어절(지역·일반어 제외) + 사건 제목의 재난어."""
    title = event.title or ""
    regions = W.region_terms(event.region or "")
    title_terms = [t for t in W.text_tokens(title) if len(t) >= 2 and not W.is_generic(t, regions)]
    incident = [w for w in _INCIDENT_WORDS if w in title]
    return tuple(k for k in dict.fromkeys([event.facility or "", *(event.keywords or []), *title_terms, *incident]) if k)


def initial_phase(event: Event, article: Article, settings: Settings | None = None) -> str:
    """연결 직후 단계. extract 가 '대응발표', judge 가 '조치진행·결과확인' 으로 덮어쓴다 (그 뒤로는 바꾸지 않는다)."""
    if occurrence_window(event, article, settings or get_settings()):
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


@dataclass
class EventContext:
    """사건 하나를 판정할 때 한 번 만드는 재료 — link 단계와 재순위 학습(scripts/train_reranker.py)이 같은 판정 함수(assess)를 쓰게 한다."""

    event: Event
    seed_vec: np.ndarray
    others: list[str]  # 다른 시드의 시설명
    promise_ids: set[str]  # 약속 키워드 재검색으로 들어온 후보
    src_date: date | None  # 원천 기사 발행일 (기간 창 기준)
    n_candidates: int = 0  # 수집 후보 수 (재순위 피처)


def event_context(session: Session, event: Event, embedder: EmbeddingBackend, other_facilities: list[str] | None = None) -> EventContext:
    rows = session.query(EventCandidate).filter_by(event_id=event.id).all()
    return EventContext(
        event=event,
        seed_vec=np.asarray(embedder.encode([seed_from_event(event).seed_text()])[0]),
        others=[f for f in (other_facilities or []) if f and f != event.facility],
        promise_ids={c.news_id for c in rows if c.source == "promise"},
        src_date=source_date(session, event),
        n_candidates=len(rows),
    )


@dataclass
class Assessment:
    """후보 기사 하나의 연결 판정. base_decision = 재순위 전 link.decide 결과 (재순위가 꺼져 있으면 decision 과 같다)."""

    ok: bool  # 게이트(기간 창·지역 조건 포함) 통과
    gate_reasons: list[str]
    notes: list[str] = field(default_factory=list)  # decide 가 덧붙인 사유 (rule-weight-low·weight-low·other-facility)
    decision: str = "drop"  # link | review | drop
    method: str = "embedding"  # rule | embedding
    score: float = 0.0  # (1-α)·임베딩 코사인 + α·weighted_overlap
    cos: float = 0.0
    overlap: float = 0.0
    strong: bool = False
    other_hit: bool = False
    promise_candidate: bool = False
    base_decision: str = "drop"
    base_method: str = "embedding"
    prob: float | None = None  # 재순위 분류기 확률 (LINK_RERANKER 가 켜졌고 base_decision 이 link·review 일 때만)

    @property
    def reasons(self) -> list[str]:
        out = [*self.gate_reasons, *self.notes]
        if self.prob is not None:
            out += [f"p={self.prob:.2f}", f"s={self.score:.2f}"]
        return out


def assess(ctx: EventContext, art: Article, settings: Settings, embedder: EmbeddingBackend, *, store_embedding: bool = True) -> Assessment:
    """게이트 → 임베딩 코사인·weighted_overlap → 강한 규칙·다른 시설 → link.decide. 재순위는 rerank.apply 가 따로 덧입힌다."""
    event = ctx.event
    ok, reasons = gate(event, art, settings, ctx.src_date)
    if not ok:
        return Assessment(ok=False, gate_reasons=reasons)
    if needs_recompute(art.embedding, art.embedding_backend, embedder):
        vec = np.asarray(embedder.encode([article_text(art)])[0], dtype=np.float32)
        if store_embedding:
            art.embedding = to_bytes(vec)
            art.embedding_backend = embedder.name
    else:
        vec = from_bytes(art.embedding)
    cos = cosine(vec, ctx.seed_vec)
    text = article_text(art)
    overlap = W.weighted_overlap(event.term_weights, text)
    promise_candidate = art.news_id in ctx.promise_ids
    strong = is_strong(reasons, promise_candidate=promise_candidate)
    other_hit = (any(f in text for f in ctx.others) and "facility" not in reasons) or capped(reasons)
    decision, method, score, notes = decide(strong, other_hit, cos, overlap, settings)
    return Assessment(
        ok=True, gate_reasons=reasons, notes=notes, decision=decision, method=method, score=score, cos=cos, overlap=overlap, strong=strong,
        other_hit=other_hit, promise_candidate=promise_candidate, base_decision=decision, base_method=method,
    )


def link_event(
    session: Session,
    event: Event,
    settings: Settings,
    embedder: EmbeddingBackend,
    other_facilities: list[str] | None = None,
    reranker=None,
) -> dict:
    """reranker: pipeline/rerank.Reranker (LINK_RERANKER) — 게이트를 통과해 link·review 로 판정된 후보를 분류기 확률로 다시 정한다."""
    session.refresh(event)
    ctx = event_context(session, event, embedder, other_facilities)
    existing = {l.news_id: l for l in event.links}
    stats = {"event": event.key, "candidates": 0, "linked": 0, "review": 0, "dropped": 0, "removed": 0, "skipped": 0}
    candidate_ids = {c.news_id for c in session.query(EventCandidate).filter_by(event_id=event.id).all()}

    def remove(link: EventArticle) -> None:
        event.links.remove(link)
        session.delete(link)
        existing.pop(link.news_id, None)
        stats["removed"] += 1

    pending: list[tuple[Article, EventArticle | None, Assessment]] = []
    for art in candidate_articles(session, event):
        stats["candidates"] += 1
        link = existing.get(art.news_id)
        if link is not None and (link.confirmed or link.rejected or link.link_method == "manual"):
            stats["skipped"] += 1
            continue
        if link is not None and art.news_id not in candidate_ids and candidate_ids:
            # 수집 후보에서 빠진(시드를 좁힌) 기사의 자동 연결은 지운다 — 게이트를 통과하더라도 더는 이 사건의 후보가 아니다
            remove(link)
            continue
        pending.append((art, link, assess(ctx, art, settings, embedder)))

    if reranker is not None:
        from .rerank import apply as rerank_apply

        stats["reranked"] = rerank_apply(reranker, ctx, [(art, a) for art, _, a in pending], settings)

    for art, link, a in pending:
        if a.decision == "drop":
            # 이번 실행의 게이트·임계값을 통과하지 못한 자동 연결은 남기지 않는다 (시드 수정·질의 변화 반영)
            if link is not None:
                remove(link)
            else:
                stats["dropped"] += 1
            continue
        if link is None:
            link = EventArticle(event_id=event.id, news_id=art.news_id, phase=initial_phase(event, art, settings))
            event.links.append(link)
            existing[art.news_id] = link
        link.link_method = a.method
        link.link_score = round(a.prob if a.prob is not None else a.score, 3)  # 재순위를 켜면 분류기 확률 (사건 간 배타 처리도 이 값으로)
        link.link_reason = "+".join([*a.reasons, f"w={a.overlap:.2f}"])
        link.needs_review = a.decision == "review"
        link.auto_decision = a.decision  # 관리자 처리와 무관한 파이프라인 결정 (평가용)
        if link.phase in ("발생", "원인조사", "기타"):
            link.phase = initial_phase(event, art, settings)
        stats["linked" if a.decision == "link" else "review"] += 1

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
    refresh_term_weights(session, events)
    from .rerank import load_reranker

    reranker = load_reranker(settings, getattr(embedder, "name", None))  # LINK_RERANKER=none·모델 없음·다른 임베딩 → None (이전 동작)
    kw = {"reranker": reranker} if reranker is not None else {}  # 꺼져 있으면 이전 호출 형태 그대로
    stats = [link_event(session, e, settings, embedder, other_facilities=facilities, **kw) for e in events]
    multi = resolve_multi_event_links(session, all_events if all_events is not None else events)
    if stats:
        stats[0]["multi_event_review"] = len(multi)
        stats[0]["multi_event_events"] = sorted(set(multi))
        stats[0]["reranker"] = reranker.describe() if reranker is not None else "none"
    return stats
