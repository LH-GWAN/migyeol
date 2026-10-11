"""LLM 어댑터의 오류 처리와 코드 가드. 실제 키·네트워크 없이 messages.parse 를 대역으로 바꿔 검증한다."""

import anthropic
import httpx
import pytest

from app.llm.client import AnthropicLLM, LLMFailure, StubLLM, validate_span
from app.llm.schemas import FollowupInput, FollowupSignal, PromiseInput, PromiseRef

REQ = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
INP = PromiseInput(
    event_summary="가온시 달빛지하차도 침수",
    responsible_org="가온시",
    source="가온시장",
    quotation="연말까지 배수펌프를 증설하겠다",
    article_title="가온시, 지하차도 배수펌프 증설",
    published_at="2025-07-20",
)
FINP = FollowupInput(
    event_summary="가온시 달빛지하차도 침수",
    promises=[PromiseRef(id=7, actor_org="가온시", action="배수펌프를 증설한다", deadline_date="2026-10-31")],
    article_title="달빛지하차도 배수펌프 증설 공사 착공",
    hilight="가온시는 3일 달빛지하차도 배수펌프 증설 공사에 착공했다. 연말 완료 예정이다.",
    quotations=[],
    published_at="2026-03-03",
)


class FakeMessages:
    def __init__(self, exc: Exception):
        self.exc = exc
        self.calls = 0

    def parse(self, **_):
        self.calls += 1
        raise self.exc


def make_llm(exc: Exception) -> AnthropicLLM:
    llm = AnthropicLLM.__new__(AnthropicLLM)  # __init__ 은 실제 클라이언트를 만들므로 건너뛴다
    llm.client = type("FakeClient", (), {"messages": FakeMessages(exc)})()
    llm.model = "test-model"
    llm._system_promise = ""
    llm._system_followup = ""
    llm.usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "calls": 0, "failures": 0}
    return llm


# ------------------------------------------------------------------ 실패는 결과를 지어내지 않는다 (1-4)
def test_transient_api_error_raises_llm_failure_not_fake_result():
    llm = make_llm(anthropic.APIConnectionError(request=REQ))
    with pytest.raises(LLMFailure):
        llm.extract_promise(INP, key="k")
    assert llm.usage["failures"] == 1


def test_rate_limit_raises_llm_failure():
    exc = anthropic.RateLimitError("rate limited", response=httpx.Response(429, request=REQ), body=None)
    with pytest.raises(LLMFailure):
        make_llm(exc).classify_followup(FINP, key="k")


def test_auth_error_stops_pipeline():
    exc = anthropic.AuthenticationError("bad key", response=httpx.Response(401, request=REQ), body=None)
    with pytest.raises(anthropic.AuthenticationError):
        make_llm(exc).extract_promise(INP, key="k")


def test_unknown_model_stops_pipeline():
    exc = anthropic.NotFoundError("no such model", response=httpx.Response(404, request=REQ), body=None)
    with pytest.raises(anthropic.NotFoundError):
        make_llm(exc).extract_promise(INP, key="k")


def test_stub_miss_raises_and_records(tmp_path):
    stub = StubLLM(tmp_path / "none.json")
    with pytest.raises(LLMFailure):
        stub.extract_promise(INP, key="nope")
    assert stub.misses == ["promise:nope"]


# ------------------------------------------------------------------ validate_span 가드 (ev-12, ej-9, ej-10)
def sig(signal, span, conf=0.9):
    return FollowupSignal(signal=signal, promise_id=7, matches_promise=True, evidence_span=span, confidence=conf, rationale="r")


def test_empty_span_demotes_positive_signal():
    out = validate_span(sig("완료", ""), FINP)
    assert out.signal == "불명확" and out.matches_promise is False and out.confidence <= 0.3


def test_empty_span_allowed_for_unrelated():
    out = validate_span(FollowupSignal(signal="무관", matches_promise=False, evidence_span="", confidence=0.9, rationale="r"), FINP)
    assert out.signal == "무관"


def test_span_not_in_input_demotes():
    out = validate_span(sig("완료", "배수펌프 증설을 모두 마쳤다"), FINP)
    assert out.signal == "불명확"


def test_span_with_different_spacing_passes():
    out = validate_span(sig("진행", "배수펌프증설공사에착공했다"), FINP)
    assert out.signal == "진행"


def test_overlong_span_demotes():
    out = validate_span(sig("진행", FINP.hilight * 3), FINP)
    assert out.signal == "불명확"


def test_completion_predicted_is_downgraded_to_progress():
    out = validate_span(sig("완료", "연말 완료 예정이다"), FINP)
    assert out.signal == "진행" and out.matches_promise is True
