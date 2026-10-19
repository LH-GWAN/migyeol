"""공공 주체 판별·정규화 규칙.

- 공공기관·지자체·공공시설과 공개된 공식 발언만 추적 대상이다 (개발 프롬프트 2절 4항).
- 발언 주체는 기관 단위로 정규화한다: "가온시장 홍길동" -> "가온시". 개인명은 추적 키로 쓰지 않는다.
- 빅카인즈 인용문 source 는 '이름 직함'(이상민 장관), '직함만'(안전총괄과장), '기관 관계자' 형태가 흔하다.

판정은 공백·가운뎃점으로 나눈 **토큰** 단위로 한다 (문자열 어디에나 있는 부분 일치로 탈락·통과시키지 않는다).
  1. 민간 토큰(주민·유가족·시공사·…씨·…건설·정당)이 있으면 False — 단, 정부 직함(장관·지사·청장·군수 …)이 있으면 공공.
     기관 직함 토큰(사장·대표·이사장)은 앞에 공공 기관 토큰이 있으면 민간으로 보지 않는다 ('한국도로공사 사장').
  2. 책임기관 별칭(단어 경계) / 공공 단어(정부·당국·소방·LH …) / 기관명 접미(…청·공사·위원회·과·실·국) / 직함 단독.
     한 글자 접미(시·군·구·도)는 사람 이름(김민구·이영도)과 겹치므로 뒤에 직함·공백이 오거나 별칭·광역명일 때만 기관으로 본다.
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
    "주민센터",
    "행정복지센터",
    "위원회",
    "공사",
    "공단",
    "본부",
    "의회",
    "청",
    "부",
    "처",
    "과",
    "실",
    "국",
    "시",
    "군",
    "구",
    "도",
)
# 사람 이름과 겹치는 한 글자 접미 — 뒤에 직함/공백이 오거나 별칭·광역명일 때만 기관으로 본다
_AMBIGUOUS_SUFFIXES = ("시", "군", "구", "도", "부", "국", "과", "실")
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
# 민간 표현이 함께 있어도 공공으로 보는 정부 직함 (시장은 '남문시장' 과 겹치므로 제외 — 기관명+시장 은 _ORG_RE 가 잡는다.
# 대변인은 정당 대변인이 있어 제외 — 기관명+대변인 은 기관 토큰이 잡는다)
_GOV_TITLES = ("장관", "차관", "지사", "청장", "총리", "대통령", "군수", "구청장", "서장")
# 광역 지자체 (한 글자 접미라도 단독으로 기관)
_WIDE_REGIONS = ("서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주", "충청북", "충청남", "전라북", "전라남", "경상북", "경상남")

_ORG_RE = re.compile(
    r"([가-힣A-Za-z]{1,10}?(?:" + "|".join(_ORG_SUFFIXES) + r"))(?:" + "|".join(_TITLE_SUFFIXES) + r")?$"
)
_TITLE_RE = re.compile(r"^[가-힣A-Za-z·]{0,12}(?:" + "|".join(_STANDALONE_TITLES) + r")$")
_GOV_TITLE_RE = re.compile(r"(?:" + "|".join(_GOV_TITLES) + r")$")
_TOKEN_SPLIT = re.compile(r"[\s·,/]+")

# 기관명이 없어도 공공 주체로 보는 표현 (토큰 단위, 완전 일치 또는 토큰 끝)
_PUBLIC_WORDS = (
    "정부",
    "당국",
    "지자체",
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
    "국토부",
    "행정안전부",
    "행안부",
    "고용노동부",
    "노동부",
    "산업통상자원부",
    "산업부",
    "환경부",
    "해양수산부",
    "해수부",
    "교육청",
    "도로공사",
    "철도공사",
    "철도공단",
    "수자원공사",
    "한전",
    "한국전력",
    "코레일",
    "LH",
    "토지주택공사",
    "주민센터",
    "행정복지센터",
    "시",
    "군",
    "구",
    "도",
)
# 공공 주체가 아닌 것으로 보는 표현 (토큰 끝 일치: '입주민', '유가족협의회' 는 '협의회'. '시민안전과장' 은 '시민' 으로 끝나지 않는다)
_PRIVATE_WORDS = (
    "주민",
    "주민들",
    "유가족",
    "유족",
    "피해자",
    "시민",
    "시민들",
    "씨",
    "노조",
    "업체",
    "회사",
    "시공사",
    "원청",
    "하청",
    "상인",
    "상인회",
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
    "당",
    "정당",
    "국민의힘",
)
# 기관 직함: 앞에 공공 기관 토큰이 있으면 공공('한국도로공사 사장'), 없으면 민간('식당 사장', '대표')
_ORG_HEAD_TITLES = ("사장", "대표", "이사장", "회장", "대표이사", "현장소장")
# 직함 앞에 붙으면 민간·이해당사자 조직인 접두/접미 ('주민대책위원장', '유가족대책위', '민주노총 위원장', '감리단장')
_PRIVATE_PREFIXES = ("주민", "유가족", "유족", "피해자", "상인", "입주민", "시공사", "감리", "노조", "노총", "민주노총", "한국노총", "입주자")
_PRIVATE_ORG_TAILS = ("노총", "노조", "연구", "연구소", "연구원", "협회", "조합", "재단", "법인")
_PERSON_NAME_RE = re.compile(r"^[가-힣]{2,4}$")
_REGION_SUFFIXES = ("시", "군", "구", "도")

_COMPANY_RE = re.compile(r"^(?:[가-힣A-Za-z]+(?:건설|산업|물산|개발|엔지니어링|건업|종합건설)|㈜.*|주식회사|\(주\))$")


def _tokens(s: str) -> list[str]:
    return [t for t in _TOKEN_SPLIT.split(s) if t]


def _alias_hit(alias: str, s: str) -> bool:
    """별칭은 단어 경계에서만 인정한다: 뒤가 끝·공백·직함이어야 한다 ('가온시의회', '가온시민' 은 '가온시' 로 보지 않는다)."""
    if not alias:
        return False
    pat = re.escape(alias) + r"(?=$|\s|" + "|".join(_TITLE_SUFFIXES) + r")"
    return re.search(pat, s, flags=re.IGNORECASE) is not None


def _org_token(tok: str, followed_by_title: bool, aliases: tuple[str, ...] | list[str]) -> str | None:
    """토큰이 기관명(+직함)이면 기관명을 돌려준다."""
    if any(_alias_hit(a, tok) for a in aliases):
        return tok
    for w in _PUBLIC_WORDS:
        if tok == w or (len(w) >= 2 and tok.endswith(w) and not any(tok.endswith(pw) for pw in _PRIVATE_WORDS)):
            return tok if len(w) >= 2 else w
    m = _ORG_RE.match(tok)
    if not m:
        return None
    org = m.group(1)
    suffix = next(sfx for sfx in _ORG_SUFFIXES if org.endswith(sfx))
    if len(org) <= len(suffix):
        return None  # '본부장' 의 '본부' 처럼 접미만 있는 것은 기관명이 아니다
    has_title = len(tok) > len(org)
    if suffix in _AMBIGUOUS_SUFFIXES:
        stem = org[: -len(suffix)]
        wide = any(stem == r or org == r + suffix for r in _WIDE_REGIONS)
        # 사람 이름('김민구', '이영도 장관', '최영실 실장')과 구분: 두 음절 어간 + 한 글자 접미는 광역명·별칭이거나
        # 뒤에 '관계자' 가 올 때만 기관으로 본다. 직함이 붙어도 이름일 수 있어 기관으로 쓰지 않는다 (LLM 기관명으로 대체).
        # 같은 토큰 안에 직함이 붙은 '가온시장'·'누리군수' 는 기관이다. 다음 토큰이 직함인 '김민구 과장'·'이영도 장관' 은 이름일 수
        # 있어 기관으로 쓰지 않는다 — 단, 다음 토큰이 '관계자'·'대변인' 처럼 기관 뒤에만 오는 말이면 기관으로 본다.
        # 사람 이름 판정(3차 검증 1-2): 성씨 1자 + 이름 2자이고 행정구역 접미(시·군·구·도)이며 사전(시드 지역·기관·별칭 + 광역·기초 지자체)에
        # 없을 때만 이름으로 본다. 사전에 있으면('청주시', '보령시', '강남구') 직함이 뒤따라도 기관이다.
        if suffix in _REGION_SUFFIXES and _person_shaped(org, aliases) and not has_title:
            return None  # '이영도 대변인'·'김민구 관계자' 도 이름 형태면 기관으로 쓰지 않는다 (직함 토큰이 공공 여부를 정한다)
        if suffix not in _REGION_SUFFIXES and len(stem) <= 2:
            if not (has_title or followed_by_title or wide):
                return None  # '도로국'·'안전과' 처럼 부서 접미만 있는 짧은 토큰은 직함·관계자가 뒤따를 때만 기관
            if _person_shaped_dept(org) and not has_title:
                return None  # '박정국 국장'·'최영실 실장': 성씨 + 2자이고 부서 명사가 아니면 사람 이름
    if any(org.endswith(pw) for pw in _PRIVATE_WORDS if pw not in ("당",)) or org.endswith(_PRIVATE_ORG_TAILS):
        return None
    return org


def _followed_by_generic(flag) -> bool:
    return flag == "generic"


def _person_shaped(name: str, lexicon: tuple[str, ...] | list[str] = ()) -> bool:
    """'성씨 1자 + 이름 2자' 이고 행정구역 사전에 없는 3자 토큰 ('김민구', '이영도'). '청주시'·'강남구'·'중구' 는 사전에 있어 아니다."""
    from .regions import SURNAMES, is_admin_region

    n = (name or "").strip()
    if len(n) != 3 or not _PERSON_NAME_RE.match(n):
        return False
    if is_admin_region(n, lexicon) or any(_alias_hit(a, n) for a in lexicon if a):
        return False
    return n[0] in SURNAMES


def _person_shaped_dept(name: str) -> bool:
    """'박정국'·'최영실' 처럼 부서 접미(국·실·과·부)로 끝나는 3자 토큰이 사람 이름 형태인지 — 성씨로 시작하고 앞 2자가 부서 명사가 아니다."""
    from .regions import DEPT_NOUNS, SURNAMES

    n = (name or "").strip()
    return len(n) == 3 and n[0] in SURNAMES and n[:2] not in DEPT_NOUNS and n[1:] not in DEPT_NOUNS


def looks_like_person(name: str, lexicon: tuple[str, ...] | list[str] = ()) -> bool:
    """기관명으로 쓰면 안 되는 사람 이름 형태인지 (규칙 결과와 LLM actor_org 모두에 적용).
    - '김민구'·'이영도': 성씨 + 2자, 행정구역 사전·시드 사전에 없음 → True
    - '청주시'·'보령시'·'강남구'·'중구'·'도로국'·'경기도': 사전에 있거나 행정구역 접미가 아닌 부서/기관 접미 → False
    - 2자·4자 한글만으로 된 이름('홍길동 2자'·'남궁민수')은 접미·공공 단어가 없을 때만 True"""
    n = (name or "").strip()
    if not _PERSON_NAME_RE.match(n):
        return False
    if any(n == w or n.endswith(w) for w in _PUBLIC_WORDS if len(w) >= 2):
        return False
    from .regions import is_admin_region

    if is_admin_region(n, lexicon):
        return False
    if n.endswith(_REGION_SUFFIXES):
        return _person_shaped(n, lexicon)
    if n.endswith(("국", "실", "과", "부")):
        return _person_shaped_dept(n)
    if n.endswith(_ORG_SUFFIXES) or n.endswith(_TITLE_SUFFIXES):
        return False
    return True


def _analyze(source: str, aliases: tuple[str, ...] | list[str]) -> tuple[list[str], list[str], bool]:
    """반환: (기관 토큰들 + 직함 단독 토큰들, 민간 토큰들, 정부 직함 여부). 기관명만 필요하면 _org_names() 를 쓴다."""
    orgs, titles, privates, gov_title = _analyze4(source, aliases)
    return orgs + titles, privates, gov_title


def _org_names(source: str, aliases: tuple[str, ...] | list[str]) -> list[str]:
    return _analyze4(source, aliases)[0]


def _analyze4(source: str, aliases: tuple[str, ...] | list[str]) -> tuple[list[str], list[str], list[str], bool]:
    toks = _tokens(source)
    orgs: list[str] = []
    titles: list[str] = []
    privates: list[str] = []
    gov_title = False
    for i, tok in enumerate(toks):
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        # "generic": 뒤에 '관계자'(사람 이름 뒤에는 오지 않는다), True: 그 밖의 직함
        followed_by_title = ("generic" if nxt.endswith(("관계자", "대변인", "당국", "측")) else True) if nxt and (nxt.endswith(_TITLE_SUFFIXES) or nxt in _ORG_HEAD_TITLES) else False
        if _GOV_TITLE_RE.search(tok) and not tok.startswith(_PRIVATE_PREFIXES):
            gov_title = True
        if _COMPANY_RE.match(tok) or tok in ("㈜",):
            privates.append(tok)
            continue
        if tok.startswith(_PRIVATE_PREFIXES) and (tok.endswith(_TITLE_SUFFIXES) or tok.endswith(("위", "위원회", "단"))):
            privates.append(tok)  # '주민대책위원장', '유가족대책위', '감리단장'
            continue
        if tok.endswith(_PRIVATE_ORG_TAILS) or tok in ("민주노총", "한국노총"):
            privates.append(tok)
            continue
        if tok in _ORG_HEAD_TITLES or tok.endswith(_ORG_HEAD_TITLES):
            if not orgs:
                privates.append(tok)
            continue
        # '당' 은 정당명(더불어민주당·개혁신당)처럼 4자 이상일 때만 민간 표시 — '담당'·'해당' 은 아니다
        if any(tok == pw or (tok.endswith(pw) and tok != pw and not _TITLE_RE.match(tok) and (pw != "당" or len(tok) >= 4)) for pw in _PRIVATE_WORDS):
            if not _GOV_TITLE_RE.search(tok):
                privates.append(tok)
                continue
        org = _org_token(tok, followed_by_title, aliases)
        if org:
            orgs.append(org)
            continue
        if _TITLE_RE.match(tok) and len(tok) >= 2:
            titles.append(tok)  # 직함 단독('안전총괄과장', '과장', '이상민 장관' 의 '장관') — 공공이지만 기관명은 아니다
    return orgs, titles, privates, gov_title


def is_public_actor(source: str, aliases: list[str] | tuple[str, ...] = ()) -> bool:
    s = (source or "").strip()
    if not s:
        return False
    orgs, privates, gov_title = _analyze(s, aliases)
    if privates and not gov_title:
        return False
    return bool(orgs) or gov_title


def normalize_actor_org(source: str, responsible_org: str = "", aliases: list[str] | tuple[str, ...] = ()) -> str | None:
    """발언 주체 문자열 -> 기관명. 책임기관/별칭이 단어 경계로 포함되면 책임기관명으로 통일한다. 개인명은 기관으로 쓰지 않는다."""
    s = (source or "").strip()
    if not s:
        return None
    if responsible_org and _alias_hit(responsible_org, s):
        return responsible_org
    for a in aliases:
        if _alias_hit(a, s):
            return responsible_org or a
    orgs, titles, privates, gov_title = _analyze4(s, aliases)
    if privates and not gov_title:
        return None
    for org in orgs:  # 직함 단독 토큰(titles)은 기관을 특정하지 못하므로 쓰지 않는다 ('본부장' -> '본부' 금지)
        name = re.sub(r"(시|군|구|도)청$", r"\1", org)  # 가온시청 -> 가온시
        if len(name) >= 2 and not looks_like_person(name, [responsible_org, *aliases]):
            return name
    for w in _PUBLIC_WORDS:
        if len(w) >= 2 and any(t == w or t.endswith(w) for t in _tokens(s)):
            return w
    return None
