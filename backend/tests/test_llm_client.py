"""live LLM 어댑터의 오류 처리. 실제 키·네트워크 없이 messages.parse 를 대역으로 바꿔 검증한다."""

import anthropic
import httpx
import pytest

from app.llm.client import AnthropicLLM
from app.llm.schemas import PromiseInput

REQ = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
INP = PromiseInput(
    event_summary="가온시 달빛지하차도 침수",
    responsible_org="가온시",
    source="가온시장",
    quotation="연말까지 배수펌프를 증설하겠다",
    article_title="가온시, 지하차도 배수펌프 증설",
    published_at="2025-07-20",
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
    llm.usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "calls": 0}
    return llm


def test_transient_api_error_fails_only_that_item():
    llm = make_llm(anthropic.APIConnectionError(request=REQ))
    out = llm.extract_promise(INP, key="k")
    assert out.is_promise is False and out.confidence == 0.0
    assert "LLM API error" in out.rationale


def test_rate_limit_fails_only_that_item():
    exc = anthropic.RateLimitError("rate limited", response=httpx.Response(429, request=REQ), body=None)
    out = make_llm(exc).extract_promise(INP, key="k")
    assert out.is_promise is False


def test_auth_error_stops_pipeline():
    exc = anthropic.AuthenticationError("bad key", response=httpx.Response(401, request=REQ), body=None)
    with pytest.raises(anthropic.AuthenticationError):
        make_llm(exc).extract_promise(INP, key="k")


def test_unknown_model_stops_pipeline():
    exc = anthropic.NotFoundError("no such model", response=httpx.Response(404, request=REQ), body=None)
    with pytest.raises(anthropic.NotFoundError):
        make_llm(exc).extract_promise(INP, key="k")
