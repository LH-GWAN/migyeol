"""행동 문장에서 검색·게이트용 명사 토큰을 뽑는 소형 규칙 (형태소 분석기 없이).

약속 행동 키워드로 후속 기사를 재검색·재연결하므로, 일반어가 섞이면 무관 기사가 대량으로 붙는다.
그래서 일반어·단위·관형형을 적극적으로 거르고, 시설명·시드 핵심어(exclude)와 겹치는 토큰은 뺀다.
"""

from __future__ import annotations

import re

_PARTICLE_RE = re.compile(r"(으로부터|에서는|으로|에서|에게|까지|부터|이라|라고|하고|에는|은|는|이|가|을|를|의|에|과|와|도|로)$")
# 분명한 용언 어미만. 한 글자 '고·지·면·서' 는 창고·문서·측면 같은 명사와 겹쳐 쓰지 않는다
_VERB_ENDINGS = ("다", "며", "해", "돼", "져", "던", "하기", "한", "된", "할", "될", "하는", "되는", "했")
_UNIT_RE = re.compile(r"^(km|m|㎞|㎡|곳|건|명|대|호|차|년|월|일|개|톤|t)$", re.IGNORECASE)
_STOP = {
    "관내", "지역", "구간", "일대", "이번", "해당", "전체", "전면", "각각", "대한", "위한", "통해", "및", "등", "내년", "올해", "이후",
    # 일반 행동·대상어 (pc-8): 같은 지역 무관 기사가 이 단어 2개만으로 연결되지 않게 한다
    "대책", "지원", "주민", "조사", "원인", "완료", "추진", "강화", "점검", "철저", "노후", "용량", "전수", "진입", "마련", "실시",
    "확대", "개선", "검토", "계획", "대상", "시설", "사업", "조치", "안전", "피해", "복구", "예산", "확보", "방안", "대응", "관리",
    "운영", "준비", "시행", "진행", "착수", "예정", "목표", "재발", "방지", "사고", "현장", "주변", "인근", "구역", "전반",
}


def action_terms(text: str, exclude: list[str] | tuple[str, ...] = (), limit: int = 5) -> list[str]:
    toks = re.findall(r"[가-힣A-Za-z]{2,}", text or "")
    out: list[str] = []
    for t in toks:
        if any(ch.isdigit() for ch in t) or _UNIT_RE.match(t):
            continue
        t2 = _PARTICLE_RE.sub("", t)
        if len(t2) < 2 or t2.endswith(_VERB_ENDINGS) or t2 in _STOP or t2 in out:
            continue
        if any(t2 in e or e in t2 for e in exclude if e):
            continue
        out.append(t2)
    return sorted(out, key=len, reverse=True)[:limit]
