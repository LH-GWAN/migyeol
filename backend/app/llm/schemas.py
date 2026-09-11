"""LLM 입출력 스키마 (개발 프롬프트 8절). 출력은 client.messages.parse(output_format=...) 로 검증한다."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PromiseInput(BaseModel):
    event_summary: str
    responsible_org: str
    source: str
    quotation: str
    article_title: str
    published_at: str  # YYYY-MM-DD, 상대 기한 해석 기준일


class PromiseExtraction(BaseModel):
    is_promise: bool = Field(description="행동 약속·계획이 담긴 발언인가")
    actor_org: str | None = Field(default=None, description="기관 단위 정규화 주체 (예: 가온시). 개인명 금지")
    actor_raw: str | None = Field(default=None, description="source 원문")
    action: str | None = Field(default=None, description="'배수시설을 보강한다' 형태의 한 문장")
    deadline_raw: str | None = Field(default=None, description="발언 속 기한 표현 원문")
    deadline_date: str | None = Field(default=None, description="YYYY-MM-DD. 상대 표현은 published_at 기준. 월 단위면 말일")
    deadline_precision: Literal["day", "month", "year", "none"] = "none"
    strength: Literal["확약", "계획", "의사표명", "해당없음"] = "해당없음"
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(description="한 문장")


class PromiseRef(BaseModel):
    id: int
    actor_org: str
    action: str
    deadline_date: str | None = None


class FollowupInput(BaseModel):
    event_summary: str
    promises: list[PromiseRef]
    article_title: str
    hilight: str
    quotations: list[str] = Field(default_factory=list)
    published_at: str


class FollowupSignal(BaseModel):
    signal: Literal["완료", "진행", "새로운문제", "무관", "불명확"]
    promise_id: int | None = Field(default=None, description="어느 약속에 대한 신호인지. 특정 불가면 null")
    matches_promise: bool
    evidence_span: str = Field(default="", description="입력 텍스트에서 그대로 발췌, 100자 이하. 없으면 빈 문자열")
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
