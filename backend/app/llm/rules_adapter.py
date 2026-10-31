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
RULES_MODEL_V4 = "rules-v4"  # v4: 제목형 표현·약속 없는 사건의 직접 후속 경로 (2026-10-29, docs/EXPERIMENT.md §14)
RULES_MODEL_V5 = "rules-v5"  # v5: 구절 오독 고침·같은 행동 제목 경로·사건 단위 시기/출처 규칙 (2026-10-29, docs/EXPERIMENT.md §14.6)
RULES_MODEL_V6 = "rules-v6"  # v6: 완료 오독 가드·어절 경계 발췌·새로운문제 예방/배경 가드 (2026-10-30 코드 리뷰 A4·A5·B3) — 캐시 무효화용

# ---------------------------------------------------------------- ⓐ 약속 추출


def actor_from_source(source: str, responsible_org: str = "") -> str | None:
    """발언 주체 원문 → 기관명. 규칙 정규화 결과를 정규 키로 바꾸고, 그것이 없으면 기관 사전이 **아는** 기관일 때만 원문에서 뽑는다
    ('김주수 의성 군수' → 의성군, '김문수 고용부 장관' → 고용노동부). 모르면 None = 기관 미상 — '김 지사'·'김문수 장관' 같은 원문을
    기관(actor_org)으로 저장하지 않는다 (10-30 rules-2)."""
    from ..rules.actors import normalize_actor_org
    from ..rules.orgs import is_known_org

    normalized = normalize_actor_org(source, responsible_org)
    if normalized:
        return canonical_org(normalized) or normalized
    cand = canonical_org(source)
    return cand if cand and is_known_org(cand) else None


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
_FRAGMENT_HEAD_RE = re.compile(r"(도록|않도록|하고|되고|하며|되며|으며|면서|고자|어서|해서|니다|다는|라는|이며|이고|이므로|것이므로|으로|에서|에게|대로)$")

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
    out = clause[start:end].strip()
    # 앞 어절이 '않도록'·'하고'·'며' 같은 용언 꼬리면 떼어 낸다 — '않도록 민간자문단 …' 조각 방지 (10-30 B11)
    while out and len(out) > 20:
        head, _, rest = out.partition(" ")
        if rest and _FRAGMENT_HEAD_RE.search(head):
            out = rest
            continue
        break
    return out


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


_WORD_BREAK = " \t\n.…·,"  # 어절·구절 경계로 삼는 글자


def _window(sentence: str, m: re.Match[str]) -> str:
    """100자 넘는 문장은 매칭 위치를 포함하는 100자 이내 창 (입력의 부분 문자열 유지).

    창의 시작과 끝은 어절 경계에 맞춘다 — 글자 단위로 자르면 '무등록업체' 가 '등록업체' 로 뜻이 뒤집힌다 (10-30 press-5).
    잘렸다는 표시('…')는 저장하지 않고 화면·초안이 붙인다 (발췌는 입력의 부분 문자열이어야 validate_span 을 통과한다)."""
    if len(sentence) <= MAX_SPAN_CHARS:
        return sentence
    start = max(0, min(m.start() - 40, len(sentence) - MAX_SPAN_CHARS))
    if start > 0 and sentence[start - 1] not in _WORD_BREAK:
        prev = max(sentence.rfind(ch, 0, start) for ch in _WORD_BREAK)
        if prev >= 0 and m.end() - (prev + 1) <= MAX_SPAN_CHARS:
            start = prev + 1  # 어절 시작으로 앞당긴다
        else:
            nxt = min((i for i in (sentence.find(ch, start) for ch in _WORD_BREAK) if i >= 0), default=-1)
            if 0 <= nxt < m.start():
                start = nxt + 1  # 앞당길 수 없으면 다음 어절 시작으로
    end = min(len(sentence), start + MAX_SPAN_CHARS)
    if end < len(sentence) and sentence[end] not in _WORD_BREAK:
        last = max(sentence.rfind(ch, m.end(), end) for ch in _WORD_BREAK)
        if last > m.end():
            end = last
    return sentence[start:end].strip()


