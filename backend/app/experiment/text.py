"""실험용 텍스트 규칙 — 기사 제목·하이라이트에서 지역·시설·재난 어휘·피해 규모를 뽑는다 (형태소 분석기 없이).

실기사 문장(제목·인용문)을 시드·CSV·리포트에 옮길 때는 scrub() 을 거친다: 명세 §2-2 의 단정 표현이 기사 원문에
들어 있어도 산출물에는 남지 않게 가린다(○ 로 대체). 리포트·CSV 도 금지어 검사 대상이기 때문이다.
"""

from __future__ import annotations

import re

from ..rules.regions import ADMIN_NAMES, _CITIES, _COUNTIES, WIDE

# 명세 §2-2 단정 표현 — 소스에 그대로 쓰지 않는다
_FORBIDDEN = ("미" + "이행", "불" + "이행", "거짓" + "말")


def scrub(text: str | None) -> str:
    """실기사 문장 속 단정 표현을 가린다 ('X이행' → 'X○행'). 나머지 글자는 그대로 둔다."""
    out = text or ""
    for w in _FORBIDDEN:
        if w in out:
            out = out.replace(w, w[0] + "○" + w[2:])
    return out


# §4.2 이슈 랭킹 필터용 재난 어휘 (토픽·토픽 키워드에 하나라도 있으면 상세 조회)
DISASTER_TERMS = ("침수", "붕괴", "화재", "폭발", "태풍", "호우", "산사태", "지진", "탈선", "추락", "매몰", "전복", "침몰", "누출")
# 원천 기사 제목 필터 — 실제로 일어난 사고·재난을 다룬 기사인가 (DISASTER_TERMS 보다 넓다)
HAZARD_TERMS = (
    *DISASTER_TERMS, "폭우", "홍수", "범람", "유실", "산불", "끼임", "질식", "감전", "싱크홀", "땅꺼짐", "지반침하", "낙석", "추돌", "충돌",
    "참사", "사고", "폭염", "한파", "폭설", "대설", "강풍", "무너", "화마", "불길", "사망", "숨져", "숨진", "실종", "부상", "사상자", "희생",
    "중대재해", "산재", "결빙", "블랙아이스", "역주행", "급발진", "좌초", "충돌", "화학", "유출", "가스",
)
_HAZARD_RE = re.compile("(" + "|".join(sorted(set(HAZARD_TERMS), key=len, reverse=True)) + ")")
_DISASTER_RE = re.compile("(" + "|".join(DISASTER_TERMS) + ")")
# 해외 사건 제목 (국내 공공기관 추적 대상 아님)
_FOREIGN_RE = re.compile(r"(미국|일본|중국|대만|튀르키예|터키|이탈리아|프랑스|독일|영국|러시아|우크라이나|인도|인도네시아|필리핀|베트남|태국|멕시코|브라질|캐나다|호주|하와이|모로코|리비아|이스라엘|가자|네팔|파키스탄|美|日|中|英|佛|獨)")

