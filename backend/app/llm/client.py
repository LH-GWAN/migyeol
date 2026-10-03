"""LLM 어댑터. 역할은 두 가지뿐이다: ⓐ 인용문 -> 약속 JSON, ⓑ 후속 기사 -> 완료/진행/새문제 신호.

LLM_MODE=stub : fixtures/llm/stub_answers.json 재생 (키 불필요, 예선·테스트용)
LLM_MODE=live : Anthropic Messages API, client.messages.parse(output_format=<pydantic>) 로 구조화 출력
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from ..config import Settings
from .schemas import FollowupInput, FollowupSignal, PromiseExtraction, PromiseInput

log = logging.getLogger("migyeol.llm")

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
PROMPT_VERSIONS = {"promise": "promise_v1", "followup": "followup_v1"}

T = TypeVar("T", bound=BaseModel)


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


class LLMClient(Protocol):
    mode: str
    model: str

    def extract_promise(self, inp: PromiseInput, *, key: str = "") -> PromiseExtraction: ...

    def classify_followup(self, inp: FollowupInput, *, key: str = "") -> FollowupSignal: ...


class StubLLM:
    """픽스처 정답을 돌려주는 대역. key 는 fixture 생성기가 정한 식별자 (promise: 'news_id||source||quotation', followup: 'event_key||news_id')."""

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
            return PromiseExtraction(
                is_promise=False, actor_raw=inp.source, strength="해당없음", confidence=0.0, rationale="stub: 정답 없음"
            )
        return PromiseExtraction.model_validate(raw)

    def classify_followup(self, inp: FollowupInput, *, key: str = "") -> FollowupSignal:
        raw = self.answers.get("signals", {}).get(key)
        if raw is None:
            self.misses.append(f"signal:{key}")
            return FollowupSignal(signal="불명확", matches_promise=False, confidence=0.0, rationale="stub: 정답 없음")
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
        self.usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "calls": 0}

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
                    raise RuntimeError(f"LLM refusal: {getattr(resp, 'stop_details', None)}")
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
                raise RuntimeError(f"LLM API error: {exc}") from exc
        raise RuntimeError(f"LLM structured output failed after retry: {last_exc}")

    def extract_promise(self, inp: PromiseInput, *, key: str = "") -> PromiseExtraction:
        try:
            return self._parse(self._system_promise, inp, PromiseExtraction, effort="medium")
        except RuntimeError as exc:
            log.error("extract_promise failed (%s): %s", key, exc)
            return PromiseExtraction(is_promise=False, actor_raw=inp.source, strength="해당없음", confidence=0.0, rationale=f"오류: {exc}"[:200])

    def classify_followup(self, inp: FollowupInput, *, key: str = "") -> FollowupSignal:
        try:
            sig = self._parse(self._system_followup, inp, FollowupSignal, effort="low")
        except RuntimeError as exc:
            log.error("classify_followup failed (%s): %s", key, exc)
            return FollowupSignal(signal="불명확", matches_promise=False, confidence=0.0, rationale=f"오류: {exc}"[:200])
        return sig


def build_llm(settings: Settings) -> LLMClient:
    if settings.llm_mode == "stub":
        return StubLLM(settings.fixtures_dir / "llm" / "stub_answers.json")
    return AnthropicLLM(settings)


def validate_span(signal: FollowupSignal, inp: FollowupInput) -> FollowupSignal:
    """evidence_span 은 반드시 입력의 부분 문자열이어야 한다. 아니면 불명확 처리."""
    if not signal.evidence_span:
        return signal
    hay = " ".join([inp.article_title, inp.hilight, *inp.quotations]).replace("\n", " ")
    span = signal.evidence_span.strip()
    if span in hay or span.replace(" ", "") in hay.replace(" ", ""):
        return signal
    return signal.model_copy(
        update={
            "signal": "불명확",
            "matches_promise": False,
            "confidence": min(signal.confidence, 0.3),
            "rationale": f"근거 문장이 입력에서 확인되지 않아 불명확 처리 (원 판정: {signal.signal})",
        }
    )