def span_truncated_prefix(span: str, *texts: str) -> str:
    """발췌가 어떤 원문 조각의 첫머리가 아니면(앞이 잘렸으면) 화면·초안에 붙일 '…' 를 돌려준다."""
    sp = (span or "").strip()
    if not sp:
        return ""
    for t in texts:
        for piece in _SENT_SPLIT_RE.split(t or ""):
            if (piece or "").strip().startswith(sp):
                return ""
    return "…"


KW_ALONE = 0.8  # 약속 키워드와 겹치지 않아도 이 이상 사건 고유어가 있는 문장은 신호 후보
SAME_ACTION_CONF = 0.62  # rules-v5 같은 행동 제목 경로의 신뢰도 (SIGNAL_MIN_CONF 0.6 바로 위)
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
_PREVENT_RE = re.compile(r"추가\s*(사고|붕괴|피해)\S{0,2}\s*(를|가|의|도|에)?\s*(최소화|예방|방지|막|줄이|없도록|우려|대비|조사|않도록|점검)")  # '추가 피해지 조사'·'추가 붕괴 우려'·'추가 붕괴에 대비'
# 10-30 rules-6: 재발 표현 뒤 '하지 않도록·막기 위해·대비' 는 예방 다짐이고, '중단됐던·중단돼 왔다가·중단된 뒤 (재개)' 는 재개의 배경 서술이다
# 재발 표현 바로 뒤(조사·'피해'·'사고' 한 어절까지)가 예방 서술어일 때만 — '또 사고 났는데 재발 방지책은 없었다' 는 재발이다 (10-30 R4)
_PROBLEM_PREVENT_TAIL_RE = re.compile(
    r"^(가|이|를|을|의|도)?\s*((피해|사고|붕괴|침수)?\s*(가|이|를|을)?\s*)?(하지\s*않도록|않도록|없도록|없게|막기\s*위해|막고자|방지를\s*위해|방지하기\s*위해|예방을\s*위해|예방하기\s*위해|에\s*대비해|에\s*대비하|우려를\s*(덜|씻))"
)
_PROBLEM_BACKGROUND_RE = re.compile(r"중단\s*(됐던|되었던|돼\s*왔다가|된\s*뒤|됐다가|되었다가|된\s*이후|됐던)")
_QUOTED_SPAN_RE = re.compile(r"[\"“‘'][^\"”’']{1,80}[\"”’']")
_RADIO_WORD_RE = re.compile(r"(무전|교신|송신|관제)")
# 꼬리 없는 '재개장·재개통' 옆에 앞날을 가리키는 말이 있으면 아직 열리지 않았다 ('청과동, 내년 2월 재개장')
_FUTURE_WORD_RE = re.compile(r"(내년|다음\s*달|오는\s|예정|목표|계획|앞두|앞둔)")

