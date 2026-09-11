"""행동 문장에서 검색·게이트용 명사 토큰을 뽑는 소형 규칙 (형태소 분석기 없이)."""

from __future__ import annotations

import re

_PARTICLE_RE = re.compile(r"(으로부터|에서는|으로|에서|에게|까지|부터|이라|라고|하고|에는|은|는|이|가|을|를|의|에|과|와|도|로)$")
_VERB_ENDINGS = ("다", "고", "며", "해", "돼", "져", "던", "지", "면", "서", "니", "게", "히", "함", "하기")
_STOP = {"관내", "지역", "구간", "일대", "이번", "해당", "전체", "전면", "각각", "대한", "위한", "통해", "및", "등", "내년", "올해", "이후"}


def action_terms(text: str, exclude: list[str] | tuple[str, ...] = (), limit: int = 5) -> list[str]:
    toks = re.findall(r"[가-힣A-Za-z]{2,}", text or "")
    out: list[str] = []
    for t in toks:
        if any(ch.isdigit() for ch in t):
            continue
        t2 = _PARTICLE_RE.sub("", t)
        if len(t2) < 2 or t2.endswith(_VERB_ENDINGS) or t2 in _STOP or t2 in out:
            continue
        if any(t2 in e or e in t2 for e in exclude if e):
            continue
        out.append(t2)
    return sorted(out, key=len, reverse=True)[:limit]
