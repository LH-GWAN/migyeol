"""공공 주체 판별·정규화 규칙.

- 공공기관·지자체·공공시설과 공개된 공식 발언만 추적 대상이다 (개발 프롬프트 2절 4항).
- 발언 주체는 기관 단위로 정규화한다: "가온시장 홍길동" -> "가온시". 개인명은 추적 키로 쓰지 않는다.
- 빅카인즈 인용문 source 는 '이름 직함'(이상민 장관), '직함만'(안전총괄과장), '기관 관계자' 형태가 흔하다.

판정 순서
  1. 민간 표현(주민·유가족·시공사·씨 …)이 있으면 False — 단, 정부 직함(장관·지사·청장·군수 …)이 있으면 공공
  2. 책임기관 별칭(단어 경계) / 공공 단어(정부·당국·소방 …) / 기관명 접미(…시·군·청·공사·위원회) / 직함 단독
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
# 기관명 없이 직함만으로도 공공 주체로 보는 표현 ('이상민 장관', '안전총괄과장', '김영환 충북지사')
_STANDALONE_TITLES = (
    "장관",
    "차관",
    "지사",
    "청장",
    "대변인",
    "본부장",
    "단장",
    "국장",
    "과장",
    "실장",
    "팀장",
    "총리",
    "대통령",
    "서장",
    "위원장",
    "감독관",
    "군수",
    "구청장",
)
# 민간 표현이 함께 있어도 공공으로 보는 정부 직함 (시장은 '남문시장' 과 겹치므로 제외 — 기관명+시장 은 _ORG_RE 가 잡는다)
_GOV_TITLES = ("장관", "차관", "지사", "청장", "대변인", "총리", "대통령", "군수", "구청장", "서장")

_ORG_RE = re.compile(
    r"([가-힣A-Za-z]{1,10}?(?:" + "|".join(_ORG_SUFFIXES) + r"))(?:" + "|".join(_TITLE_SUFFIXES) + r")?(?![가-힣])"
)
_TITLE_RE = re.compile(r"(?:^|\s)[가-힣A-Za-z·]{0,12}(?:" + "|".join(_STANDALONE_TITLES) + r")(?![가-힣])")
_GOV_TITLE_RE = re.compile(r"(?:" + "|".join(_GOV_TITLES) + r")(?![가-힣])")

# 기관명이 없어도 공공 주체로 보는 표현
_PUBLIC_WORDS = (
    "정부",
    "당국",
    "소방",
    "경찰",
    "해경",
    "군청",
    "시청",
    "구청",
    "도청",
    "대통령",
    "국무총리",
    "총리",
    "대통령실",
    "중대본",
    "중앙재난안전대책본부",
    "감사원",
    "국무조정실",
    "국과수",
    "국토교통부",
    "행정안전부",
    "고용노동부",
    "산업통상자원부",
    "환경부",
    "해양수산부",
    "교육청",
    "도로공사",
    "철도공사",
    "수자원공사",
)
# 공공 주체가 아닌 것으로 보는 표현 (개인·민간)
_PRIVATE_WORDS = (
    "주민",
    "유가족",
    "유족",
    "피해자",
    "시민",
    "씨",
    "대표",
    "노조",
    "업체",
    "회사",
    "시공사",
    "원청",
    "하청",
    "상인",
    "변호사",
    "교수",
    "전문가",
    "목격자",
    "운전자",
    "학생",
    "기사",
    "협의회",
    "번영회",
    "입주민",
    "가족",
    "승객",
    "환자",
    "점주",
    "사장",
)


_COMPANY_RE = re.compile(r"(?:[가-힣A-Za-z]+(?:건설|산업|물산|개발|엔지니어링|건업|종합건설)|㈜|주식회사|\(주\))(?=\s|$)")


def _alias_hit(alias: str, s: str) -> bool:
    """별칭은 단어 경계에서만 인정한다: 뒤가 끝·공백·직함이어야 한다 ('가온시의회', '가온시민' 은 '가온시' 로 보지 않는다)."""
    if not alias:
        return False
    pat = re.escape(alias) + r"(?=$|\s|" + "|".join(_TITLE_SUFFIXES) + r")"
    return re.search(pat, s, flags=re.IGNORECASE) is not None


def is_public_actor(source: str, aliases: list[str] | tuple[str, ...] = ()) -> bool:
    s = (source or "").strip()
    if not s:
        return False
    if (any(w in s for w in _PRIVATE_WORDS) or _COMPANY_RE.search(s)) and not _GOV_TITLE_RE.search(s):
        return False
    if any(_alias_hit(a, s) for a in aliases):
        return True
    if any(w in s for w in _PUBLIC_WORDS):
        return True
    if _ORG_RE.search(s):
        return True
    return _TITLE_RE.search(s) is not None


def normalize_actor_org(source: str, responsible_org: str = "", aliases: list[str] | tuple[str, ...] = ()) -> str | None:
    """발언 주체 문자열 -> 기관명. 책임기관/별칭이 단어 경계로 포함되면 책임기관명으로 통일한다."""
    s = (source or "").strip()
    if not s:
        return None
    if responsible_org and _alias_hit(responsible_org, s):
        return responsible_org
    for a in aliases:
        if _alias_hit(a, s):
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
