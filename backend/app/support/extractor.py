"""민간 지원 언급 추출 규칙 (docs/SUPPORT.md §2·§3).

입력은 빅카인즈 메타데이터(제목·hilight·content_200·tms_keywords)뿐이다.
  0. 기사 전체(제목+발췌+본문 200자+핵심어)에 피해·복구·이재민·구호 같은 사건 관련어가 하나도 없으면 보지 않는다
     (연결 기사 중 사건과 무관한 기업 홍보 기사 제외).
  1. 문장 단위로 지원 어휘(rules/support_terms.SUPPORT_TERMS)가 있는 문장만 본다 (가림 표현은 먼저 지운다). 촉구·수사·표창 같은
     문장은 지원 행위가 아니므로 뺀다.
  2. 그 문장의 토큰에서 기업·단체 이름을 뽑는다 — 접미(은행·그룹·재단·협회 …)·상호 접두(KT·신한 …).
     - 공공 주체(rules/actors.is_public_actor: 정부·지자체·공사·공단·소방 …)와 공직자 모임(시장군수협의회·의회·정당)은 뺀다 —
       공공기관의 지원은 '기관 발표' 영역이다. 모금 창구(공동모금회·사랑의열매)는 주체로 보지 않는다.
     - 수혜·경유 주체('…에', '…를 통해', '회장에게', '적십자사 충남지사에')는 뺀다.
     - 주체는 문장의 주어여야 한다: 주격 조사·쉼표(제목 'X, …'), '임직원들이'·'등 기업들은' 같은 연쇄, 제목 첫머리,
       'X 홍길동 대표가'. '금호타이어 화재'·'제주항공 참사' 처럼 사건 설명의 일부로만 나온 이름은 주체가 아니다.
  3. 기업·단체가 없고 '…씨'·'익명' 이 지원 어휘와 같은 문장에 있으면 actor_kind=개인 — 이름은 저장하지 않는다.
  4. 주체 이름이 사건 설명(제목·요약·시설·책임기관·핵심어)에 들어 있으면 actor_role=당사자, 아니면 제3자.
판정은 토큰 단위이고 평가 표현은 만들지 않는다. 결과는 보조 근거이며 사건 상태를 바꾸지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..rules.actors import is_public_actor
from ..rules.regions import ADMIN_BASES, ADMIN_NAMES, SURNAMES, WIDE
from ..rules.support_terms import (
    ASSOCIATION_PREFIXES,
    ASSOCIATION_SUFFIXES,
    CORPORATE_PREFIXES,
    CORPORATE_SUFFIXES,
    GENERIC_STEMS,
    PLACE_TAILS,
    SUPPORT_MASKS,
    SUPPORT_TERMS,
)

EVIDENCE_MAX = 200
ACTOR_MAX = 100

# 기사(제목+발췌+본문+핵심어)에 하나는 있어야 하는 사건 관련어 — 없으면 사건과 무관한 기업 소식으로 본다
RELEVANCE_WORDS: tuple[str, ...] = ("피해", "이재민", "복구", "수해", "유가족", "유족", "희생자", "구호", "재난", "재해", "참사", "부상자", "사망자", "실종자", "상인", "침수", "수습")
# 문장에 있으면 지원 행위가 아닌 말 (요구·수사·표창)
NEGATIVE_WORDS: tuple[str, ...] = ("촉구", "요구", "요청", "건의", "주장", "비판", "논란", "의혹", "횡령", "유용", "사기", "사칭", "체불", "거부", "반발", "수사", "압수수색", "기소", "표창", "수상", "시상", "포상", "감사패", "공로", "고발", "고소", "검토 중", "준비 중", "지원받", "지급받", "지급", "고용유지")
# 공직자 모임·의회·정당 — 민간 주체로 보지 않는다 (공공 영역)
PUBLIC_SPHERE_WORDS: tuple[str, ...] = ("시장군수", "군수구청장", "군수·구청장", "군수ㆍ구청장", "구청장협", "시장협", "의회", "의원", "의정회", "공무원", "공직자", "교섭단체", "시당", "도당", "당원", "지도부", "비대위", "국민의힘", "민주당", "정의당", "개혁신당", "혁신당", "진보당", "소득당", "미래당", "국민의당", "한국당")
# 모금 창구 — 기부자가 아니라 전달 경로
CHANNEL_WORDS: tuple[str, ...] = ("공동모금회", "모금회", "사랑의열매")
# 공공 병원·공공 조직 접미를 가진 토큰
PUBLIC_UNIT_WORDS: tuple[str, ...] = ("서울대", "국립", "도립", "시립", "군립", "구립", "보훈", "의료원", "경찰", "군병원", "공단", "공사")
# 사건 설명의 일부로 흔히 이어지는 말 ('금호타이어 광주공장 화재', '제주항공 참사')
EVENT_WORDS_RE = re.compile(r"(?:화재|사고|참사|붕괴|침몰|전복|추락|폭발|침수|범람|산불|산사태|유출|탈선|충돌|매몰|누출|화재피해|사고피해)(?:와|과|로|의|에|에서|를|을|도|로|가|는|은)?$")
FACILITY_WORDS_RE = re.compile(r"(?:공장|조선소|여객기|항공기|사업장|현장|제철소|변전소|지점|매장|공사장|발전소|물류센터|창고|선박|어선|버스|열차|차량|기내|백화점|점)$")
# 주어 연쇄: 'X 임직원들이', 'X 등 기업들은', 'X 및 Y는'
CHAIN_WORDS: frozenset[str] = frozenset(
    "등 및 그리고 임직원 임직원들 직원 직원들 회원 회원들 조합원 조합원들 봉사자 봉사자들 자원봉사자 자원봉사자들 봉사단 봉사단원 단원 단원들 관계자 관계자들 측 본사 본부 지회 지부 소속 교인 신도 학생 학생들 가족 가족들 노조 노조원 구성원 구성원들 임원 임원들 대표 회장 이사장 대표이사 사장 지사장 본부장 지점장 지부장 센터장 단장 총재 총장 원장 이사 조합장 신부 목사 스님 주지".split()
)
TITLE_WORDS: frozenset[str] = frozenset("회장 이사장 대표 대표이사 사장 지사장 본부장 지점장 지부장 센터장 단장 총재 총장 원장 조합장 회장단 부회장 부회장단 명예회장 상임이사 전무 상무 지회장 지부장 단장 감독 코치 신부 목사 스님 주지 위원장 회원".split())
_COUNT_RE = re.compile(r"^\d+[여]?(?:명|개|곳|인|대|팀)?$")
_LEADING_STOP: frozenset[str] = frozenset("소속 지역 각 여러 등 및 산하 관내 지난 최근 이번 해당 같은 다른 전국 인근 현지 관련 비롯한 비롯해 위한 통해 통한 따른 그리고 또 또한 역시 함께 주변 전 현 가운데 포함 등의 이날 오늘 내일 어제 지역의 기존".split())
# 앞 토큰이 이 말로 끝나면 이름이 아니라 서술어·일반어다 ('지원 농협', '전달 봉사단' 금지)
_NOT_NAME_TAIL_RE = re.compile(r"(?:지원|전달|기탁|활동|복구|피해|통해|위해|위한|따른|대한|관련|모금|성금|기부|후원|참여|동참|진행|운영|실시|개최|마련|구성|설치|제공|방문|함께|이어|행사|위로|돕기|돕는|나선|나서|펼친|펼쳐|열린|된|한|하는|되는|있는|없는|적인|했다|된다|한다|입은|입는|겪는|겪은|찾아|찾은|통하|위하)$")

_SENT_SPLIT_RE = re.compile(r"(?<=[.!?。])\s+|\.{2,}|…+|\n+|\s▲|\s■|\s◆|\s◇|\s○|\s●")
_TOKEN_RE = re.compile(r"[^\s,、/=|\[\]［］【】<>▲■◆◇○●〈〉《》「」『』※:]+")
_PAREN_RE = re.compile(r"\([^()]{0,80}\)|（[^（）]{0,80}）|\[[^\[\]]{0,40}\]|［[^［］]{0,40}］")
_SUBJECT_PARTICLE_RE = re.compile(r"(?:에서는|에서도|에서|은|는|이|가|도|측은|측이|측도|측)$")
_STRIP_PARTICLE_RE = re.compile(r"(?:들)?(?:에서는|에서도|에서|으로는|으로도|으로|로는|로도|로|에게도|에게|에도|에|께|한테|은|는|이|가|도|과|와|의|을|를|측은|측이|측도|측에|측|이며)$")
_RECIPIENT_PARTICLE_RE = re.compile(r"(?:에게도|에게|에도|에|를|을|께|한테|측에|으로|로)$")
_CO_PARTICLE_RE = re.compile(r"(?:과|와|이며)$")
_PERSON_RE = re.compile(r"(?<![가-힣A-Za-z])([가-힣]{1,4})\s?씨(?=[가-힣]?)")
_NOT_PERSON_STEMS = frozenset("불 날 솜 글 말 마음 아저 아가 김치 씨 호 생 아기 옥 반 구".split())
_ANON_RE = re.compile(r"익명|독지가|이름을 밝히지 않은")
_PREFIX_TAIL_RE = re.compile(r"^[A-Za-z0-9가-힣&]*$")
_PARTICLE_ONLY_RE = re.compile(r"은|는|이|가|을|를|의|도|와|과|에|에서|으로|로|들|측")
_PERSON_NAME_RE = re.compile(r"^[가-힣]{2,3}$")
_WIDE_SET = frozenset(WIDE)
_BRANCH_RE = re.compile(r"^[가-힣A-Za-z0-9]{1,14}(?:본부|지점|지사|지부|지회|지역본부|지역단|사업단|지구|지역|협의회|봉사관|봉사회)$")
_PUBLIC_UNITS = frozenset("소방본부 경찰서 소방서 해양경찰서 교육청 시청 군청 구청 도청 본부 지방본부".split())
_UNIT_TAIL_RE = re.compile(r"(?:병원|센터|본부|지부|지회|지점|지사|봉사단|봉사회|협의회)$")

_CORP_SUFFIXES = tuple(sorted(CORPORATE_SUFFIXES, key=len, reverse=True))
_ASSOC_SUFFIXES = tuple(sorted(ASSOCIATION_SUFFIXES, key=len, reverse=True))
_CORP_PREFIXES = tuple(sorted(CORPORATE_PREFIXES, key=len, reverse=True))
_ASSOC_PREFIXES = tuple(sorted(ASSOCIATION_PREFIXES, key=len, reverse=True))
_ALL_PREFIXES = tuple(sorted(set(CORPORATE_PREFIXES) | set(ASSOCIATION_PREFIXES), key=len, reverse=True))
_TERMS = tuple(sorted(SUPPORT_TERMS, key=len, reverse=True))
_MASKS = tuple(sorted(SUPPORT_MASKS, key=len, reverse=True))
# 두 글자 어휘 '기부' 는 '경기부지사'·'중기부'·'2분기부터' 안에서도 나오므로 어절 첫머리(또는 허용 접두 뒤)에서만 센다
_GIBU_RE = re.compile(r"(?<![가-힣A-Za-z0-9])기부|(?<=재능|현금|현물|물품|금품|연탄|헌혈)기부|(?<=쌀)기부")
_TERM_HEAD_ONLY = {"기부": _GIBU_RE}


@dataclass
class Mention:
    actor: str  # 기업·단체 이름, 개인은 '개인'
    actor_kind: str  # 기업|단체|개인
    support_terms: list[str]
    evidence_span: str
    core: str = ""  # 당사자 판정·중복 제거에 쓰는 핵심 이름 (공백 제거)
    actor_role: str = "제3자"
    sources: list[str] = field(default_factory=list)


@dataclass
class _Tok:
    raw: str  # 조사 포함 원 토큰
    core: str  # 조사를 뗀 토큰
    comma: bool  # 바로 뒤에 쉼표
    dotted: bool  # 바로 앞이 가운뎃점 (농·축협)


# ---------------------------------------------------------------------------
# 문장·어휘
# ---------------------------------------------------------------------------


def sentences(text: str | None) -> list[str]:
    """제목·hilight·content_200 을 문장으로 나눈다. hilight 의 '..' 생략 표시도 경계로 본다."""
    if not text:
        return []
    out = []
    for s in _SENT_SPLIT_RE.split(text):
        s = re.sub(r"\s+", " ", s or "").strip(" .·-—=|")
        if len(s) >= 4:
            out.append(s)
    return out


def mask_terms(sentence: str) -> str:
    s = sentence
    for m in _MASKS:
        s = s.replace(m, " " * len(m))
    return s


def support_terms_in(sentence: str) -> list[str]:
    """문장에서 맞은 지원 어휘 (긴 것 우선. '성금 전달' 과 '성금' 처럼 겹치는 어휘는 함께 둔다)."""
    masked = mask_terms(sentence)
    out = []
    for t in _TERMS:
        pat = _TERM_HEAD_ONLY.get(t)
        if pat is not None:
            if pat.search(masked):
                out.append(t)
        elif t in masked:
            out.append(t)
    return out


def is_relevant_article(*texts: str | None, keywords: list[str] | None = None) -> bool:
    blob = " ".join([t or "" for t in texts] + [k or "" for k in (keywords or [])])
    return any(w in blob for w in RELEVANCE_WORDS)


def is_negative_sentence(sentence: str) -> bool:
    return any(w in sentence for w in NEGATIVE_WORDS)


# ---------------------------------------------------------------------------
# 주체 토큰
# ---------------------------------------------------------------------------


def _strip(tok: str) -> str:
    t = tok.strip("“”‘’\"'()[]<>「」『』,.;:·ㆍ‧")
    if len(t) <= 2:
        return t
    core = _STRIP_PARTICLE_RE.sub("", t)
    return core if len(core) >= 2 else t


def _tokens(sentence: str) -> list[_Tok]:
    s = sentence.replace("(주)", "㈜").replace("（주）", "㈜")
    s = _PAREN_RE.sub("", s)  # '신한은행(은행장 정상혁)은' → '신한은행은' (조사를 붙여 둔다)
    s = re.sub(r"(?<=[가-힣])-(?=[가-힣])", " ", s)  # '신한은행-인천' (영문 상호 S-OIL 은 그대로)
    toks: list[_Tok] = []
    for m in _TOKEN_RE.finditer(s):
        raw = m.group()
        parts = re.split(r"[·ㆍ‧]", raw)
        for k, part in enumerate(parts):
            if not part:
                continue
            last = k == len(parts) - 1
            after = s[m.end() : m.end() + 1]
            toks.append(_Tok(raw=part, core=_strip(part), comma=last and after == ",", dotted=k > 0))
    return toks


def _has_subject_particle(raw: str) -> bool:
    t = raw.strip("“”‘’\"'()[],.;:")
    return len(t) > 2 and _SUBJECT_PARTICLE_RE.search(t) is not None and not _RECIPIENT_PARTICLE_RE.search(t)


def _has_recipient_particle(raw: str) -> bool:
    t = raw.strip("“”‘’\"'()[],.;:")
    return len(t) > 2 and _RECIPIENT_PARTICLE_RE.search(t) is not None and not t.endswith(("에서", "에서는", "에서도"))


def _cut_caption(core: str) -> str:
    """'사진=인천시청NH농협은행'·'인천시청점신한은행' 처럼 캡션이 붙은 토큰은 상호 접두 앞을 잘라내고, '더본코리아더본코리아' 는 한 번만 남긴다."""
    for p in _ALL_PREFIXES:
        idx = core.find(p)
        if idx > 0:
            head = core[:idx]
            if any(w in head for w in ("시청", "군청", "구청", "도청", "제공", "사진", "DB", "기자", "출처", "자료")) or is_public_actor(head):
                core = core[idx:]
                break
    for p in _ALL_PREFIXES:
        if core.startswith(p) and core[len(p):].startswith(p):
            return core[len(p):]
    return core


def _public_like(core: str) -> bool:
    return is_public_actor(core) or core.endswith(("의료원", "공단", "공사", "보건소", "교육청", "시청", "군청", "구청", "도청"))


def classify_token(core: str) -> str | None:
    """토큰이 기업·단체 이름이면 '기업'|'단체', 아니면 None. 공공 주체·공직자 모임·모금 창구·일반명사·지명은 None."""
    core = _cut_caption(core or "")
    if not core or len(core) < 2 or len(core) > 40:
        return None
    if any(w in core for w in PUBLIC_SPHERE_WORDS) or any(w in core for w in CHANNEL_WORDS):
        return None
    if core.startswith("㈜") and len(core) >= 3:
        return "기업"
    if is_public_actor(core):
        return None
    for p in _ASSOC_PREFIXES:
        if core.startswith(p):
            tail = core[len(p):]
            if _PREFIX_TAIL_RE.match(tail) and not tail.endswith(PLACE_TAILS):
                return "단체"
    for sfx in _ASSOC_SUFFIXES:
        if core.endswith(sfx):
            stem = core[: -len(sfx)]
            if not stem or stem in GENERIC_STEMS or stem.endswith(("피해", "사고")):
                return None
            if sfx == "병원" and any(w in stem for w in PUBLIC_UNIT_WORDS):
                return None
            return "단체"
    for p in _CORP_PREFIXES:
        if core.startswith(p):
            tail = core[len(p):]
            if tail.startswith(p):
                tail = tail[len(p):]  # '더본코리아더본코리아' (캡션 중복)
            if _PREFIX_TAIL_RE.match(tail) and not tail.endswith(PLACE_TAILS) and not _PARTICLE_ONLY_RE.fullmatch(tail):
                return "단체" if tail.endswith(_ASSOC_SUFFIXES) else "기업"
    for sfx in _CORP_SUFFIXES:
        if core.endswith(sfx):
            stem = core[: -len(sfx)]
            if not stem or stem in GENERIC_STEMS or stem.endswith(("피해", "사고", "불법", "부실")):
                return None
            return "기업"
    return None


def _is_region_token(core: str) -> bool:
    return core in _WIDE_SET or core in ADMIN_NAMES or core in ADMIN_BASES


def _is_title_word(core: str) -> bool:
    return core in TITLE_WORDS or (len(core) <= 8 and core.endswith(tuple(TITLE_WORDS)) and core[: -2] not in ("", "부"))


def _is_person_name(core: str) -> bool:
    """'이은주'·'홍길동' 꼴 (성씨 + 1~2자). '전폭적'·'관계자' 같은 일반어는 아니다."""
    return (
        bool(_PERSON_NAME_RE.match(core))
        and core[0] in SURNAMES
        and core[-1] not in "적성화들인은는이가을를의로에서과와도체회장단부국실과청시군구도"
        and not _is_region_token(core)
        and core not in CHAIN_WORDS
        and classify_token(core) is None
    )


def _in_event_phrase(toks: list[_Tok], i: int) -> bool:
    """'금호타이어 광주공장 화재'·'제주항공 참사'·'HD현대삼호 화재,' 처럼 사건 설명의 일부인지."""
    nxt = toks[i + 1].raw if i + 1 < len(toks) else ""
    if EVENT_WORDS_RE.search(nxt):
        return True
    if FACILITY_WORDS_RE.search(_strip(nxt)) and i + 2 < len(toks) and EVENT_WORDS_RE.search(toks[i + 2].raw):
        return True
    return False


def _chain_subject(toks: list[_Tok], j: int) -> bool:
    """j 번째 토큰 뒤로 'X 임직원 50여명이'·'X 등 기업들은'·'X 및 Y는' 연쇄가 주격으로 끝나는지 (최대 4토큰)."""
    for k in range(j + 1, min(j + 5, len(toks))):
        t = toks[k]
        if _has_recipient_particle(t.raw):
            return False
        if _has_subject_particle(t.raw) or t.comma:
            return True
        if t.core in CHAIN_WORDS or _COUNT_RE.match(t.core) or _is_region_token(t.core) or classify_token(t.core) is not None or _is_person_name(t.core):
            continue
        return False
    return False


def _subject_ok(toks: list[_Tok], i: int, j: int, is_title: bool, strict: bool) -> bool:
    """i..j(지부 포함) 토큰이 문장의 주어로 보이는지. strict(사건 당사자 이름)이면 제목 첫머리 규칙을 쓰지 않는다."""
    last = toks[j]
    if last.comma or _has_subject_particle(last.raw):
        return True
    if _chain_subject(toks, j):
        return True
    if j + 2 < len(toks) and _is_person_name(toks[j + 1].core) and _is_title_word(toks[j + 2].core) and not _has_recipient_particle(toks[j + 2].raw):
        return True  # '현우토건㈜ 이은주 대표,'
    if not strict and is_title and all(_is_region_token(t.core) or t.core in _LEADING_STOP or len(t.core) <= 1 for t in toks[:i]):  # i = 이름 시작 토큰
        return True  # 제목 첫머리 'KT그룹 수해 성금 10억 기탁'
    return False


def _name_like(core: str) -> bool:
    """앞 토큰이 이름의 일부로 쓸 만한지 ('아이다움 봉사단', '평해구씨 대종친회', '손해보험 사회공헌협의회')."""
    return (
        len(core) >= 2
        and core not in _LEADING_STOP
        and core not in GENERIC_STEMS
        and core not in CHAIN_WORDS
        and not _COUNT_RE.match(core)
        and not _is_region_token(core)
        and not _public_like(core)
        and not _is_person_name(core)
        and not _NOT_NAME_TAIL_RE.search(core)
        and not any(w in core for w in PUBLIC_SPHERE_WORDS)
    )


def _bare_suffix(core: str) -> bool:
    """접미만으로 된 토큰 ('봉사단', '대종친회', '협의회', '손해보험') — 앞 토큰과 합쳐야 이름이 된다."""
    return core in ASSOCIATION_SUFFIXES or core in CORPORATE_SUFFIXES or core in ("사회공헌협의회", "봉사단", "협의회")


def _suffix_kind(core: str) -> str:
    return "기업" if core in CORPORATE_SUFFIXES else "단체"


def actors_in(sentence: str, is_title: bool = False, party_blob: str = "") -> list[tuple[str, str, str]]:
    """문장의 기업·단체 주체 [(표시 이름, 종류, 핵심 이름)]. 수혜·경유·사건 설명 속 이름은 뺀다."""
    toks = _tokens(sentence)
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    i = 0
    while i < len(toks):
        t = toks[i]
        core = _cut_caption(t.core)
        kind = classify_token(core)
        prev = toks[i - 1] if i > 0 else None
        pcore = _cut_caption(prev.core) if prev is not None else ""
        prev_free = prev is not None and not _has_recipient_particle(prev.raw) and not _has_subject_particle(prev.raw) and not prev.comma
        start = i  # 이름이 앞 토큰부터 시작하면 i-1
        parts: list[str] = []
        key = ""
        if kind is None:
            # 접미만 있는 토큰: '아이다움 봉사단', '농·축협'
            if _bare_suffix(core) and prev_free and (t.dotted and len(pcore) >= 1 and classify_token(pcore) is None and not _bare_suffix(pcore) and pcore not in _LEADING_STOP or (not t.dotted and _name_like(pcore))):
                kind = _suffix_kind(core)
                joiner = "·" if t.dotted else " "
                parts = [pcore + joiner + core]
                key = (pcore + joiner + core).replace(" ", "")
                start = i - 1
            else:
                i += 1
                continue
        # 공공 조직의 산하 단위 ('경기도의료원 의정부병원') 는 공공
        if prev is not None and _public_like(pcore) and _UNIT_TAIL_RE.search(core):
            i += 1
            continue
        # 지부·지점 합치기 ('대한적십자사 대전세종지사는') — 지부가 수혜자('충남지사에')면 전체를 버린다
        j = i
        if i + 1 < len(toks) and _BRANCH_RE.match(toks[i + 1].core) and classify_token(toks[i + 1].core) in (None, "단체") and toks[i + 1].core not in _PUBLIC_UNITS and not _has_subject_particle(t.raw) and not t.comma:
            if _has_recipient_particle(toks[i + 1].raw):
                i += 2
                continue
            j = i + 1
        if _has_recipient_particle(t.raw):
            i += 1
            continue
        nxt = toks[j + 1] if j + 1 < len(toks) else None
        if nxt is not None and _has_recipient_particle(nxt.raw) and (_is_title_word(nxt.core) or _BRANCH_RE.match(nxt.core) or classify_token(nxt.core) is not None or _UNIT_TAIL_RE.search(nxt.core)):
            i = j + 1
            continue  # '희망브리지 전국재해구호협회에', 'X 회장에게'
        if nxt is not None and _is_title_word(nxt.core) and j + 2 < len(toks) and _has_recipient_particle(toks[j + 2].raw):
            i = j + 1
            continue
        if not key:
            key = core.replace(" ", "")
        strict = bool(party_blob) and len(key) >= 2 and key in party_blob
        if not (t.comma or _has_subject_particle(t.raw)) and _in_event_phrase(toks, j):
            i = j + 1
            continue
        if _CO_PARTICLE_RE.search(toks[j].raw) and not _chain_subject(toks, j):
            i = j + 1
            continue  # 'A와 B는' 만 공동 주어. '농협과 지역농축협 계좌에' 는 아니다
        if not _subject_ok(toks, start, j, is_title, strict):
            i = j + 1
            continue
        if not parts:
            parts = [core]
            if prev_free:
                if _is_region_token(pcore):
                    parts.insert(0, pcore)
                elif not t.dotted and prev.raw == prev.core and classify_token(pcore) is not None and pcore.replace(" ", "") in seen and pcore.replace(" ", "") != key:
                    # 바로 앞이 이미 뽑힌 주체면 이 토큰은 그 지부·산하 ('대한적십자사 경기도협의회') — 이름만 늘린다
                    idx = next(n for n, (_nm, _k, kk) in enumerate(out) if kk == pcore.replace(" ", ""))
                    nm, kk_kind, kk = out[idx]
                    if core not in nm:
                        out[idx] = ((nm + " " + core)[:ACTOR_MAX], kk_kind, kk)
                    i = j + 1
                    continue
        if j > i:
            parts.append(toks[j].core)
        name = " ".join(parts)[:ACTOR_MAX]
        if key in seen:
            i = j + 1
            continue
        seen.add(key)
        out.append((name, kind, key))
        i = j + 1
    return out


def individual_in(sentence: str) -> bool:
    """'…씨가/는 성금을', '익명의 독지가' — 개인 기부. '불씨는'·'날씨' 는 아니다."""
    for m in _PERSON_RE.finditer(sentence):
        stem = m.group(1)
        if stem in _NOT_PERSON_STEMS or stem[0] not in SURNAMES:
            continue
        after = sentence[m.end() : m.end() + 3]
        if after.startswith(("에게", "에", "를", "을", "께", "한테")):
            continue
        return True
    return _ANON_RE.search(sentence) is not None


# ---------------------------------------------------------------------------
# 기사 → 언급
# ---------------------------------------------------------------------------


def _evidence(sentence: str) -> str:
    s = re.sub(r"\s+", " ", sentence).strip()
    return s if len(s) <= EVIDENCE_MAX else s[: EVIDENCE_MAX - 1].rstrip() + "…"


def _dedup_subnames(mentions: list[Mention]) -> list[Mention]:
    """같은 기사에서 '대한적십자사'·'적십자사', 'KB국민은행'·'국민은행' 처럼 한 이름이 다른 이름에 포함되면 긴 쪽만 남긴다."""
    keep: list[Mention] = []
    for m in mentions:
        covered = False
        for other in mentions:
            if other is m or other.core == m.core:
                continue
            if m.core and other.core and m.core in other.core and len(other.core) > len(m.core):
                covered = True
                for t in m.support_terms:
                    if t not in other.support_terms:
                        other.support_terms.append(t)
                break
        if not covered:
            keep.append(m)
    return keep


def extract_article(title: str | None, hilight: str | None, content_200: str | None, keywords: list[str] | None = None, party_blob: str = "") -> list[Mention]:
    """한 기사의 민간 지원 언급. hilight·content 문장을 먼저 보고(근거 문구가 길다) 제목은 마지막.
    party_blob 은 사건 설명(공백 제거) — 당사자 이름은 주어 판정을 엄격히 한다."""
    if not is_relevant_article(title, hilight, content_200, keywords=keywords):
        return []
    by_key: dict[str, Mention] = {}
    order: list[str] = []
    ordered = [("hilight", s) for s in sentences(hilight)] + [("content", s) for s in sentences(content_200)] + [("title", s) for s in sentences(title)]
    person_hit: tuple[list[str], str] | None = None
    for src, sent in ordered:
        terms = support_terms_in(sent)
        if not terms or is_negative_sentence(sent):
            continue
        found = actors_in(sent, is_title=(src == "title"), party_blob=party_blob)
        for name, kind, key in found:
            m = by_key.get(key)
            if m is None:
                m = Mention(actor=name, actor_kind=kind, support_terms=list(terms), evidence_span=_evidence(sent), core=key, sources=[src])
                by_key[key] = m
                order.append(key)
            else:
                for t in terms:
                    if t not in m.support_terms:
                        m.support_terms.append(t)
                if src not in m.sources:
                    m.sources.append(src)
        if person_hit is None and not found and individual_in(sent):
            person_hit = (list(terms), _evidence(sent))
    out = _dedup_subnames([by_key[k] for k in order])
    if not out and person_hit:
        out.append(Mention(actor="개인", actor_kind="개인", support_terms=person_hit[0], evidence_span=person_hit[1], core="", sources=["person"]))
    return out


def event_text(title: str, summary: str = "", facility: str = "", responsible_org: str = "", keywords: list[str] | None = None, aliases: list[str] | None = None) -> str:
    return re.sub(r"\s+", "", " ".join([title or "", summary or "", facility or "", responsible_org or "", *(keywords or []), *(aliases or [])]))


def assign_roles(mentions: list[Mention], event_blob: str) -> list[Mention]:
    """주체의 핵심 이름(공백 제거, 2자 이상)이 사건 설명에 들어 있으면 당사자. 사실 표시일 뿐 평가가 아니다."""
    for m in mentions:
        core = m.core
        m.actor_role = "당사자" if core and len(core) >= 2 and core in event_blob else "제3자"
    return mentions