# ---- rules-v4 제목형 표현 (FOLLOWUP_HEADLINE_EXPRESSIONS) — 제목은 '복구 완료' 처럼 명사로 끝나 과거형 정규식(_PAST_DONE_RE)이 놓친다.
# 수색·진화·구조·감식(응급 대응 자체)의 '마무리' 는 넣지 않는다 — 맹검 라벨 기준(EXPERIMENT §12.1)에서 후속 조치가 아니다
_HEADLINE_DONE_RE = re.compile(
    r"((복구|설치|교체|정비|보강|철거|이전|준설|정밀\s*점검|안전\s*점검|안전\s*진단|정밀\s*진단|점검|진단|개선|보수|재건|재시공|공사|조사|지급|도입|구축|배치|복원)"
    r"\s*(작업|사업|공사)?\s*(이|을|가|를)?\s*(모두\s*|전부\s*|최종\s*)?(완료|마무리|끝)(?!\s*(예정|계획|목표|못|안\s|하지|되지|않|할|될|시점|기한|일|때|까지|후|뒤|되면|하면|이후|직후))"
    r"|(전체|전면|완전|모두|100\s*%)\s*(복구|정상화|재개|재가동|복원)(?!\s*(공사|사업|계획|작업|을|를|에|위해|위한|까지|때|방안|대책))"
    r"|재가동(했|됐|돼|(?=\s|$|…|,|\.))"
    r"|((작업\s*)?중지|통제|제한\s*급수|급수\s*제한|대피\s*명령|운행\s*중지|사용\s*중지|출입\s*금지|봉쇄|폐쇄|경보|특보)\s*(명령\s*)?(이|가|을|를)?\s*(\S+\s*){0,2}?(전면\s*)?해제)"
)
# 조사·수사의 결과 (원인 규명을 약속·지시한 사건의 후속) — '원인' 과 결론 표현이 한 문장에, 추정·조사 중은 제외
# 제목형 완료 바로 옆(앞 12자·뒤 8자)에 앞날을 가리키는 말이 있으면 진행 — '작업중지 연내 전면해제'. '장마 앞두고 복구 완료' 의 '앞두' 는 넣지 않는다
_HEADLINE_FUTURE_RE = re.compile(r"(내년|다음\s*달|오는\s|예정|목표|계획|연내|올해\s*안|이달\s*중|상반기|하반기|분기|올해\s*말|이달\s*말)")
# 제목의 '-ㄴ다·-ㄹ' 꼬리는 앞날의 완료다 ('8월까지 설치 끝낸다', '설치 완료한다') — 진행으로 본다
_HEADLINE_FUTURE_TAIL_RE = re.compile(r"^\s*(낸다|낼|난다|날\s|된다|될|한다|할)")
_HEADLINE_COND_RE = re.compile(r"(해야|아야|어야|려면|하도록|토록)")  # '공사를 완료하고 사용승인을 받아야' — 조건·요구이지 완료가 아니다
# ---- rules-v5 구절 오독 고침 (FOLLOWUP_PHRASE_FIXES)
_DEMAND_TAIL_RE = re.compile(r"^\S{0,3}?\s*(하라|해라|하십시오|해\s*달라|해\s*주길|해야|아야|어야|려면|하도록|토록)")  # '폐로 절차에 착수하라' — 요구
_ALSO_AT_RE = re.compile(r"^\s*(당시|때|현장|이후|직후|로\s|으로\s|와\s|과\s|에\s*대한)")  # '또 화재 당시' — '또한 화재 때' 이지 재발이 아니다
# ---- 10-30 rules-5 완료 오독 가드: 완료 표현 바로 뒤·앞의 요구·부정·조건·이유·대조·임박·발언 표현은 완료가 아니다
_DONE_TAIL_DEMAND_RE = re.compile(r"^(를|을|도|이|가)?\s*(당부|촉구|요구|요청|주문|지시|건의|호소|강조|독려)")  # '조속한 복구 완료를 당부했다' ('완료 후 개선 건의' 는 아니다)
_DONE_TAIL_NEG_RE = re.compile(r"^(나|내|되|하|이|을|를|가|지)?\s*(지\s*(않|못|말)|않|못\s*(했|하|한)|안\s*(됐|했|돼)|되지\s*(않|못))")  # '끝나지 않았다'·'끝내지 않았다'
_DONE_TAIL_COND_RE = re.compile(
    r"^(나|되|하|이|을|를|가|돼|되어|내|해)?\s*(면|려면|다면|거나|더라도|야\b|야\s|야$|도\s|도$|도,|라\s|라$)"  # '드러나면'·'끝내야'·'완료돼야'·'마무리돼도'·'끝나도'·'끝내라'
    r"|^(되는|하는|될|할|나는|내는)\s*(시점|때|날|대로|즉시|직후|순간|까지|\d{2,4}\s*년|내년|올해|이달|다음|오는|\d{1,2}\s*월)"  # '마무리되는 시점'·'마무리되는 2027년께' (앞날)
    r"|^(나기|내기|하기|되기)\s*(전|까지|전까지|직전)"  # '끝나기 전까지'
)
_DONE_TAIL_REASON_RE = re.compile(r"^(기\s*때문|으므로|기에\s|어서\s|해서\s|기\s*땜)")  # '교체했기 때문에 노후화되지 않았다' — 배경 설명
_DONE_TAIL_CONTRAST_RE = re.compile(r"^(으나|지만|으나,|지만,)\s*\S{0,12}?(효과|소용|변화|진전)\s*(가|이|는|도)?\s*(없|미미|적)")  # '설치했으나 효과가 없었다'
_DONE_TAIL_IMMINENT_RE = re.compile(r"^\s*(을|를)?\s*(앞두|앞둔|눈앞|코앞)")  # '복구 완료를 앞두고'
_DONE_HEAD_SPEECH_RE = re.compile(r"(발언|말|연설|인사말|인사|축사|답변|설명|브리핑|보고|기자회견|회견)(을|를)\s*$")  # '발언을 마쳤다'
_DONE_HEAD_NEG_RE = re.compile(r"(어려운|어렵다는|않은|못한|불가능한|힘든|없는|불가능하다는)\s*(것으로|것이|것|상태로)?\s*$")  # '규명이 어려운 것으로 확인됐다'


