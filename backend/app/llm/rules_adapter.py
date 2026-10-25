"""LLM_MODE=rules — LLM 키 없이 규칙으로 두 역할(ⓐ 약속 추출, ⓑ 후속 신호 분류)을 대신하는 어댑터 (v2 §5).

LLMClient 프로토콜(extract_promise, classify_followup)을 그대로 구현하므로 파이프라인 코드는 모드를 모른다.
산출물의 model 은 "rules-v2"(FOLLOWUP_* 보강을 켜면 "rules-v3", 처음 버전은 rules-v1) — live·stub 결과와 섞이지 않고, 캐시 키(model)가 달라 live 로 바꾸면 다시 분류된다.

ⓐ 약속 추출
  - 행동 동사(하겠다·할 계획·추진·마련·설치·보강·점검·전수조사·교체·확충·신설·강화·복구·정비·예정 …)가 있으면 is_promise.
  - action = 행동 동사를 포함한 절(최대 40자, 인용문의 부분 문자열).
  - 기한: 'N월까지·연내·올해 안·상반기·하반기·내년·N일 내·YYYY년 M월' 을 정규식으로 찾고 발언일 기준으로 날짜를 정한다.
  - strength: 기한 + 단정형 → 확약 / 구체 행동만 → 계획 / 검토·노력·공감 → 의사표명 / 그 외 → 해당없음.
  - confidence 는 규칙별 고정값(확약 0.8 / 계획 0.7 / 의사표명 0.6 / 해당없음 0.5).
ⓑ 후속 신호 (rules-v2: 문장 가중치)
  - 제목·하이라이트·인용문을 문장으로 나눈다(제목도 한 문장 — '…철거 완료' 같은 제목 근거를 놓치지 않게).
  - 문장 관련도 = (추적 약속 action 과의 임베딩 코사인 최댓값) + (사건 가중치 단어 점수, rules/weights.sentence_weight).
    관련도 상위 FOLLOWUP_TOP_SENTENCES(기본 3)개 중 FOLLOWUP_SENTENCE_MIN 이상인 문장 안에서만 표현을 본다.
  - 그 문장이 약속 행동 키워드와 겹치거나(이전 규칙), 겹치지 않아도 사건 고유어 점수가 KW_ALONE(0.8) 이상이면 신호로 본다.
  - 새로운문제(재발·또다시·지연·차질·백지화 …) / 완료(client._PAST_DONE_RE) / 진행(착공·공사 중·예산 확보·설계·발주 …).
  - 완료 발췌가 미래 서술어·부분 완료면 completion_adjustment 로 진행으로 낮춘다 (validate_span 도 다시 검사한다).
  - confidence: 겹침 2개 이상 0.75, 1개 0.65, 0개(고유어 경로) 0.6. 사건 가중치가 있으면 + 0.2·(고유어 점수-0.5) + 0.1·(코사인-0.5).
    고르지 못하면 무관(표현 없음) 또는 불명확(표현은 있으나 약속·사건과 무관한 문장).
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

from ..config import Settings
from ..rules.orgs import canonical_org
from ..rules.terms import action_terms
from ..rules.weights import sentence_weight
from .client import _PAST_DONE_RE, MAX_SPAN_CHARS, completion_adjustment
from .schemas import FollowupInput, FollowupSignal, PromiseExtraction, PromiseInput, PromiseRef

RULES_MODEL = "rules-v2"  # v2: 문장 가중치 (2026-10-24). 모델명이 바뀌면 후속 신호 캐시가 무효가 되어 다시 분류된다
RULES_MODEL_V3 = "rules-v3"  # v3: 표현 고침 등 FOLLOWUP_* 보강을 켠 규칙 (2026-10-25, docs/EXPERIMENT.md §10) — 켠 상태로 분류한 신호는 v2 캐시와 섞이지 않는다

# ---------------------------------------------------------------- ⓐ 약속 추출
# 구체 행동 동사 (명사형 어간). '…하겠다' 앞에 오면 계획 이상
_CONCRETE = (
    "전수조사", "재발 방지", "재발방지", "마련", "설치", "보강", "점검", "교체", "확충", "신설", "강화", "복구", "정비", "증설", "개선",
    "철거", "도입", "구축", "배치", "확대", "보수", "정밀안전진단", "안전진단", "진단", "준설", "이전", "재건축", "개통", "준공", "완공",
    "착공", "시행", "추진", "늘리", "높이", "줄이", "없애", "막겠", "갖추", "지원", "보상", "배상", "제정", "개정", "폐쇄", "통제", "감독", "단속", "수립",
    "조성", "선포", "동원", "투입", "측정", "모니터링", "규명", "지급", "가동", "보완", "확보", "감식", "수사", "착수", "재개", "공개", "신고센터",
)
_MODAL_RE = re.compile(r"(하겠다|하겠습니다|겠다|겠습니다|할 계획|할 방침|할 예정|예정이다|예정입니다|계획이다|계획입니다|방침이다|할 것이다|추진한다|추진할|추진하겠)")
_SOFT_RE = re.compile(r"(검토|노력|공감|고민|살펴보|살피|협의|논의|모색|최선을 다|만전을 기|노력하)")
# 지시·당위·가정('…해야 한다', '…했어야', '…하라', '…해 달라')은 기관이 스스로 하겠다는 약속이 아니다
_DIRECTIVE_RE = re.compile(r"(해야\s*(한다|합니다|할|하며|하고)?|했어야|하라|하십시오|해\s*달라|해\s*주길|돼야|되어야|바란다|당부|촉구)")
_ACTION_RE = re.compile("(" + "|".join(sorted({*_CONCRETE, "하겠다", "하겠습니다", "할 계획", "예정"}, key=len, reverse=True)) + ")")
_CLAUSE_SPLIT_RE = re.compile(r"[.!?。\n]|(?<=다)\s|,\s|·")
_ACTION_MAX = 40

_REL_DEADLINES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일\s*(까지|안에|내|중)?"), "ymd"),
    (re.compile(r"(\d{4})\s*년\s*(상반기|하반기)\s*(까지|안에|내|중)?"), "y-half"),
    (re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(말|까지|안에|내|중)?"), "ym"),
    (re.compile(r"(\d{4})\s*년\s*(까지|안에|내|중|말)"), "y"),
    (re.compile(r"내년\s*(\d{1,2})\s*월\s*(말|까지|안에|내|중)?"), "next-m"),
    (re.compile(r"내년\s*(상반기|하반기)\s*(까지|안에|내|중)?"), "next-half"),
    (re.compile(r"내년\s*(말|까지|안에|중|안)"), "next-y"),
    (re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일\s*(까지|안에|내)"), "md"),
    (re.compile(r"(\d{1,2})\s*월\s*(말|까지|안에|내|중)"), "m"),
    (re.compile(r"(올해\s*(상반기|하반기))\s*(까지|안에|내|중)?"), "this-half"),
    (re.compile(r"(?<!내년\s)(?<!\d년\s)(상반기|하반기)\s*(까지|안에|내|중)"), "half"),
    (re.compile(r"(연내|연말까지|올해\s*안|올해\s*말|올해\s*내|올 연말|금년\s*내|금년\s*중|올해\s*중)"), "this-y"),
    (re.compile(r"이달\s*(말|안|중|내)"), "this-m"),
    (re.compile(r"다음\s*달\s*(말|까지|안|중)?"), "next-month"),
    (re.compile(r"(\d{1,3})\s*일\s*(이내|내|안에)"), "n-days"),
    (re.compile(r"(\d{1,2})\s*개월\s*(이내|내|안에)"), "n-months"),
)


def _month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def parse_relative_deadline(text: str, published: date) -> tuple[str | None, date | None, str]:
    """기한 표현을 찾아 (원문, 날짜, 정밀도). 상대 표현은 발언일 기준, 월 단위는 말일, 반기는 6/30·12/31."""
    for pat, kind in _REL_DEADLINES:
        m = pat.search(text or "")
        if not m:
            continue
        raw = m.group(0).strip()
        y = published.year
        try:
            if kind == "ymd":
                return raw, date(int(m.group(1)), int(m.group(2)), min(int(m.group(3)), calendar.monthrange(int(m.group(1)), int(m.group(2)))[1])), "day"
            if kind == "ym":
                return raw, _month_end(int(m.group(1)), int(m.group(2))), "month"
            if kind == "y":
                return raw, date(int(m.group(1)), 12, 31), "year"
            if kind == "y-half":
                yy = int(m.group(1))
                return raw, date(yy, 6, 30) if m.group(2) == "상반기" else date(yy, 12, 31), "month"
            if kind == "next-m":
                return raw, _month_end(y + 1, int(m.group(1))), "month"
            if kind == "next-half":
                return raw, date(y + 1, 6, 30) if m.group(1) == "상반기" else date(y + 1, 12, 31), "month"
            if kind == "next-y":
                return raw, date(y + 1, 12, 31), "year"
            if kind == "md":
                mo, d = int(m.group(1)), int(m.group(2))
                yy = y if (mo, d) >= (published.month, published.day) else y + 1
                return raw, date(yy, mo, min(d, calendar.monthrange(yy, mo)[1])), "day"
            if kind == "m":
                mo = int(m.group(1))
                yy = y if mo >= published.month else y + 1
                return raw, _month_end(yy, mo), "month"
            if kind in ("this-half", "half"):
                half = "상반기" if "상반기" in raw else "하반기"
                cand = date(y, 6, 30) if half == "상반기" else date(y, 12, 31)
                if cand < published:
                    cand = date(y + 1, cand.month, cand.day)
                return raw, cand, "month"
            if kind == "this-y":
                return raw, date(y, 12, 31), "year"
            if kind == "this-m":
                return raw, _month_end(y, published.month), "month"
            if kind == "next-month":
                nm = published.month % 12 + 1
                return raw, _month_end(y + (1 if nm == 1 else 0), nm), "month"
            if kind == "n-days":
                return raw, published + timedelta(days=int(m.group(1))), "day"
            if kind == "n-months":
                k = int(m.group(1))
                yy, mo = divmod(published.month - 1 + k, 12)
                return raw, _month_end(y + yy, mo + 1), "month"
        except ValueError:
            return raw, None, "none"
    return None, None, "none"


def _action_clause(quotation: str) -> str | None:
    """행동 동사를 포함한 절 하나(최대 40자, 인용문의 부분 문자열). 구체 행동 동사가 있는 절을 먼저 고른다."""
    text = re.sub(r"\s+", " ", quotation or "").strip()
    clauses = [c.strip() for c in _CLAUSE_SPLIT_RE.split(text) if c and c.strip()]
    best = None
    for c in clauses:
        m = _ACTION_RE.search(c)
        if not m:
            continue
        concrete = any(v in c for v in _CONCRETE)
        score = (1 if (_MODAL_RE.search(c) or _FIRM_RE.search(c) or _WILL_RE.search(c)) else 0, 1 if concrete else 0)  # 다짐이 걸린 절을 먼저
        if best is None or score > best[0]:
            best = (score, c, m)
    if best is None:
        return None
    _, clause, m = best
    if len(clause) <= _ACTION_MAX:
        return clause
    # 다짐 표현(없으면 행동 동사) 어절 끝까지를 포함하는 40자 창
    commits = [x for x in (_MODAL_RE.search(clause), _FIRM_RE.search(clause), _WILL_RE.search(clause)) if x]
    if commits:
        m = max(commits, key=lambda x: x.end())
    end = clause.find(" ", m.end())
    end = len(clause) if end < 0 else end
    start = max(0, end - _ACTION_MAX)
    if start > 0 and clause[start - 1] != " ":
        nxt = clause.find(" ", start)
        start = nxt + 1 if 0 <= nxt < end - 4 else start  # 어절 중간에서 시작하지 않게
    return clause[start:end].strip()


def _strength(quotation: str, has_deadline: bool) -> str:
    q = quotation or ""
    concrete_hits = [v for v in _CONCRETE if v in q]
    modal = bool(_MODAL_RE.search(q))
    soft = bool(_SOFT_RE.search(q))
    # '설치를 검토하겠다' 처럼 구체 행동이 검토·노력에 걸리면 의사표명. 다른 절에 단정 행동이 따로 있으면 그 절을 따른다
    firm_concrete = any(re.search(re.escape(v) + r"\S{0,6}\s?(하겠|할 계획|할 예정|예정|계획|방침|한다|할 것|키로|하기로|하고\s|하며|한 뒤|하는 한편)", q) and not re.search(re.escape(v) + r"\S{0,12}\s?(검토|노력|고민|협의|논의|모색)", q) for v in concrete_hits)
    if _DIRECTIVE_RE.search(q) and not re.search(r"(하겠|할 계획|할 예정|키로|하기로)", q):
        return "의사표명"
    if soft and not firm_concrete:
        return "의사표명"
    if has_deadline and (modal or firm_concrete):
        return "확약"
    if concrete_hits and (modal or firm_concrete or not soft):
        return "계획"
    if modal:
        return "의사표명"
    return "해당없음"


_CONF = {"확약": 0.8, "계획": 0.7, "의사표명": 0.6, "해당없음": 0.5}

# 약속 = 스스로 하겠다는 **다짐이 있는 문장**. 사실 묘사('…이 설치된', '…것으로 추정된다', '복구 작업을 진행하고 있다')는
# 구체 행동어가 있어도 약속이 아니다 (rules-v2, 2026-10-25 — 서술 조각이 '계획' 으로 추적되던 오탐)
_SENT_RE = re.compile(r"(?<=[.!?。])\s+|\n+|(?<=다)\.?\s+")
_FIRM_RE = re.compile(
    "(" + "|".join(sorted(_CONCRETE, key=len, reverse=True)) + r")\S{0,6}\s?(하겠|할 계획|할 예정|할 방침|한다|할 것|키로|하기로)"
)
_WAS_RE = re.compile(r"(계획|예정|방침)이었|하려\s*했|하기로\s*했었")
_WILL_RE = re.compile(r"(할|갈|낼|울|킬) 것(이다|입니다|)(?=\s|$|[.!?])")  # 문장 끝 '…지원할 것'·'…이어갈 것' ('할 것으로 보인다' 는 추정이라 제외)


def _commitment_sentences(quotation: str) -> list[str]:
    """다짐 표현(하겠다·할 계획·할 예정·방침·키로·하기로·「설치한다」형)이 있는 문장만. 과거형 계획('계획이었다')은 뺀다."""
    out = []
    for raw in _SENT_RE.split(quotation or ""):
        t = re.sub(r"\s+", " ", raw or "").strip()
        if len(t) >= 4 and (_MODAL_RE.search(t) or _FIRM_RE.search(t) or _WILL_RE.search(t)) and not _WAS_RE.search(t):
            out.append(t)
    return out

# ---------------------------------------------------------------- ⓑ 후속 신호
_PROGRESS_RE = re.compile(
    r"(착공|착수|추진\s*중|추진하고\s*있|진행\s*중|진행하고\s*있|공사\s*중|공사를\s*(시작|벌이|진행)|공사에\s*들어|예산\s*(을\s*)?확보|예산을\s*편성|국비\s*확보|"
    r"설계\s*(중|용역|를\s*마치|에\s*들어)|실시설계|발주|입찰|일부\s*완료|부분\s*개통|시범\s*운영|시행\s*중|본격\s*(추진|시행)|단계적으로|"
    r"(완료|준공|개통|완공|마무리|설치|교체|증설)\s*(할|될|을|될\s*것으로)?\s*(예정|계획|전망))"
)
_NEW_PROBLEM_RE = re.compile(
    # rules-v2 (10-25): 배경 서술('영업이 중단됐던')·조건('중단되면')·부정('중단되는 것은 아니다')·예방 촉구('되풀이 막아야')·
    # 논란('또다시 도마 위에')은 새로운 문제가 아니다 — 재발은 사고어 옆의 '또다시', 중단은 '다시 중단' 또는 조치(공사·복구…)의 중단만
    r"(또\s*다시\s*(\S+\s*){0,2}?(발생|침수|붕괴|사고|화재|침하|무너|유실|파손|균열|누수|정전|탈선|폭발|사망|추락|불이?\s*났)|재발했|재발해|재발됐|되풀이(됐|되었|되고\s*있)|"
    r"다시\s*(발생|침수|붕괴|사고|무너|불)|또\s*(사고|침수|붕괴|화재|폭발|사망|무너|물에\s*잠|유실|파손|균열|누수|정전|탈선|지반\s*침하|침하)|재(유실|침수|붕괴|파손)|추가\s*(사고|붕괴|피해)|"
    r"지연(되|돼|됐|으로|에)(?!\s*없)|늦어지|늦어져|차질(?!\s*(없|이\s*없|을\s*빚지\s*않))|백지화|무산|연기(됐|되)|"
    r"(다시|또|재차)\s*중단|(공사|사업|복구|운항|운행|수색|작업|보수|재개)(이|가|은|는)?\s*(전면\s*|잠정\s*)?중단(됐다|되었다|됐고|돼|된\s*상태)(?!던)|(공사|사업|복구|운항|수색|작업|보수)\s*중단(?=\s*(…|\.\.\.|,|·|$))|"
    r"제자리|하세월|지지부진|늑장\s*(대응|복구|공사|조치|대처))"
)
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?。])\s+|\n+")  # 제목의 '…' 는 같은 기사의 두 절이라 나누지 않는다
_NEUTRAL = re.compile(r"[\"“”‘’'<>《》「」]")


def _sentences(inp: FollowupInput) -> list[str]:
    out: list[str] = []
    for part in [inp.article_title, inp.hilight, *inp.quotations]:
        for s in _SENT_SPLIT_RE.split(part or ""):
            s = (s or "").strip()
            if len(s) >= 4 and s not in out:
                out.append(s)
    return out


# 겹침 판정에서 빼는 말: 재난 자체를 가리키는 말(사건 기사 어디에나 있다)·때·정도·일반 기관어·용언꼴
_OVERLAP_STOP = {
    "화재", "산불", "침수", "붕괴", "폭발", "사고", "참사", "태풍", "호우", "폭우", "지진", "산사태", "피해", "사망", "부상", "재난", "재해", "안전",
    "일단", "향후", "앞으로", "위해", "중심", "상황", "이내", "이상", "이하", "최대", "최소", "모든", "각종", "관련", "통해", "대해", "따라", "함께",
    "시청", "군청", "구청", "도청", "정부", "지자체", "관계기관", "기관", "당국", "합동", "협력", "협력체계", "체계", "인력", "투입", "현장", "주민", "국민",
    "영업", "중단", "출입", "통제", "대처", "초동", "신속", "정확", "실무", "실무중심", "훈련", "대비", "예방", "노력", "최선", "대응", "지원", "강화",
}
_TERM_VERBISH = re.compile(r"(고|어야|았어야|었어야|했어야|예정|하고|되고|됐고|해야|하며|되며|으며|면서|도록|는데|지만|였고|이고)$")


def _promise_terms(p: PromiseRef) -> list[str]:
    terms = action_terms(p.action, limit=8)
    # action_terms 는 '설치·교체' 같은 일반 행동어를 거른다 — 겹침 판정에는 약속의 대상 명사만 쓴다
    return [t for t in terms if len(t) >= 2 and t not in _OVERLAP_STOP and not _TERM_VERBISH.search(t)][:6]


def _window(sentence: str, m: re.Match[str]) -> str:
    """100자 넘는 문장은 매칭 위치를 포함하는 100자 창 (입력의 부분 문자열 유지)."""
    if len(sentence) <= MAX_SPAN_CHARS:
        return sentence
    start = max(0, min(m.start() - 40, len(sentence) - MAX_SPAN_CHARS))
    return sentence[start : start + MAX_SPAN_CHARS].strip()


KW_ALONE = 0.8  # 약속 키워드와 겹치지 않아도 이 이상 사건 고유어가 있는 문장은 신호 후보
_BASE_CONF = {2: 0.75, 1: 0.65, 0: 0.6}

# ---- 보강 후보 (설정으로 켠다, docs/EXPERIMENT.md §10)
# 같은 종류의 행동: 약속의 행동어와 문장의 행동어가 한 묶음에 있으면 '같은 조치' 로 본다
_ACTION_GROUPS: tuple[tuple[str, ...], ...] = (
    ("설치", "조성", "구축", "도입", "신설", "설비", "배치"),
    ("복구", "정비", "보수", "재건", "원상", "보강", "개축", "재시공", "수리"),
    ("교체", "개선", "개량"),
    ("철거", "해체"),
    ("지원", "지급", "보상", "배상", "보험금", "재난지원금", "위로금"),
    ("점검", "진단", "전수조사", "실태조사", "감사"),
    ("확충", "증설", "확대", "늘리", "증원"),
    ("이전", "이설", "폐쇄"),
    ("개통", "준공", "완공", "착공", "공사", "재개"),
    ("대책", "개정", "제정", "법안", "제도", "매뉴얼", "수립", "마련"),
    ("준설",),
    ("통제", "차단", "금지"),
    ("수사", "감식", "규명", "입건", "송치", "기소"),
    ("수색", "구조", "인양"),
    ("진화", "진압"),
)
_COMMIT_SENT_RE = re.compile(r"(하겠|할\s*계획|할\s*예정|예정이다|예정입니다|방침이다|키로|하기로|제안하겠|건의하겠|요청하겠|추진할)")
_DEMAND_SENT_RE = re.compile(r"(필요하|필요한|필요성|필요해|필요가|촉구|요구|해야|시급|바란다|당부|건의)")
_PREVENT_RE = re.compile(r"추가\s*(사고|붕괴|피해)\s*(를|가|의|도)?\s*(최소화|예방|방지|막|줄이|없도록|우려)")
# 꼬리 없는 '재개장·재개통' 옆에 앞날을 가리키는 말이 있으면 아직 열리지 않았다 ('청과동, 내년 2월 재개장')
_FUTURE_WORD_RE = re.compile(r"(내년|다음\s*달|오는\s|예정|목표|계획|앞두|앞둔)")


def action_groups(text: str) -> set[int]:
    """문장·약속에 나온 행동 묶음 번호."""
    return {i for i, g in enumerate(_ACTION_GROUPS) if any(w in (text or "") for w in g)}


# 멈췄던 것이 다시 열렸다 = 조치의 결과 ('영업 재개'·'운행을 재개했다'·'재개통'·'정상화됐다'). '재개될 예정' 은 completion_adjustment 가 진행으로 낮춘다
_RESUMED_RE = re.compile(
    r"((영업|운행|운영|통행|조업|가동|개장|진료|수업|입주)\s*(을|를|이|가)?\s*재개(했|됐|되었|된다|하고|돼|(?=\s*(…|\.\.\.|,|·|$)))|재개장(했|됐|한다)?|재개통(했|됐|한다)?|정상화(됐|했|되었|된다))"
)


def _expressions(s: str, fixes: bool = False) -> list[tuple[str, re.Match[str]]]:
    """fixes (FOLLOWUP_EXPRESSION_FIXES): '추가 피해 최소화·예방' 은 새로운 문제가 아니고, 앞날을 가리키는 말 옆의 꼬리 없는 '재개장·재개통' 은 완료가 아니다."""
    found: list[tuple[str, re.Match[str]]] = []
    for sig, pat in (("새로운문제", _NEW_PROBLEM_RE), ("완료", _PAST_DONE_RE), ("완료", _RESUMED_RE), ("진행", _PROGRESS_RE)):
        m = pat.search(s)
        if m:
            if fixes and sig == "새로운문제" and m.group(0).startswith("추가") and _PREVENT_RE.search(s):
                continue  # '추가 피해 최소화·예방' — 새 문제가 아니라 예방 다짐
            if fixes and pat is _RESUMED_RE and m.group(0) in ("재개장", "재개통") and _FUTURE_WORD_RE.search(s):
                continue  # '내년 2월 재개장' — 계획이지 다시 연 것이 아니다
            found.append((sig, m))
    return found


class RulesLLM:
    """LLMClient 프로토콜의 규칙 구현. 외부 호출이 없어 실패(LLMFailure)를 내지 않는다."""

    mode = "rules"

    @property
    def model(self) -> str:
        st = self.settings
        v3 = st is not None and (
            getattr(st, "followup_expression_fixes", False) or getattr(st, "followup_action_match", False)
            or getattr(st, "followup_skip_commitment", False) or not getattr(st, "followup_kw_alone", True)
        )
        return RULES_MODEL_V3 if v3 else RULES_MODEL

    VEC_CACHE_MAX = 50_000  # 문장 임베딩 캐시 상한 — 넘으면 비운다

    def __init__(self, settings: Settings | None = None, embedder=None):
        self.settings = settings
        self.embedder = embedder  # 문장 관련도용. 파이프라인이 자기 임베딩 백엔드를 붙인다 (없으면 hash)
        self.usage = {"calls": 0, "promise_calls": 0, "followup_calls": 0}
        self._vec_cache: dict[str, object] = {}

    def _vectors(self, texts: list[str]):
        import numpy as np

        if self.embedder is None:
            from ..pipeline.embeddings import HashEmbedding

            self.embedder = HashEmbedding()
        if len(self._vec_cache) > self.VEC_CACHE_MAX:
            self._vec_cache.clear()  # 비우기는 할 일(todo)을 고르기 전에 — 고른 뒤에 비우면 이미 있던 문장이 사라져 KeyError
        todo = [t for t in dict.fromkeys(texts) if t not in self._vec_cache]
        if todo:
            for t, v in zip(todo, self.embedder.encode(todo)):
                self._vec_cache[t] = np.asarray(v, dtype=np.float32)
        return [self._vec_cache[t] for t in texts]

    def sentence_relevance(self, sentences: list[str], inp: FollowupInput) -> list[tuple[float, float, int | None]]:
        """문장마다 (약속 action 과의 임베딩 코사인 최댓값, 사건 가중치 단어 점수, 가장 가까운 약속 id)."""
        actions = [p.action for p in inp.promises if p.action]
        out: list[tuple[float, float, int | None]] = []
        svecs = self._vectors(sentences) if actions else []
        avecs = self._vectors(actions) if actions else []
        ids = [p.id for p in inp.promises if p.action]
        for i, s in enumerate(sentences):
            emb, pid = 0.0, None
            for j, av in enumerate(avecs):
                c = float(svecs[i] @ av)
                if pid is None or c > emb:
                    emb, pid = c, ids[j]
            out.append((round(emb, 3), sentence_weight(inp.term_weights, s), pid))
        return out

    # ---------------------------------------------------------- ⓐ
    def extract_promise(self, inp: PromiseInput, *, key: str = "") -> PromiseExtraction:
        self.usage["calls"] += 1
        self.usage["promise_calls"] += 1
        q = re.sub(r"\s+", " ", inp.quotation or "").strip()
        try:
            published = date.fromisoformat(inp.published_at[:10])
        except ValueError:
            published = date.today()
        from ..rules.actors import normalize_actor_org

        actor = canonical_org(normalize_actor_org(inp.source, inp.responsible_org) or inp.source) or None
        committed = " ".join(_commitment_sentences(inp.quotation))
        if not committed:
            # 다짐 문장이 없다: 지시('…하라')·검토·노력은 의사표명, 사실 묘사는 해당없음 — 추적하지 않는다
            soft = bool(_DIRECTIVE_RE.search(q) or _SOFT_RE.search(q)) and bool(_ACTION_RE.search(q))
            return PromiseExtraction(
                is_promise=soft, actor_org=actor, actor_raw=inp.source, action=_action_clause(q) if soft else None,
                strength="의사표명" if soft else "해당없음", confidence=_CONF["의사표명" if soft else "해당없음"],
                rationale="다짐 문장 없음 — 지시·검토 표현 (규칙)" if soft else "다짐 문장 없음 — 사실 묘사 (규칙)",
            )
        action = _action_clause(committed) if _ACTION_RE.search(committed) else None
        if not action:
            return PromiseExtraction(
                is_promise=False, actor_org=actor, actor_raw=inp.source, action=None, strength="해당없음",
                confidence=_CONF["해당없음"], rationale="행동 동사 없음 (규칙)",
            )
        raw, deadline, precision = parse_relative_deadline(committed, published)
        if deadline is None:
            raw, deadline, precision = parse_relative_deadline(q, published)
        if deadline is not None and deadline < published:
            raw, deadline, precision = raw, None, "none"
        strength = _strength(committed, deadline is not None)
        return PromiseExtraction(
            is_promise=strength != "해당없음",
            actor_org=actor,
            actor_raw=inp.source,
            action=action,
            deadline_raw=raw,
            deadline_date=deadline.isoformat() if deadline else None,
            deadline_precision=precision if deadline else "none",
            strength=strength,
            confidence=_CONF[strength],
            rationale=f"규칙: 행동 동사 + {'기한(' + raw + ')' if raw else '기한 없음'} → {strength}",
        )

    # ---------------------------------------------------------- ⓑ
    def classify_followup(self, inp: FollowupInput, *, key: str = "") -> FollowupSignal:
        return self.classify_followup_detail(inp, key=key)[0]

    def classify_followup_detail(self, inp: FollowupInput, *, key: str = "") -> tuple[FollowupSignal, dict]:
        """classify_followup 과 같은 판정 + 고른 문장의 세부(겹침 수·경로·관련도) — 학습·진단용 (docs/EXPERIMENT.md §10)."""
        self.usage["calls"] += 1
        self.usage["followup_calls"] += 1
        st = self.settings
        top_n = st.followup_top_sentences if st else 3
        min_rel = st.followup_sentence_min if st else 0.0
        action_match = bool(getattr(st, "followup_action_match", False))
        kw_alone = bool(getattr(st, "followup_kw_alone", True))
        skip_commit = bool(getattr(st, "followup_skip_commitment", False))
        fixes = bool(getattr(st, "followup_expression_fixes", False))
        weighted = bool(inp.term_weights)
        identity = [t for t in (inp.identity_terms or []) if len(t) >= 2]
        terms_by_promise = {p.id: _promise_terms(p) for p in inp.promises}
        groups_by_promise = {p.id: action_groups(p.action) for p in inp.promises}
        sents = _sentences(inp)
        rel = self.sentence_relevance(sents, inp) if sents else []
        ranked = sorted(range(len(sents)), key=lambda i: -(rel[i][0] + rel[i][1]))
        selected = {i for i in ranked[: max(1, top_n)] if rel[i][0] + rel[i][1] >= min_rel}
        best: tuple | None = None  # (overlap, priority, relevance, signal, span, promise_id, conf)
        detail: dict = {}
        expression_without_overlap = False
        priority = {"새로운문제": 3, "완료": 2, "진행": 1}
        for i, s in enumerate(sents):
            found = _expressions(s, fixes)
            if not found:
                continue
            if i not in selected:
                expression_without_overlap = True
                continue
            if skip_commit and all(sig == "진행" for sig, _ in found) and (_COMMIT_SENT_RE.search(s) or _DEMAND_SENT_RE.search(s)):
                expression_without_overlap = True  # 다짐·요구 문장의 '추진·예정' 은 진행 보도가 아니다
                continue
            emb, kw, near = rel[i]
            s_groups = action_groups(s)
            pid, overlap, obj_hits, id_hits, verb = None, 0, 0, 0, False
            for p_id, terms in terms_by_promise.items():
                hits = [t for t in terms if t in s]
                ids = [t for t in hits if any(t in x or x in t for x in identity)]
                same_kind = bool(groups_by_promise[p_id] & s_groups)
                k = (len(hits) - len(ids)) + (len(ids) if same_kind else 0) if action_match else len(hits)
                if k > overlap:
                    pid, overlap, obj_hits, id_hits, verb = p_id, k, len(hits) - len(ids), len(ids), same_kind
            path = "overlap"
            if overlap == 0:
                near_kind = near is not None and bool(groups_by_promise.get(near, set()) & s_groups)
                if not kw_alone or not weighted or kw < KW_ALONE or near is None or (action_match and not near_kind):
                    expression_without_overlap = True
                    continue
                pid, path, verb = near, "unique-word", near_kind  # 약속 키워드는 없지만 사건 고유어가 무거운 문장 — 임베딩이 가장 가까운 약속
            # 한 문장에 여러 표현이 있으면: 새로운문제 > 완료 > 진행. 단 '완료' 가 미래 서술어면 진행으로 본다
            sig, m = max(found, key=lambda x: priority[x[0]])
            span = _window(s, m)
            if sig == "완료" and completion_adjustment(span):
                sig = "진행"
            conf = _BASE_CONF[min(overlap, 2)]
            if weighted:
                conf = min(0.9, max(0.3, conf + 0.2 * (kw - 0.5) + 0.1 * (emb - 0.5)))
            cand = (min(overlap, 2), priority[sig], round(emb + kw, 3), sig, span, pid, round(conf, 3))
            if best is None or cand[:3] > best[:3]:
                best = cand
                detail = {
                    "overlap": overlap, "object_hits": obj_hits, "identity_hits": id_hits, "same_kind": verb, "path": path, "emb": emb, "kw": kw,
                    "title_sentence": i == 0, "commit": bool(_COMMIT_SENT_RE.search(s)), "demand": bool(_DEMAND_SENT_RE.search(s)),
                    "rank": ranked.index(i), "n_sentences": len(sents),
                }
        if best is None:
            if expression_without_overlap:
                return FollowupSignal(signal="불명확", promise_id=None, matches_promise=False, evidence_span="", confidence=0.4, rationale="조치 표현은 있으나 약속·사건과 관련된 문장이 아님 (규칙)"), detail
            return FollowupSignal(signal="무관", promise_id=None, matches_promise=False, evidence_span="", confidence=0.6, rationale="조치 상태 표현 없음 (규칙)"), detail
        overlap, _, relevance, sig, span, pid, conf = best
        return FollowupSignal(
            signal=sig, promise_id=pid, matches_promise=True, evidence_span=span, confidence=conf,
            rationale=f"규칙: 관련 문장(관련도 {relevance:.2f}) · 약속 키워드 {overlap}개 겹침 + {sig} 표현",
        ), detail
