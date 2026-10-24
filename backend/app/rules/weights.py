"""사건별 단어 가중치 (TF-IDF 식) — 연결 게이트와 규칙 후속 신호가 '이 사건에만 있는 말' 을 보게 한다.

  TF  = 사건 핵심 문서(시드 문장 + 원천 기사 + 발생일 ±CORE_DAYS 일 안의 게이트 통과 후보) 중 그 단어가 나온 문서 비율
  IDF = log((N+1)/(df+1)) / log(N+1)  — N·df 는 articles 테이블 전체 (0~1 로 정규화)
  가중치 = TF × IDF, 사건 안 최댓값으로 나눠 0~1. 시설명·핵심어·질의어의 고유어는 하한(FLOOR), 지역명·일반 재난어·행정어는 상한(CAP).

토큰은 형태소 분석기 없이 만든다: 제목·하이라이트 어절에서 조사를 떼고(rules/terms 와 같은 규칙), tms_keywords 는 띄어쓰기로 나눈다.
기사와 단어의 일치는 게이트와 같은 부분 문자열 비교다('통복' 은 '통복터널' 에 걸린다).
weighted_overlap = (기사에 나온 사건 단어 가중치 합) / (사건 상위 REF_TOP=10 개 가중치 합), 1 에서 자른다.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from .regions import ADMIN_NAMES, WIDE
from .terms import _NOUN_TAILS, _PARTICLE_RE, _STOP, _VERB_ENDINGS

CORE_DAYS = 3
MAX_TERMS = 40  # 사건당 저장하는 단어 수
REF_TOP = 10  # weighted_overlap 정규화 분모 = 상위 10개 가중치 합 (라벨 434행: 5개 AUC 0.72 → 10개 0.77)
FLOOR = 0.6  # 시드 고유어 하한
CAP = 0.05  # 지역명·일반어 상한
SEED_IDF_MIN = 0.45  # 시드 단어라도 코퍼스에서 이보다 흔하면(정규화 IDF 미만) 하한을 주지 않는다 — '산사태'·'임야' 같은 시설명

# 재난 자체·때·행정·보도 관용어 — 어느 재난 기사에나 있다 (상한)
GENERIC = frozenset(
    {
        *_STOP,
        "화재", "산불", "침수", "붕괴", "폭발", "사고", "참사", "태풍", "호우", "폭우", "집중호우", "장마", "장마철", "지진", "산사태", "홍수", "범람", "폭염", "한파",
        "폭설", "강풍", "재난", "재해", "안전", "피해", "사망", "부상", "실종", "사망자", "부상자", "실종자", "인명", "인명피해", "구조", "구조대", "진화", "추락",
        "충돌", "전복", "유출", "누출", "붕괴사고", "화재사고", "사고로", "발생", "발생한", "숨진", "숨져", "다쳐", "중상", "경상", "부상자들", "희생자", "유가족", "유족",
        "소방", "소방당국", "소방서", "소방본부", "경찰", "경찰서", "해경", "정부", "장관", "차관", "시장", "군수", "구청장", "도지사", "지사", "의원", "국회", "대통령",
        "지자체", "자치단체", "시청", "군청", "구청", "도청", "당국", "관계자", "관계기관", "기관", "행정", "공무원", "담당자", "위원회", "의회", "시의회", "도의회",
        "기자", "뉴스", "연합뉴스", "뉴시스", "뉴스1", "보도", "취재", "오전", "오후", "새벽", "당시", "지난", "지난해", "올해", "내년", "최근", "현재", "오늘", "어제",
        "이날", "전날", "다음", "이번", "관련", "대해", "대한", "위해", "통해", "따라", "함께", "이후", "이전", "가운데", "정도", "이상", "이하", "모든", "각종", "일부",
        "전국", "지역", "주민", "시민", "국민", "현장", "상황", "문제", "원인", "조사", "결과", "대책", "점검", "긴급", "특별", "종합", "총력", "대비", "예방", "대응",
        "지원", "강화", "추진", "계획", "예정", "마련", "실시", "확대", "개선", "검토", "관리", "운영", "시설", "공사", "사업", "예산", "복구", "피해액", "피해복구",
        "밝혔다", "말했다", "전했다", "설명했다", "했다", "있다", "없다", "것으로", "이라고", "등을", "등이", "있는", "없는", "위한", "하는", "따르면",
    }
)
_ADMIN_SUFFIX_RE = re.compile(r"(특별자치시|특별자치도|특별시|광역시|시|군|구|도|청|읍|면|동|리)$")
_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9][가-힣A-Za-z0-9~·\-]*")
_NUMERIC_RE = re.compile(r"^\d")
# 3음절 이상 어절의 용언 활용꼴 ('파손되고'·'발표했지만'·'사고였나'·'지났다'·'붕괴해'·'구체적인'). 2음절은 대개 명사라 보지 않는다
_INFLECTED_RE = re.compile(r"(다|고|며|나|지만|으나|는데|면서|도록|해서|하여|되어|됐|했|였|었|았|된|한|할|될|적인|해|돼|져|던|서|면|자)$")
_NOUN_KEEP = ("창고", "신고", "사고", "경고", "보고", "광고", "공고", "최고", "재고", "금고", "바다", "기한", "제한", "권한", "부서", "문서", "증서", "도서")


def norm_token(raw: str) -> str | None:
    """어절 하나 → 비교용 명사 토큰 (조사 떼기). 숫자로 시작하거나 1음절·용언꼴이면 None."""
    t = raw.strip("~·-")
    if len(t) < 2 or _NUMERIC_RE.match(t):
        return None
    for _ in range(2):
        if t.endswith(_NOUN_TAILS):
            break
        s = _PARTICLE_RE.sub("", t)
        if s == t:
            break
        if len(s) < 2:
            return None
        t = s
    if t.endswith(_NOUN_KEEP) or t.endswith(_NOUN_TAILS):
        return t
    if t.endswith(_VERB_ENDINGS) and len(t) <= 4:
        return None
    if len(t) >= 3 and _INFLECTED_RE.search(t):
        return None
    return t


def text_tokens(text: str) -> set[str]:
    out = set()
    for raw in _TOKEN_RE.findall(text or ""):
        t = norm_token(raw)
        if t:
            out.add(t)
    return out


def article_tokens(title: str | None, hilight: str | None, tms_keywords: Iterable[str] | None) -> set[str]:
    toks = text_tokens(f"{title or ''} {hilight or ''}")
    for kw in tms_keywords or []:
        for part in str(kw).split():
            t = norm_token(part)
            if t:
                toks.add(t)
    return toks


def region_terms(*names: str) -> set[str]:
    """지역명과 접미어를 뗀 꼴 ('평택시' → 평택시·평택, '인천 동구' → 인천·동구·동)."""
    out: set[str] = set()
    for n in names:
        for part in (n or "").split():
            if not part:
                continue
            out.add(part)
            base = _ADMIN_SUFFIX_RE.sub("", part)
            if len(base) >= 2:
                out.add(base)
    return out


_ADMIN_BASES = frozenset({_ADMIN_SUFFIX_RE.sub("", a) for a in ADMIN_NAMES} | set(ADMIN_NAMES) | set(WIDE))


def is_generic(term: str, regions: set[str]) -> bool:
    if term in GENERIC or term in regions or term in _ADMIN_BASES:
        return True
    base = _ADMIN_SUFFIX_RE.sub("", term)
    return base in _ADMIN_BASES and base != term and len(base) >= 2


@dataclass
class CorpusStats:
    n_docs: int
    df: Counter

    def idf(self, term: str) -> float:
        if self.n_docs <= 1:
            return 1.0
        return math.log((self.n_docs + 1) / (self.df.get(term, 0) + 1)) / math.log(self.n_docs + 1)


def corpus_stats(rows: Iterable[tuple[str | None, str | None, list | None]]) -> CorpusStats:
    """rows = (title, hilight, tms_keywords) 전체. 문서 빈도(DF)."""
    df: Counter = Counter()
    n = 0
    for title, hilight, tms in rows:
        n += 1
        df.update(article_tokens(title, hilight, tms))
    return CorpusStats(n_docs=n, df=df)


def seed_terms(facility: str, keywords: list[str] | None, query: str | None) -> set[str]:
    """시드가 직접 지정한 말: 시설명(통째·어절), 핵심어(통째·어절), 질의의 따옴표 안 낱말."""
    out: set[str] = set()
    for phrase in [facility or "", *(keywords or []), *re.findall(r'"([^"]+)"', query or "")]:
        phrase = phrase.strip()
        if not phrase:
            continue
        if len(phrase) >= 2 and " " not in phrase:
            out.add(phrase)
        out |= text_tokens(phrase)
    return out


def build_weights(
    core_docs: list[set[str]],
    stats: CorpusStats,
    *,
    seeds: set[str],
    regions: set[str],
    max_terms: int = MAX_TERMS,
) -> dict[str, float]:
    """핵심 문서 토큰 집합들 → {단어: 가중치(0~1)}. 시드 고유어는 핵심 문서에 없어도 넣는다."""
    n = max(1, len(core_docs))
    tf: Counter = Counter()
    for d in core_docs:
        tf.update(d)
    raw: dict[str, float] = {t: (c / n) * stats.idf(t) for t, c in tf.items()}
    top = max((v for t, v in raw.items() if not is_generic(t, regions)), default=0.0) or 1.0
    w = {t: min(1.0, v / top) for t, v in raw.items()}
    for t in seeds:
        if is_generic(t, regions) or stats.idf(t) < SEED_IDF_MIN:
            continue
        w[t] = max(w.get(t, 0.0), FLOOR)
    for t in list(w):
        if is_generic(t, regions):
            w[t] = min(w[t], CAP)
    ranked = sorted(((round(v, 3), t) for t, v in w.items() if v >= 0.05), key=lambda x: (-x[0], x[1]))
    return {t: v for v, t in ranked[:max_terms]}


def weighted_overlap(weights: dict[str, float] | None, text: str) -> float:
    """기사 본문(부분 문자열)에 나온 사건 단어의 가중치 합 / 상위 REF_TOP 합 — 0~1."""
    if not weights:
        return 0.0
    ref = sum(sorted(weights.values(), reverse=True)[:REF_TOP]) or 1.0
    hit = sum(v for t, v in weights.items() if t in text)
    return round(min(1.0, hit / ref), 3)


def sentence_weight(weights: dict[str, float] | None, sentence: str) -> float:
    """문장 하나의 사건 단어 점수 — 가장 무거운 단어 하나 + 나머지 합의 절반, 1 에서 자른다."""
    if not weights:
        return 0.0
    hits = sorted((v for t, v in weights.items() if t in sentence), reverse=True)
    if not hits:
        return 0.0
    return round(min(1.0, hits[0] + 0.5 * sum(hits[1:])), 3)


def core_window(occurred_at, published_at) -> bool:
    return published_at is not None and abs((published_at - occurred_at).days) <= CORE_DAYS