def completion_blocked(sentence: str, m: re.Match[str]) -> str | None:
    """완료 표현이 요구·부정·조건·이유·대조·발언 종료 문맥이면 사유를, 아니면 None 을 돌려준다 (10-30 rules-5)."""
    tail = sentence[m.end() : m.end() + 16]
    head = sentence[max(0, m.start() - 14) : m.start()]
    if _DONE_TAIL_DEMAND_RE.search(tail):
        return "요구·당부"
    if _DONE_TAIL_NEG_RE.search(tail):
        return "부정"
    if _DONE_TAIL_COND_RE.search(tail):
        return "조건"
    if _DONE_TAIL_REASON_RE.search(tail):
        return "이유·배경"
    if _DONE_TAIL_CONTRAST_RE.search(tail):
        return "효과 없음"
    if _DONE_HEAD_SPEECH_RE.search(head):
        return "발언 종료"
    if _DONE_HEAD_NEG_RE.search(head):
        return "부정"
    return None


def completion_imminent(sentence: str, m: re.Match[str]) -> bool:
    """완료 표현 바로 뒤가 '앞두고' 면 아직 끝나지 않았다 — 진행으로 본다."""
    return _DONE_TAIL_IMMINENT_RE.search(sentence[m.end() : m.end() + 8]) is not None
_UNDER_CONSTRUCTION_RE = re.compile(r"^\s*(이던|이었던|인\s|인데|에\s|에서)")  # '공사 중이던 냉동창고에서 불' — 장소 설명이지 진행이 아니다
INVESTIGATION_TAG = "수사·조사 표현"  # judge 가 원천 발언이 수사·조사가 아닌 사건이면 내린다 (FOLLOWUP_INVESTIGATION_SOURCE)
_INVESTIGATION_SPAN_RE = re.compile(r"(입건|송치|구속|수사|감식|합동\s*조사|압수수색|원인)")
_CAUSE_RESULT_RE = re.compile(r"(직접\s*원인|원인이었|원인으로\s*(결론|확인|밝혀|드러|판명)|결론(지었|났|냈|을\s*내)|밝혀(졌|져)|드러(났|나)|판명|확인(됐|되었)|과실(이|로|\s*때문))")
CAUSE_RESULT_TAG = "조사 결과 표현"  # judge 가 원천 발언 직후(같은 발표의 전재일 수 있음)면 내린다
_CAUSE_PENDING_RE = re.compile(r"(추정|조사\s*중|규명\s*(중|못|안)|조사할|밝힐|파악\s*중|오리무중|난항|못\s*(밝|찾))")
_HEADLINE_PROGRESS_RE = re.compile(
    r"((복구율|공정률|진척률|진행률)\s*\d+"
    r"|\d[\d,.]*\s*(억|조|만)\s*원?\s*(을|를|이|가)?\s*(\S+\s*)?(투입|편성|지원|확보|배정|지급|집행)"
    r"|(추가|긴급|예비비\s*로?)\s*(편성|투입|배정)"
    r"|(구호금|지원금|재난지원금|보상금|보험금|위로금|성금|복구비|생계비|구호비|격려금)\s*(을|를)?\s*(\S+\s*)?(선)?지급"
    r"|(24시간|집중|특별|합동)\s*(현장\s*)?순찰|현장\s*순찰"  # '집중 단속' 은 예방 캠페인이 많아 넣지 않는다
    r"|(임시\s*(숙소|주거|시장|주택|대피소|거처|상가|매장)|심리\s*상담소|상담소|쉼터|구호소)\s*(\S+\s*){0,2}?(운영|마련|설치|개장|개소)"
    r"|(\d+\s*월\s*(부터|중|말|초)?|올해\s*안|연내|이달\s*(부터|중|말)?|다음\s*달\s*(부터|중|말|초)?)\s*(\S+\s*)?(복구|공사|정비|착공|개통|재개장|재개통|개장|운영|시행|설치|해제|교체|철거)"
    r"|(입건|송치|구속\s*영장)(했|됐|돼|(?=\s|$|…|,|\.))"  # 수사 단계만 — 기소·재판·선고는 맹검 라벨 기준에서 후속 조치가 아니다
    r"|현재\s*진행형)"
)


