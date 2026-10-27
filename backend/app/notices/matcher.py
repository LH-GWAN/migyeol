"""게시판 항목 제목 ↔ 사건 매칭 — 용어 특정성에 따라 가중한 토큰 겹침 점수 (v2 §3, 2026-10-27 보강).

용어(Term)는 시설명(facility)·사건 핵심어(keyword)·약속 행동 키워드(promise)·사건 지역명(region) 네 종류이고,
각 용어는 **고유어** 또는 **일반어**로 나눈다.
  일반어 = 정규화(공백 제거) 3자 이하이면서 숫자가 없는 용어('열차'·'산불'·'송전선'), 또는 모든 단어가 일반어 목록(GENERIC_WORDS)에
           있는 용어('하천 제방'·'지하차도 침수'). 지역명과 숫자가 든 용어('5호선'·'4대강')는 길이와 무관하게 고유어.
가중치
  고유 시설명 0.6 ('통복터널'·'달빛지하차도' — 단독으로 저장)
  일반어 구(두 단어 이상이 모두 일반어) 0.4 ('하천 제방'·'태풍 카눈' — 단독으로는 못 넘고 조치 어휘나 다른 용어가 하나 더 있어야)
  고유 핵심어·약속어·지역명 0.3
  일반어 단일 용어 0.2 ('열차'·'사고'·'송전선' — 둘이 맞아도 0.4, 시설명·지역명 등 붙잡는 용어가 없으면 가점도 없어 저장되지 않는다)
가점(각 0.1; 시설명·지역명·고유어가 맞았거나 사건 지역 어간이 제목·요약에 있을 때만 — '열차'·'호우', '어선 전복' 같은 일반어 핵심어만 맞은 제목은 지역명이 없으면 가점이 없어 둘이 맞아도 0.4)
  조치 어휘: 제목에 점검·보강·복구·대책 같은 대응 어휘(ACTION_WORDS)가 있다  ← 기관 발표 중 '대응 조치' 글을 고르는 신호
  발생 직후: 게시일이 발생일 전날~7일 뒤(RECENT_DAYS)
  요약: 본문 요약(snippet)에 사건 지역명(또는 그 광역·지자체 책임기관 어간) 또는 고유 시설명이 있다(제목에는 없을 때)
감점: 제목에 사건과 다른 행정구역 이름이 있으면 −0.3(ADMIN_BASES, 고유 시설명이 맞은 제목은 제외) — 같은 종류의 다른 지역 사고 발표를 거른다.
합계 0~1.0. 기본 임계값 `NOTICE_MATCH_THRESHOLD`=0.5.
그 밖의 규칙: 여러 단어 용어는 모든 단어가 제목에 있어야 일치. 3자 이하 용어는 띄어쓰기 없이 그대로 제목에 있어야 한다
('벤처 4대 강국'에 '4대강'은 불일치). 더 긴 용어에 포함된 짧은 용어는 중복으로 세지 않는다('달빛지하차도'가 맞으면 '지하차도'는 더하지 않음).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from ..rules.orgs import canonical_org
from ..rules.regions import ADMIN_BASES, region_base, region_parents

W_FACILITY = 0.6  # 고유 시설명
W_GENERIC_PHRASE = 0.4  # 일반어로만 된 두 단어 이상 용어 (종류 무관)
W_SPECIFIC = 0.3  # 고유 핵심어·약속어·지역명
W_GENERIC = 0.2  # 일반어 단일 용어
B_ACTION = 0.1
B_RECENT = 0.1
B_SNIPPET = 0.1
P_FOREIGN_REGION = 0.3  # 제목에 사건과 다른 지역명이 있을 때 감점 (고유 시설명이 맞으면 적용하지 않는다)
MIN_TERM_CHARS = 2
SHORT_TERM_CHARS = 3  # 이하이면 (숫자 없을 때) 일반어, 그리고 제목에 띄어쓰기 없이 그대로 있어야 일치
RECENT_DAYS = 7

# 재난·안전 사건에서 시설명·핵심어로 자주 쓰이지만 한 단어로는 사건을 특정하지 못하는 말 (정규화형). 3자 이하는 목록 없이도 일반어다.
GENERIC_WORDS = frozenset((
    "지하차도", "고속도로", "국가하천", "지방하천", "송전선로", "송전선", "송전탑", "전력시설", "철도시설", "전동차", "지하철", "건널목",
    "정류장", "교차로", "횡단보도", "어린이집", "요양병원", "요양원", "물류센터", "공사현장", "공사장", "아파트", "주택가", "오피스텔",
    "변전소", "저수지", "배수로", "하수관", "하수도", "상수도", "옹벽", "비탈면", "절개지", "산사태", "집중호우", "폭우",
    "태풍", "호우", "산불", "화재", "침수", "붕괴", "범람", "유실", "폭발", "누출", "사고", "재난",
    "재해", "안전", "피해", "복구", "열차", "철도", "선로", "궤도", "도로", "교량", "터널", "하천",
    "제방", "차량", "버스", "공장", "학교", "병원", "건물", "시설", "현장", "주택", "상가",
))

# 기관의 대응 조치를 알리는 제목에 흔한 어휘. 일반어만 맞은 제목은 이 어휘가 있어야 임계값을 넘는다.
ACTION_WORDS = (
    "점검", "보강", "복구", "대책", "조치", "정비", "보수", "준설", "설치", "개선", "강화", "예방", "대비", "대응",
    "안전", "지원", "선포", "지정", "착공", "준공", "완료", "추진", "훈련", "긴급", "방지", "감독", "수립", "시행",
    "확충", "교체", "철거", "보상", "지시", "처분", "과징금", "징계", "수사", "조사", "규명", "재발", "통제", "폐쇄",
)

_DIGIT_RE = re.compile(r"\d")


@dataclass(frozen=True)
class Term:
    text: str
    weight: float
    kind: str  # facility|keyword|promise|region
    generic: bool = False


@dataclass(frozen=True)
class Match:
    score: float
    terms: list[str] = field(default_factory=list)  # 제목에 맞은 용어
    bonuses: list[str] = field(default_factory=list)  # 가점 설명 ('조치 어휘: 점검', '발생 7일 내', '요약: 군위')
    penalties: list[str] = field(default_factory=list)  # 감점 설명 ('다른 지역: 제주')

    @property
    def labels(self) -> list[str]:
        """저장·표시용 — 용어 뒤에 가점을 '+', 감점을 '−' 로 붙인다."""
        return list(self.terms) + [f"+{b}" for b in self.bonuses] + [f"−{p}" for p in self.penalties]


def _norm(s: str) -> str:
    return "".join((s or "").split()).lower()


def _tok_generic(tok: str) -> bool:
    n = _norm(tok)
    return (len(n) <= SHORT_TERM_CHARS and not _DIGIT_RE.search(n)) or n in GENERIC_WORDS


def is_generic(text: str) -> bool:
    """일반어 판정: 단일 단어는 3자 이하(숫자 없음) 또는 목록, 여러 단어는 모두 일반어일 때."""
    toks = text.split()
    return bool(toks) and all(_tok_generic(t) for t in toks)


def _weight(kind: str, text: str) -> tuple[float, bool]:
    if kind == "region":
        return W_SPECIFIC, False  # 지역명은 짧아도 고유어
    generic = is_generic(text)
    if not generic:
        return (W_FACILITY if kind == "facility" else W_SPECIFIC), False
    return (W_GENERIC_PHRASE if len(text.split()) >= 2 else W_GENERIC), True


def region_term(region: str | None, org: str | None) -> str:
    """사건 지역명을 용어로 쓸 어간('군위군'→'군위'). 책임기관 자신의 이름(서울시 사건의 '서울시')이면 쓰지 않는다."""
    r = " ".join((region or "").split())
    if not r or (org and canonical_org(r) == canonical_org(org)):
        return ""
    stem = r[:-1] if len(r) >= 3 and r[-1] in "시군구도" else r
    return stem if len(_norm(stem)) >= MIN_TERM_CHARS else ""


def event_terms(
    facility: str | None,
    keywords: list | None,
    promise_terms: list | None,
    region: str | None = None,
    org: str | None = None,
) -> list[Term]:
    out: list[Term] = []
    seen: set[str] = set()
    for text, kind in (
        [(facility or "", "facility")]
        + [(str(k), "keyword") for k in (keywords or [])]
        + [(str(k), "promise") for k in (promise_terms or [])]
        + [(region_term(region, org), "region")]
    ):
        t = " ".join(text.split())
        if len(_norm(t)) < MIN_TERM_CHARS or _norm(t) in seen:
            continue
        seen.add(_norm(t))
        w, g = _weight(kind, t)
        out.append(Term(t, w, kind, g))
    return out


def _hit(term: str, title_norm: str, title_raw: str) -> bool:
    toks = term.split()
    if not all(_norm(tok) in title_norm for tok in toks):
        return False
    n = _norm(term)
    if len(toks) == 1 and len(n) <= SHORT_TERM_CHARS:
        return n in title_raw  # 짧은 용어는 띄어쓰기 없이 그대로 있어야 ('4대 강국' ≠ '4대강')
    return True


def _dedupe(hits: list[Term]) -> list[Term]:
    hits = sorted(hits, key=lambda t: -len(_norm(t.text)))
    kept: list[Term] = []
    for t in hits:
        if any(_norm(t.text) in _norm(k.text) for k in kept):
            continue  # 더 긴 용어에 포함된 짧은 용어는 한 번만
        kept.append(t)
    return kept


def _anchors(t: Term) -> bool:
    """가점을 받을 수 있는 '사건을 붙잡는' 용어 — 시설명·지역명·고유어. 일반어 핵심어('열차'·'호우', '어선 전복' 같은 쌍 포함)만 맞은
    제목은 사건 지역 어간이 제목·요약에 있어야 가점이 붙는다(score_title 의 anchored)."""
    return t.kind in ("facility", "region") or not t.generic


def own_regions(region: str | None, org: str | None) -> set[str]:
    """사건 자신의 지역 어간들 — 사건 지역·그 광역, 책임기관이 지자체면 그 이름도 ('옹진군'/'인천시' → {옹진, 인천})."""
    out: set[str] = set()
    for name in (region or "", org or ""):
        for part in name.split():
            b = region_base(part)
            if len(b) >= 2 and b in ADMIN_BASES:  # 기관명('해양경찰청')·가상 지명은 지역이 아니다
                out.add(b)
        out |= region_parents(name)
    return out


def foreign_regions(title: str, own: set[str]) -> list[str]:
    """제목에 든, 사건 지역이 아닌 행정구역 이름(2음절 이상, 보통명사와 겹치는 이름은 사전에서 제외돼 있다)."""
    tn = _norm(title)
    found = sorted((b for b in ADMIN_BASES if len(b) >= 2 and b in tn and b not in own), key=lambda b: (-len(b), b))
    return [b for b in found if not any(b != o and b in o for o in own)]  # '부산진' 같은 상위 이름 포함 관계 정리


def action_words(title: str) -> list[str]:
    tn = _norm(title)
    return [w for w in ACTION_WORDS if w in tn]


def score_title(
    title: str,
    terms: list[Term],
    *,
    snippet: str = "",
    published_at: date | None = None,
    occurred_at: date | None = None,
    own: set[str] | None = None,
) -> Match:
    """제목 점수(0~1)·일치 용어·가점·감점. snippet·날짜를 주면 요약·발생 직후 가점, own(사건 지역 어간)을 주면 다른 지역 감점도 계산한다."""
    tn = _norm(title)
    traw = " ".join((title or "").split()).lower()
    kept = _dedupe([t for t in terms if _hit(t.text, tn, traw)])
    score = sum(t.weight for t in kept)
    bonuses: list[str] = []
    penalties: list[str] = []
    sn, sraw = _norm(snippet), " ".join((snippet or "").split()).lower()
    own_in_title = [o for o in sorted(own or (), key=len, reverse=True) if _norm(o) in tn]
    own_in_snippet = [o for o in sorted(own or (), key=len, reverse=True) if _norm(o) in sn]
    if kept and own is not None and not any(t.kind == "facility" and not t.generic for t in kept):
        others = foreign_regions(title, own)
        if others:
            score -= P_FOREIGN_REGION  # 같은 종류의 다른 지역 사고 발표 ('제주 해역 어선 전복' ↔ 옹진 어선 전복 사건)
            penalties.append(f"다른 지역: {others[0]}")
    # 일반어 핵심어만 맞은 제목('어선 전복 긴급지시')은 사건 지역 어간이 제목·요약에 있어야 가점을 받는다 → 쌍만으로는 0.4 에 머문다
    anchored = bool(kept) and (any(_anchors(t) for t in kept) or bool(own_in_title) or bool(own_in_snippet))
    if anchored:
        acts = action_words(title)
        if acts:
            score += B_ACTION
            bonuses.append(f"조치 어휘: {acts[0]}")
        if published_at is not None and occurred_at is not None and -1 <= (published_at - occurred_at).days <= RECENT_DAYS:
            score += B_RECENT
            bonuses.append(f"발생 {RECENT_DAYS}일 내")
        if sn:
            matched_norm = {_norm(t.text) for t in kept}
            hit = next(
                (t.text for t in terms if _norm(t.text) not in matched_norm and (t.kind == "region" or (t.kind == "facility" and not t.generic)) and _hit(t.text, sn, sraw)),
                None,
            ) or (own_in_snippet[0] if own_in_snippet and not own_in_title else None)
            if hit:
                score += B_SNIPPET
                bonuses.append(f"요약: {hit}")
    return Match(round(max(0.0, min(1.0, score)), 3), [t.text for t in kept], bonuses, penalties)
