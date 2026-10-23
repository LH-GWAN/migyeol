"""게시판 항목 제목 ↔ 사건 매칭 — 시설명·사건 핵심어·약속 행동 키워드(promise_terms)의 토큰 겹침 점수 (v2 §3).

가중치: 시설명 0.6, 핵심어 0.3, 약속 행동 키워드 0.3 (합계 상한 1.0). 기본 임계값 `NOTICE_MATCH_THRESHOLD`=0.5 이므로
시설명 하나, 또는 핵심어·행동 키워드 두 개가 제목에 있어야 저장된다. 여러 단어 용어는 모든 단어가 제목에 있어야 일치.
더 긴 용어에 포함된 짧은 용어는 중복으로 세지 않는다('달빛지하차도' 가 맞으면 '지하차도' 는 더하지 않음).
"""

from __future__ import annotations

from dataclasses import dataclass

W_FACILITY = 0.6
W_KEYWORD = 0.3
W_PROMISE = 0.3
MIN_TERM_CHARS = 2


@dataclass(frozen=True)
class Term:
    text: str
    weight: float
    kind: str  # facility|keyword|promise


def _norm(s: str) -> str:
    return "".join((s or "").split()).lower()


def event_terms(facility: str | None, keywords: list | None, promise_terms: list | None) -> list[Term]:
    out: list[Term] = []
    seen: set[str] = set()
    for text, weight, kind in (
        [(facility or "", W_FACILITY, "facility")]
        + [(str(k), W_KEYWORD, "keyword") for k in (keywords or [])]
        + [(str(k), W_PROMISE, "promise") for k in (promise_terms or [])]
    ):
        t = " ".join(text.split())
        if len(_norm(t)) < MIN_TERM_CHARS or _norm(t) in seen:
            continue
        seen.add(_norm(t))
        out.append(Term(t, weight, kind))
    return out


def _hit(term: str, title_norm: str) -> bool:
    return all(_norm(tok) in title_norm for tok in term.split())


def score_title(title: str, terms: list[Term]) -> tuple[float, list[str]]:
    """제목 점수(0~1)와 일치한 용어 목록."""
    tn = _norm(title)
    hits = [t for t in terms if _hit(t.text, tn)]
    hits.sort(key=lambda t: -len(_norm(t.text)))
    kept: list[Term] = []
    for t in hits:
        if any(_norm(t.text) in _norm(k.text) for k in kept):
            continue  # 더 긴 용어에 포함된 짧은 용어는 한 번만
        kept.append(t)
    score = min(1.0, sum(t.weight for t in kept))
    return round(score, 3), [t.text for t in kept]