def action_groups(text: str) -> set[int]:
    """문장·약속에 나온 행동 묶음 번호."""
    return {i for i, g in enumerate(_ACTION_GROUPS) if any(w in (text or "") for w in g)}


# 멈췄던 것이 다시 열렸다 = 조치의 결과 ('영업 재개'·'운행을 재개했다'·'재개통'·'정상화됐다'). '재개될 예정' 은 completion_adjustment 가 진행으로 낮춘다
_RESUMED_RE = re.compile(
    r"((영업|운행|운영|통행|조업|가동|개장|진료|수업|입주)\s*(을|를|이|가)?\s*재개(했|됐|되었|된다|하고|돼|(?=\s*(…|\.\.\.|,|·|$)))|재개장(했|됐|한다)?|재개통(했|됐|한다)?|정상화(됐|했|되었|된다))"
)


def _inside_quoted_radio(s: str, m: re.Match[str]) -> bool:
    """문장에 무전·교신 말이 있고 매칭이 따옴표 안에 들어 있으면 True ('…"작업 중단, 응답하라" 고 무전했다' 는 상태 표현이 아니다)."""
    if not _RADIO_WORD_RE.search(s):
        return False
    for q in _QUOTED_SPAN_RE.finditer(s):
        if q.start() <= m.start() and m.end() <= q.end():
            return True
    return False