# 시설명: 고유 명칭 + 시설 접미. 접미만 있거나 일반명사면 버린다
_FACILITY_SUFFIX = (
    "지하차도", "지하도", "터널", "대교", "교량", "육교", "고가도로", "고가차도", "고속도로", "IC", "나들목", "분기점", "JC", "역", "선",
    "공장", "제련소", "발전소", "물류센터", "물류창고", "창고", "아파트", "주차장", "지하주차장", "저수지", "댐", "제방", "하천", "천", "강", "항",
    "산단", "산업단지", "시장", "백화점", "빌딩", "타워", "센터", "병원", "요양병원", "학교", "초등학교", "공사장", "광산", "탄광",
    "해수욕장", "유원지", "교", "배수펌프장", "펌프장", "정수장", "변전소", "선착장", "방파제", "부두", "갱도", "광업소", "제철소", "역사",
)
_FAC_RE = re.compile(r"([가-힣A-Za-z0-9]{2,14}(?:" + "|".join(sorted(_FACILITY_SUFFIX, key=len, reverse=True)) + r"))(?=[\s,·…'\"’”]|$|에서|에|의|서|로|을|를|이|가|은|는|와|과|도)")
_FAC_GENERIC = {
    "지하차도", "터널", "아파트", "공장", "주차장", "지하주차장", "고속도로", "현장", "공사장", "하천", "학교", "병원", "센터", "시장", "창고", "물류창고",
    "공사현장", "건설현장", "사고현장", "재난현장", "화재현장", "작업현장", "산업현장", "노동현장", "교육현장", "의료현장", "학교현장", "생산현장", "위험현장",
    "관계자", "도로", "철도", "선로", "노선", "본선", "전선", "최전선", "일선", "우선", "노선", "경선", "대선", "총선", "당선", "개선", "곡선", "직선",
    "대응선", "방어선", "경계선", "생명선", "안전선", "중앙선", "차선", "점선", "실선", "수선", "회선", "배선", "광선", "시선", "선선", "이선",
    "국가산단", "지방산단", "산업단지", "학교들", "병원들", "강풍", "한강", "교", "보", "방안", "시설", "이번역", "전역", "지역", "구역", "영역", "역",
    "피해지역", "재난지역", "특별재난지역", "침수지역", "해당지역", "인근지역", "전지역", "일부지역", "상황센터", "재난안전상황센터", "콜센터", "안전센터",
    "소방안전센터", "119안전센터", "행정복지센터", "주민센터", "지원센터", "피해지원센터", "통합지원센터", "재난심리지원센터", "구급센터", "응급의료센터",
    "모든 현장", "전 현장", "건설사 현장", "현장들", "각 현장", "외국인 근로자", "근로자", "중대재해", "대형병원", "상급병원", "종합병원", "대학병원", "보건소",
}
_FAC_PREFIX_BLOCK = re.compile(r"^(이번|해당|인근|관내|전국|모든|각|일부|주변|해당|당시|사고|재난|피해|침수|붕괴|화재|폭발|추락|공공|민간|대형|소형|노후|기존|신규|국내|도내|시내|군내|구내|신축|준공|건설|공사|수해|취약|위험|안전|노유자|다중|전통|재래)")
_FAC_BLOCK_RE = re.compile(r"(에선|에서|구역|지역|전역|영역|경보|주의보|안전센터|소방|119|지원센터|상황센터|대책본부|본부|현장|어선|화물선|여객선|유조선|운반선|예인선|낚싯배|선박)$|안전센터|119")
# 단어로도 흔히 쓰이는 기초 지자체 어간 — '…군'·'…시' 접미가 붙을 때만 지역으로 본다
_AMBIGUOUS_STEMS = frozenset({"강화", "장수", "영광", "진도", "예산", "고령", "보은", "남해", "동해", "서천", "청송", "고성", "광양", "성주", "영동", "부안", "화순", "곡성", "정선", "양구", "괴산", "단양", "의령", "함안", "울주", "증평", "기장", "달성", "옥천", "화천", "인제", "양양", "횡성", "홍천", "청양", "태안", "보령", "계룡", "과천", "구리", "오산", "광명", "동두천", "안성", "여주", "이천", "평창", "영월", "태백", "삼척", "속초", "문경", "상주", "영주", "영천", "경산", "김천", "밀양", "사천", "통영", "거제", "양산", "진주", "남원", "김제", "정읍", "나주", "광주"} - {"광주"})

_PARTICLE_TAIL = re.compile(r"(에서는|에서|으로|에는|에게|까지|부터|에|의|은|는|이|가|을|를|과|와|도|로)$")
_CASUALTY_DEAD = (
    re.compile(r"(\d{1,4})\s*명\s*(?:이|이\s*모두|가)?\s*(?:사망|숨지|숨져|숨진|목숨|희생|숨졌)"),
    re.compile(r"(?:사망자|사망|숨진\s*사람|희생자)\s*(?:는|가|도)?\s*(?:모두|총)?\s*(\d{1,4})\s*명"),
    re.compile(r"(\d{1,4})\s*명\s*(?:사망|숨짐)"),
)
_CASUALTY_INJURED = (
    re.compile(r"(\d{1,4})\s*명\s*(?:이|이\s*모두|가)?\s*(?:부상|다쳐|다친|다쳤|중상|경상|중경상)"),
    re.compile(r"(?:부상자|부상|중상자|경상자)\s*(?:는|가|도)?\s*(?:모두|총)?\s*(\d{1,4})\s*명"),
)
_CASUALTY_COMBINED = re.compile(r"(\d{1,4})\s*명\s*(?:이)?\s*(?:사망|숨지)[^\d]{0,12}(\d{1,4})\s*명\s*(?:이)?\s*(?:부상|다쳐|다친|중경상|중상)")

_WIDE_SET = frozenset(WIDE)
_CITY_SET = frozenset(_CITIES)
_COUNTY_SET = frozenset(_COUNTIES)


# 예방·대비 기사 (사고 뒤 대응이 아니라 계절 대비·홍보) — 원천 기사 순위에서 뒤로
_PREVENTIVE_RE = re.compile(r"(대비|예방|주의보|당부|훈련|캠페인|컨설팅|홍보|교육|위험예보|성수기|여름철|장마철|겨울철|해빙기|피서철|명절|연휴|대책회의|점검\s*나서|선제|사전\s*점검|안전\s*점검|안전관리\s*강화|특별\s*점검|집중\s*점검)")
# 실제로 일어난 사고를 가리키는 제목 어휘
_OCCURRED_RE = re.compile(r"(참사|붕괴|침수|화재|불|폭발|추락|매몰|전복|침몰|탈선|산사태|누출|유출|싱크홀|땅꺼짐|유실|범람|붕락|끼임|질식|감전|사망|숨져|숨진|숨지|사상|부상|실종|희생|피해|중대재해|산재|추돌|충돌)")


