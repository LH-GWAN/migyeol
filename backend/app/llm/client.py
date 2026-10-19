"""LLM 어댑터. 역할은 두 가지뿐이다: ⓐ 인용문 -> 약속 JSON, ⓑ 후속 기사 -> 완료/진행/새문제 신호.

LLM_MODE=stub : fixtures/llm/stub_answers.json 재생 (키 불필요, 예선·테스트용)
LLM_MODE=live : Anthropic Messages API, client.messages.parse(output_format=<pydantic>) 로 구조화 출력

실패(한도 초과·서버 오류·파싱 실패·스텁 미스)는 결과를 지어내지 않고 LLMFailure 를 올린다.
호출자는 그 항목을 저장하지 않고 다음 실행에서 다시 시도한다. 키·권한·모델 ID 오류는 모든 호출이 똑같이 실패하므로 그대로 전파한다.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from ..config import Settings
from .schemas import FollowupInput, FollowupSignal, PromiseExtraction, PromiseInput

log = logging.getLogger("migyeol.llm")

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
PROMPT_VERSIONS = {"promise": "promise_v1", "followup": "followup_v1"}
MAX_SPAN_CHARS = 100
# 완료 -> 진행 강등 규칙 (3차 검증 1-1): '예정·계획·목표' 라는 단어가 문장에 있다는 것만으로는 미래가 아니다.
#  - 미래는 **문장 끝 서술어** 기준: '…할 예정(이다)', '…ㄹ 계획', '…ㄹ 것', '앞두고 있다', '추진 중이다' 로 끝날 때만.
#    보고 동사 꼬리('…이라고 밝혔다')는 떼고 본다.
#  - 부분 완료는 '일부·절반' 단독, 또는 '1단계·우선·먼저 … 나머지·2단계' 짝 구조일 때만.
#  - '모두·전부·100%·완전히' 가 있으면 완료.
_REPORT_TAIL_RE = re.compile(r"(이?라고|고|며|다고)\s*(밝혔|말했|전했|설명했|덧붙였|강조했|했)(다|습니다)?\.?$")
_FUTURE_TAIL_RE = re.compile(
    r"((할|될|ㄹ|을)\s*(예정|계획|방침|것|전망)(이다|입니다|이라|이며|임)?|(예정|계획|방침|전망|목표)(이다|입니다|이라|이며|임)?"
    r"|앞두고\s*있다|앞뒀다|앞두었다|(추진|진행|공사)\s*중(이다|입니다)?|마무리할\s*(계획|예정)?|완료할\s*(계획|예정)?|완료하겠다|마치겠다)\.?$"
)
_PAST_DONE_RE = re.compile(r"(마쳤|마무리했|마무리됐|완료했|완료됐|완료되었|완공했|완공됐|준공했|준공됐|개통했|개통됐|끝냈|끝났|설치했|설치됐|교체했|교체됐|복구했|복구됐)")
_FULL_RE = re.compile(r"(모두|전부|100%|100 %|완전히|전\s*구간|전량|일괄|전면)")
_PARTIAL_SINGLE_RE = re.compile(r"(일부|절반|부분적으로|부분만)")
_PARTIAL_FIRST_RE = re.compile(r"(1단계|1차|우선|먼저|일단|\d+단계)")
_PARTIAL_REST_RE = re.compile(r"(나머지|2단계|2차|남은|이어|추가로|다음\s*단계|후속|\d+단계는)")


def completion_adjustment(span: str) -> str | None:
    """'완료' 발췌를 '진행' 으로 낮춰야 하면 사유를, 아니면 None 을 돌려준다."""
    text = (span or "").strip()
    if _FULL_RE.search(text):
        return None
    core = _REPORT_TAIL_RE.sub("", text).strip()
    if _FUTURE_TAIL_RE.search(core):
        return "발췌가 예정·목표(미래 서술어) 표현이라 완료가 아닌 진행으로 조정"
    if _PARTIAL_SINGLE_RE.search(core) or (_PARTIAL_FIRST_RE.search(core) and _PARTIAL_REST_RE.search(core)):
        return "발췌가 부분 완료·남은 공정 표현이라 진행으로 조정"
    return None


T = TypeVar("T", bound=BaseModel)


class LLMFailure(RuntimeError):
    """이 항목만 실패 — 저장하지 않고 다음 실행에서 재시도한다."""


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


class LLMClient(Protocol):
    mode: str
    model: str

    def extract_promise(self, inp: PromiseInput, *, key: str = "") -> PromiseExtraction: ...

    def classify_followup(self, inp: FollowupInput, *, key: str = "") -> FollowupSignal: ...


class StubLLM:
    """픽스처 정답을 돌려주는 대역. key 는 fixture 생성기가 정한 식별자 (promise: 'news_id||source||quotation', followup: 'event_key||news_id').
    정답이 없으면 LLMFailure — 실패 행을 캐시하지 않는다."""

    mode = "stub"
    model = "stub"

    def __init__(self, answers_file: Path):
        self.answers: dict[str, dict[str, Any]] = {"promises": {}, "signals": {}}
        if answers_file.exists():
            with answers_file.open(encoding="utf-8") as f:
                self.answers = json.load(f)
        self.misses: list[str] = []

    def extract_promise(self, inp: PromiseInput, *, key: str = "") -> PromiseExtraction:
        raw = self.answers.get("promises", {}).get(key)
        if raw is None:
            self.misses.append(f"promise:{key}")
            raise LLMFailure(f"stub 정답 없음: promise {key}")
        return PromiseExtraction.model_validate(raw)

    def classify_followup(self, inp: FollowupInput, *, key: str = "") -> FollowupSignal:
        raw = self.answers.get("signals", {}).get(key)
        if raw is None:
            self.misses.append(f"signal:{key}")
            raise LLMFailure(f"stub 정답 없음: signal {key}")
        data = dict(raw)
        # 픽스처는 promise_index(약속 순번)로 저장한다 -> 입력의 실제 promise id 로 치환
        idx = data.pop("promise_index", None)
        promise_id = None
        if idx is not None and 0 <= int(idx) < len(inp.promises):
            promise_id = inp.promises[int(idx)].id
        data["promise_id"] = promise_id
        if promise_id is None:
            data["matches_promise"] = False
        return FollowupSignal.model_validate(data)


class AnthropicLLM:
    mode = "live"

    def __init__(self, settings: Settings):
        import anthropic

        if not settings.anthropic_api_key:
            # SDK 는 ANTHROPIC_API_KEY 환경변수 / ant auth 프로필도 읽으므로 빈 값이면 기본 해석에 맡긴다
            self.client = anthropic.Anthropic()
        else:
            self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.model = settings.llm_model
        self._system_promise = load_prompt(PROMPT_VERSIONS["promise"])
        self._system_followup = load_prompt(PROMPT_VERSIONS["followup"])
        self.usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "calls": 0, "failures": 0}

    def _parse(self, system_text: str, payload: BaseModel, output_model: type[T], effort: str) -> T:
        import anthropic

        # 시스템 프롬프트는 고정 문자열이므로 cache_control 로 프롬프트 캐시를 건다.
        # max_tokens 는 adaptive thinking 토큰까지 포함하므로 넉넉히 둔다 (부족하면 JSON 이 잘려 parse 실패).
        request: dict[str, Any] = dict(
            model=self.model,
            max_tokens=16000,
            system=[{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": payload.model_dump_json()}],
            output_format=output_model,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
        )
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                resp = self.client.messages.parse(**request)
                usage = getattr(resp, "usage", None)
                if usage is not None:
                    self.usage["calls"] += 1
                    self.usage["input_tokens"] += getattr(usage, "input_tokens", 0) or 0
                    self.usage["output_tokens"] += getattr(usage, "output_tokens", 0) or 0
                    self.usage["cache_read_input_tokens"] += getattr(usage, "cache_read_input_tokens", 0) or 0
                if getattr(resp, "stop_reason", None) == "refusal":
                    raise LLMFailure(f"LLM refusal: {getattr(resp, 'stop_details', None)}")
                parsed = resp.parsed_output
                if parsed is None:
                    raise ValidationError.from_exception_data("parsed_output is None", [])
                return parsed
            except (ValidationError, ValueError) as exc:
                last_exc = exc
                log.warning("LLM parse attempt %d failed: %s", attempt + 1, exc)
            except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.NotFoundError):
                # 키·권한·모델 ID 오류는 모든 호출이 똑같이 실패하므로 파이프라인을 멈춰 바로 드러낸다
                raise
            except anthropic.APIError as exc:
                # 한도 초과·서버·연결 오류(SDK 재시도 후에도 실패)는 이 항목만 실패 처리하고 다음 항목으로 넘어간다
                self.usage["failures"] += 1
                raise LLMFailure(f"LLM API error: {type(exc).__name__}") from exc
        self.usage["failures"] += 1
        raise LLMFailure(f"LLM structured output failed after retry: {type(last_exc).__name__}")

    def extract_promise(self, inp: PromiseInput, *, key: str = "") -> PromiseExtraction:
        try:
            return self._parse(self._system_promise, inp, PromiseExtraction, effort="medium")
        except LLMFailure as exc:
            log.error("extract_promise failed (%s): %s", key, exc)
            raise

    def classify_followup(self, inp: FollowupInput, *, key: str = "") -> FollowupSignal:
        try:
            return self._parse(self._system_followup, inp, FollowupSignal, effort="low")
        except LLMFailure as exc:
            log.error("classify_followup failed (%s): %s", key, exc)
            raise


def build_llm(settings: Settings) -> LLMClient:
    if settings.llm_mode == "stub":
        return StubLLM(settings.fixtures_dir / "llm" / "stub_answers.json")
    return AnthropicLLM(settings)


def _demote(signal: FollowupSignal, why: str) -> FollowupSignal:
    return signal.model_copy(
        update={
            "signal": "불명확",
            "matches_promise": False,
            "confidence": min(signal.confidence, 0.3),
            "rationale": f"{why} (원 판정: {signal.signal})",
        }
    )


def validate_span(signal: FollowupSignal, inp: FollowupInput) -> FollowupSignal:
    """후속 신호 코드 가드.

    - 완료·진행·새로운문제는 발췌 문장(≤100자)이 입력의 부분 문자열이어야 한다. 없거나 길거나 입력에 없으면 불명확.
    - 발췌가 '예정·목표·계획' 같은 미래 표현이면 완료가 아니라 진행으로 낮춘다 (프롬프트만 믿지 않는다).
    - 무관·불명확은 빈 발췌를 허용한다.
    """
    span = (signal.evidence_span or "").strip()
    if signal.signal in ("무관", "불명확"):
        if not span:
            return signal
    else:
        if not span:
            return _demote(signal, "근거 발췌 문장이 없어 불명확 처리")
        if len(span) > MAX_SPAN_CHARS:
            return _demote(signal, f"발췌가 {MAX_SPAN_CHARS}자를 넘어 불명확 처리")
    hay = " ".join([inp.article_title, inp.hilight, *inp.quotations]).replace("\n", " ")
    if not (span in hay or span.replace(" ", "") in hay.replace(" ", "")):
        return _demote(signal, "근거 문장이 입력에서 확인되지 않아 불명확 처리")
    if signal.signal == "완료":
        why = completion_adjustment(span)
        if why:
            return signal.model_copy(update={"signal": "진행", "rationale": f"{why} — {signal.rationale}"})
    return signal