def _expressions(s: str, fixes: bool = False, headline: bool = False, phrase: bool = False) -> list[tuple[str, re.Match[str]]]:
    """fixes (FOLLOWUP_EXPRESSION_FIXES): '추가 피해 최소화·예방' 은 새로운 문제가 아니고, 앞날을 가리키는 말 옆의 꼬리 없는 '재개장·재개통' 은 완료가 아니다.
    headline (FOLLOWUP_HEADLINE_EXPRESSIONS, rules-v4): 제목형 명사 표현(_HEADLINE_DONE_RE·_HEADLINE_PROGRESS_RE·조사 결과)도 본다.
    이때 앞날의 '재개장·재개통' 은 버리지 않고 진행(일정이 잡힌 복구)으로 본다."""
    found: list[tuple[str, re.Match[str]]] = []
    pats = [("새로운문제", _NEW_PROBLEM_RE), ("완료", _PAST_DONE_RE), ("완료", _RESUMED_RE), ("진행", _PROGRESS_RE)]
    if headline:
        pats += [("완료", _HEADLINE_DONE_RE), ("진행", _HEADLINE_PROGRESS_RE)]
    for sig, pat in pats:
        m = pat.search(s)
        if m:
            if fixes and sig == "새로운문제" and m.group(0).startswith("추가") and _PREVENT_RE.search(s):
                continue  # '추가 피해 최소화·예방' — 새 문제가 아니라 예방 다짐
            if fixes and pat is _RESUMED_RE and m.group(0) in ("재개장", "재개통") and _FUTURE_WORD_RE.search(s):
                if headline:
                    found.append(("진행", m))  # '내년 2월 재개장' — 일정이 잡힌 복구는 진행
                continue  # '내년 2월 재개장' — 계획이지 다시 연 것이 아니다
            if phrase:
                tail = s[m.end() : m.end() + 12]
                if sig in ("진행", "완료") and pat is not _PAST_DONE_RE and _DEMAND_TAIL_RE.search(tail):
                    continue  # '착수하라·마쳐야' — 요구·조건이지 상태 보도가 아니다
                if sig == "새로운문제" and m.group(0).startswith("또") and _ALSO_AT_RE.search(tail):
                    continue  # '또 화재 당시' — 재발이 아니다
                if sig == "새로운문제" and _PROBLEM_PREVENT_TAIL_RE.search(s[m.end() : m.end() + 14]):
                    continue  # '다시 발생하지 않도록'·'재발 방지를 위해' — 예방 다짐 (10-30 rules-6)
                if sig == "새로운문제" and "중단" in m.group(0) and _PROBLEM_BACKGROUND_RE.search(s[m.start() : m.end() + 8]):
                    continue  # '중단됐던 공사가 재개'·'중단돼 왔다가' — 배경 서술
                if sig == "새로운문제" and _inside_quoted_radio(s, m):
                    continue  # 사고 직전 무전·교신 인용 구절
                if pat is _PROGRESS_RE and m.group(0).replace(" ", "").startswith("공사중") and _UNDER_CONSTRUCTION_RE.search(tail):
                    continue  # '공사 중이던 창고' — 장소 설명
            if pat in (_HEADLINE_DONE_RE, _HEADLINE_PROGRESS_RE) and _HEADLINE_COND_RE.search(s[m.end() : m.end() + 25]):
                continue  # 조건·요구('…까지 공사를 완료하고 사용승인을 받아야') — 완료도 진행도 아니다
            if pat is _HEADLINE_DONE_RE and (_HEADLINE_FUTURE_RE.search(s[max(0, m.start() - 12) : m.end() + 8]) or _HEADLINE_FUTURE_TAIL_RE.search(s[m.end() : m.end() + 4])):
                found.append(("진행", m))  # '연내 전면해제' — 앞날의 완료는 진행
                continue
            if phrase and sig == "완료":
                if completion_blocked(s, m):
                    continue  # '복구 완료를 당부했다'·'끝나지 않았다'·'드러나면'·'교체했기 때문에'·'설치했으나 효과가 없었다' (10-30 rules-5)
                if completion_imminent(s, m):
                    found.append(("진행", m))  # '완료를 앞두고' — 아직 끝나지 않았다
                    continue
            found.append((sig, m))
    if headline and "원인" in s and not _CAUSE_PENDING_RE.search(s):
        m = _CAUSE_RESULT_RE.search(s)
        if m and not (phrase and completion_blocked(s, m)):
            found.append(("완료", m))  # 조사·수사 결과 — '작업대 선로 침범이 직접 원인', '담배꽁초가 원인이었다'
    return found


