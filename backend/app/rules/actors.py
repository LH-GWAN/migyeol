"""공공 주체 판별·정규화 규칙.

- 공공기관·지자체·공공시설과 공개된 공식 발언만 추적 대상이다 (개발 프롬프트 2절 4항).
- 발언 주체는 기관 단위로 정규화한다: "가온시장 홍길동" -> "가온시". 개인명은 추적 키로 쓰지 않는다.
"""

from __future__ import annotations

import re

# 기관명 접미 (긴 것을 먼저 두어 정규식 대안이 올바르게 매칭되게 한다)
_ORG_SUFFIXES = (
    "특별자치시",
    "특별자치도",
    "특별시",
    "광역시",
    "해양경찰서",
    "소방본부",
    "소방서",
    "경찰서",
    "교육청",
    "위원회",
    "공사",
    "공단",
    "본부",
    "의회",
    "청",
    "시",
    "군",
    "구",
    "도",
    "부",
    "처",
)
# 기관명 뒤에 붙을 수 있는 직함
_TITLE_SUFFIXES = (
    "장관",
    "차관",
    "청장",
    "서장",
    "소장",
    "본부장",
    "단장",
    "국장",
    "과장",
    "실장",
    "팀장",
    "대변인",
    "관계자",
    "지사",
    "수",
    "장",
)
_ORG_RE = re.compile(
    r"([가-힣A-Za-z]{1,10}?(?:" + "|".join(_ORG_SUFFIXES) + r"))(?:" + "|".join(_TITLE_SUFFIXES) + r")?(?![가-힣])"
)
# 기관명이 없어도 공공 주체로 보는 표현
_PUBLIC_WORDS = ("정부", "당국", "소방당국", "경찰", "해경", "군청", "시청", "구청", "도청", "국토교통부", "행정안전부", "고용노동부", "산업통상자원부", "환경부", "해양수산부")
# 공공 주체가 아닌 것으로 보는 표현 (개인·민간)
_PRIVATE_WORDS = ("주민", "유가족", "피해자", "시민 ", "씨", "대표", "노조", "업체", "회사", "변호사", "교수", "전문가", "목격자", "운전자", "학생")


def is_public_actor(source: str, aliases: list[str] | tuple[str, ...] = ()) -> bool:
    s = (source or "").strip()
    if not s:
        return False
    low = s.lower()
    if any(a and a.lower() in low for a in aliases):
        return True
    if any(w in s for w in _PRIVATE_WORDS) and not _ORG_RE.search(s):
        return False
    if any(w in s for w in _PUBLIC_WORDS):
        return True
    return bool(_ORG_RE.search(s))


def normalize_actor_org(source: str, responsible_org: str = "", aliases: list[str] | tuple[str, ...] = ()) -> str | None:
    """발언 주체 문자열 -> 기관명. 책임기관/별칭이 포함되면 책임기관명으로 통일한다."""
    s = (source or "").strip()
    if not s:
        return None
    low = s.lower()
    if responsible_org and responsible_org.lower() in low:
        return responsible_org
    for a in aliases:
        if a and a.lower() in low:
            return responsible_org or a
    m = _ORG_RE.search(s)
    if m:
        org = m.group(1)
        org = re.sub(r"(시|군|구|도)청$", r"\1", org)  # 가온시청 -> 가온시
        return org
    for w in _PUBLIC_WORDS:
        if w in s:
            return w
    return None