def is_preventive(title: str) -> bool:
    return _PREVENTIVE_RE.search(title or "") is not None


def looks_occurred(title: str) -> bool:
    return _OCCURRED_RE.search(title or "") is not None


def hazard_terms(text: str) -> list[str]:
    out: list[str] = []
    for m in _HAZARD_RE.finditer(text or ""):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def has_hazard(text: str) -> bool:
    return _HAZARD_RE.search(text or "") is not None


def has_disaster_vocab(text: str) -> bool:
    return _DISASTER_RE.search(text or "") is not None


def is_foreign(title: str) -> bool:
    return _FOREIGN_RE.search(title or "") is not None


_ORG_TAIL = re.compile(r"(군수|시장|구청장|도지사|지사|시청|군청|구청|도청|해경|해양경찰서|소방서|소방본부|경찰서|교육청|시의회|군의회|구의회|도의회)$")


def _tokens(text: str) -> list[str]:
    toks = []
    for t in re.findall(r"[가-힣A-Za-z0-9]{2,}", text or ""):
        toks.append(t)
        stripped = _PARTICLE_TAIL.sub("", t)
        if stripped != t and len(stripped) >= 2:
            toks.append(stripped)
        m = _ORG_TAIL.search(stripped)
        if m and len(stripped) - len(m.group(1)) >= 2:
            stem = stripped[: m.start()]
            toks.append(stem)  # '평택해경' → '평택'
            suffix = {"군수": "군", "시장": "시", "구청장": "구", "도지사": "도", "지사": "도"}.get(m.group(1))
            if suffix and not stem.endswith(suffix):
                toks.append(stem + suffix)  # '보은군수' → '보은군'
    return toks


def find_regions(text: str) -> list[str]:
    """본문에 나오는 표면형 그대로의 지역명 (기초 지자체 먼저, 광역은 뒤). '청주'·'청주시'·'강남구'·'충북'."""
    basic: list[str] = []
    wide: list[str] = []
    for t in _tokens(text):
        if t in ADMIN_NAMES:
            if t in _WIDE_SET or (t.endswith(("시", "도")) and t[:-1] in _WIDE_SET) or t.endswith(("특별시", "광역시", "특별자치시", "특별자치도")):
                if t not in wide:
                    wide.append(t)
            elif t.endswith("구") and len(t) <= 2:
                continue  # '중구'·'남구' 는 광역 없이 모호
            elif t not in basic:
                basic.append(t)
        elif (t in _CITY_SET or t in _COUNTY_SET) and t not in _WIDE_SET and t not in basic and t not in {"강화", "장수", "영광", "진도", "예산", "고령", "보은", "남해", "동해", "서천", "청송"}:
            basic.append(t)
    return basic + wide


def find_facility(text: str) -> str:
    """제목에서 고유 시설명 하나 ('궁평2지하차도', '서리천', '오송역'). 없으면 ''."""
    for m in _FAC_RE.finditer(text or ""):
        name = m.group(1)
        if name in _FAC_GENERIC or _FAC_PREFIX_BLOCK.match(name) or _FAC_BLOCK_RE.search(name):
            continue
        if name.endswith("시장") and (name[:-2] in _WIDE_SET or name[:-2] in _CITY_SET or name[:-1] in ADMIN_NAMES):
            continue  # '대전시장'·'청주시장' 은 시설이 아니라 단체장
        if any(name.endswith(g) and len(name) - len(g) < 2 for g in ("센터", "선", "역", "교", "천", "강", "항", "댐")):
            continue
        if name in ADMIN_NAMES or name.rstrip("시군구도") in ADMIN_NAMES:
            continue
        return name
    return ""


def _max_num(patterns, text: str) -> int | None:
    best = None
    for pat in patterns:
        for m in pat.finditer(text or ""):
            n = int(m.group(1))
            if 0 < n < 5000:
                best = n if best is None else max(best, n)
    return best


def casualties(text: str) -> tuple[int | None, int | None]:
    """제목·하이라이트에서 사망·부상자 수 (정규식, 없으면 None — 추정하지 않는다)."""
    dead = _max_num(_CASUALTY_DEAD, text)
    injured = _max_num(_CASUALTY_INJURED, text)
    for m in _CASUALTY_COMBINED.finditer(text or ""):
        d, i = int(m.group(1)), int(m.group(2))
        dead = d if dead is None else max(dead, d)
        injured = i if injured is None else max(injured, i)
    return dead, injured