class RulesLLM:
    """LLMClient 프로토콜의 규칙 구현. 외부 호출이 없어 실패(LLMFailure)를 내지 않는다."""

    mode = "rules"

    @property
    def model(self) -> str:
        st = self.settings
        if st is not None and any(getattr(st, f, False) for f in ("followup_phrase_fixes", "followup_same_action", "followup_investigation_source",
                                                                    "followup_recurrence")) or (st is not None and getattr(st, "followup_early_problem_days", 0)):
            return RULES_MODEL_V6 if getattr(st, "followup_phrase_fixes", False) else RULES_MODEL_V5
        if st is not None and (getattr(st, "followup_headline_expressions", False) or getattr(st, "followup_event_path", False)):
            return RULES_MODEL_V4
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
        actor = actor_from_source(inp.source, inp.responsible_org)
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
        headline = bool(getattr(st, "followup_headline_expressions", False))
        phrase = bool(getattr(st, "followup_phrase_fixes", False))
        same_action = bool(getattr(st, "followup_same_action", False))
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
            found = _expressions(s, fixes, headline, phrase)
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
            same_action_hit = False
            if overlap == 0:
                near_kind = near is not None and bool(groups_by_promise.get(near, set()) & s_groups)
                if not kw_alone or not weighted or kw < KW_ALONE or near is None or (action_match and not near_kind):
                    # rules-v5 같은 행동 제목 경로 (FOLLOWUP_SAME_ACTION): 제목 문장의 완료·진행 표현이 가장 가까운 약속과 같은 종류의 행동이면
                    # ('복구를 완료하겠다' ↔ '장마 앞두고 복구 완료') 약속 낱말이 안 겹쳐도 받는다
                    hit = next((p_id for p_id, g in groups_by_promise.items() if g & s_groups), None) if same_action and i == 0 else None
                    if hit is None or not any(sig_ in ("완료", "진행") for sig_, _ in found):
                        expression_without_overlap = True
                        continue
                    pid, path, verb, same_action_hit = hit, "same-action", True, True
                else:
                    pid, path, verb = near, "unique-word", near_kind  # 약속 키워드는 없지만 사건 고유어가 무거운 문장 — 임베딩이 가장 가까운 약속
            # 한 문장에 여러 표현이 있으면: 새로운문제 > 완료 > 진행. 단 '완료' 가 미래 서술어면 진행으로 본다
            sig, m = max(found, key=lambda x: priority[x[0]])
            cause_result = m.re is _CAUSE_RESULT_RE
            investigation = bool(_INVESTIGATION_SPAN_RE.search(m.group(0))) or cause_result
            span = _window(s, m)
            if sig == "완료" and completion_adjustment(span):
                sig = "진행"
            conf = _BASE_CONF[min(overlap, 2)]
            if weighted:
                conf = min(0.9, max(0.3, conf + 0.2 * (kw - 0.5) + 0.1 * (emb - 0.5)))
            if same_action_hit:
                conf = SAME_ACTION_CONF  # 제목 + 같은 종류 행동 + 완료·진행 표현 — 관련도 점수 대신 고정값
            cand = (min(overlap, 2), priority[sig], round(emb + kw, 3), sig, span, pid, round(conf, 3))
            if best is None or cand[:3] > best[:3]:
                best = cand
                detail = {
                    "overlap": overlap, "object_hits": obj_hits, "identity_hits": id_hits, "same_kind": verb, "path": path, "emb": emb, "kw": kw,
                    "title_sentence": i == 0, "commit": bool(_COMMIT_SENT_RE.search(s)), "demand": bool(_DEMAND_SENT_RE.search(s)),
                    "rank": ranked.index(i), "n_sentences": len(sents), "cause_result": cause_result, "investigation": investigation,
                }
        if best is None:
            if expression_without_overlap:
                return FollowupSignal(signal="불명확", promise_id=None, matches_promise=False, evidence_span="", confidence=0.4, rationale="조치 표현은 있으나 약속·사건과 관련된 문장이 아님 (규칙)"), detail
            return FollowupSignal(signal="무관", promise_id=None, matches_promise=False, evidence_span="", confidence=0.6, rationale="조치 상태 표현 없음 (규칙)"), detail
        overlap, _, relevance, sig, span, pid, conf = best
        cause = bool(detail.get("cause_result"))
        invest = bool(detail.get("investigation"))
        tags = (f" · {CAUSE_RESULT_TAG}" if cause else "") + (f" · {INVESTIGATION_TAG}" if invest else "") + (" · 같은 행동 제목" if detail.get("path") == "same-action" else "")
        return FollowupSignal(
            signal=sig, promise_id=pid, matches_promise=True, evidence_span=span, confidence=conf,
            rationale=f"규칙: 관련 문장(관련도 {relevance:.2f}) · 약속 키워드 {overlap}개 겹침 + {sig} 표현" + tags,
        ), detail
