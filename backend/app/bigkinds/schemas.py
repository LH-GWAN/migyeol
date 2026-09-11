"""빅카인즈 OpenAPI 요청/응답 모델. 필드명은 지침서(newstore V1.0)와 동일하게 유지한다.

- 요청 본문: {"access_key": ..., "argument": {...}}
- 응답 본문: {"result": 0, "return_object": {...}}
- published_at.until 은 해당 일자를 제외한다 (from 은 포함).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# 요청
# ---------------------------------------------------------------------------


class PublishedAt(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: str = Field(alias="from")
    until: str

    def dump(self) -> dict[str, str]:
        return {"from": self.from_, "until": self.until}

    @classmethod
    def of(cls, start: date, until_exclusive: date) -> "PublishedAt":
        return cls(**{"from": start.isoformat(), "until": until_exclusive.isoformat()})


class _SearchBase(BaseModel):
    """뉴스 검색 / 인용문 검색 / 연관어 / 트렌드가 공유하는 조건."""

    query: str | dict[str, str] = ""
    published_at: PublishedAt | None = None
    provider: list[str] = Field(default_factory=list)
    category: list[str] = Field(default_factory=list)
    category_incident: list[str] = Field(default_factory=list)
    byline: str = ""
    provider_subject: list[str] = Field(default_factory=list)

    def argument(self) -> dict[str, Any]:
        arg: dict[str, Any] = {
            "query": self.query,
            "provider": self.provider,
            "category": self.category,
            "category_incident": self.category_incident,
            "byline": self.byline,
            "provider_subject": self.provider_subject,
        }
        if self.published_at is not None:
            arg["published_at"] = self.published_at.dump()
        return arg


class NewsSearchArgument(_SearchBase):
    """2. 뉴스 검색 API — /search/news"""

    sort: dict[str, str] | list[dict[str, str]] = Field(default_factory=lambda: {"date": "desc"})
    hilight: int = Field(default=200, le=200)
    return_from: int = Field(default=0, ge=0, le=20000)
    return_size: int = Field(default=100, ge=1, le=10000)
    fields: list[str] = Field(default_factory=list)

    def argument(self) -> dict[str, Any]:
        arg = super().argument()
        arg.update(
            sort=self.sort,
            hilight=self.hilight,
            return_from=self.return_from,
            return_size=self.return_size,
            fields=self.fields,
        )
        return arg


class NewsByIdsArgument(BaseModel):
    """3. 뉴스 상세 정보 조회 — /search/news (news_ids)"""

    news_ids: list[str]
    fields: list[str] = Field(default_factory=list)

    def argument(self) -> dict[str, Any]:
        return {"news_ids": self.news_ids, "fields": self.fields}


class IssueRankingArgument(BaseModel):
    """4. 오늘의 이슈 — /issue_ranking"""

    date: str
    provider: list[str] = Field(default_factory=list)

    def argument(self) -> dict[str, Any]:
        return {"date": self.date, "provider": self.provider}


class WordCloudArgument(_SearchBase):
    """5. 연관어 분석 — /word_cloud (query 필수)"""

    query: str


class TimeLineArgument(_SearchBase):
    """6. 키워드 트렌드 — /time_line"""

    query: str
    interval: Literal["day", "month", "year"] = "month"
    normalize: str = "false"

    def argument(self) -> dict[str, Any]:
        arg = super().argument()
        arg.update(interval=self.interval, normalize=self.normalize)
        return arg


class QuotationSearchArgument(_SearchBase):
    """8. 뉴스 인용문 검색 — /search/quotation"""

    sort: dict[str, str] | list[dict[str, str]] = Field(default_factory=lambda: {"date": "desc"})
    hilight: int = Field(default=300, le=300)
    return_from: int = Field(default=0, ge=0, le=1000)
    return_size: int = Field(default=100, ge=1, le=100)
    fields: list[str] = Field(default_factory=list)

    def argument(self) -> dict[str, Any]:
        arg = super().argument()
        arg.update(
            sort=self.sort,
            hilight=self.hilight,
            return_from=self.return_from,
            return_size=self.return_size,
            fields=self.fields,
        )
        return arg


class ChangeTrackerArgument(BaseModel):
    """13. 뉴스 변경이력 — /changeTracker/news"""

    model_config = ConfigDict(populate_by_name=True)
    from_: str = Field(alias="from")  # "yyyy-mm-dd hh:mm:ss"
    interval: Literal["MIN_15", "MIN_30", "MIN_45", "MIN_60", "DAY_1", "DAY_2", "DAY_3"] = "DAY_1"
    offset: str = "1"

    def argument(self) -> dict[str, Any]:
        return {"from": self.from_, "interval": self.interval, "offset": self.offset}


# ---------------------------------------------------------------------------
# 응답
# ---------------------------------------------------------------------------


def parse_published(value: str | None) -> date | None:
    """'2019-03-14T00:00:00.000+09:00' / '20190314' / '2019-03-14' -> date"""
    if not value:
        return None
    v = value.strip()
    if len(v) == 8 and v.isdigit():
        return date(int(v[:4]), int(v[4:6]), int(v[6:8]))
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).date()
    except ValueError:
        return date.fromisoformat(v[:10])


class NewsDocument(BaseModel):
    """2.3 출력 결과 documents[] — fields 에 지정한 값만 채워진다."""

    model_config = ConfigDict(extra="allow")

    news_id: str
    title: str = ""
    content: str | None = None  # 200자 제한
    hilight: str | None = None  # 최대 200자
    published_at: str | None = None
    enveloped_at: str | None = None
    dateline: str | None = None
    provider: str = ""
    category: list[str] = Field(default_factory=list)
    category_incident: list[str] = Field(default_factory=list)
    byline: str | None = None
    images: str | None = None
    images_caption: str | None = None
    provider_news_id: str | None = None
    publisher_code: str | None = None
    provider_link_page: str | None = None
    printing_page: str | None = None
    tms_raw_stream: str | None = None

    @property
    def published_date(self) -> date | None:
        return parse_published(self.published_at)

    @property
    def keywords(self) -> list[str]:
        if not self.tms_raw_stream:
            return []
        return [k.strip() for k in self.tms_raw_stream.split("\n") if k.strip()]

    @property
    def provider_code(self) -> str:
        return self.news_id.split(".", 1)[0]


class NewsSearchResult(BaseModel):
    total_hits: int = 0
    documents: list[NewsDocument] = Field(default_factory=list)


class IssueTopic(BaseModel):
    model_config = ConfigDict(extra="allow")
    topic: str
    topic_rank: int = 0
    topic_keyword: str = ""
    topic_content: str = ""
    issue_category: str = ""
    news_cluster: list[str] = Field(default_factory=list)

    @property
    def keywords(self) -> list[str]:
        return [k.strip() for k in self.topic_keyword.split(",") if k.strip()]


class IssueRankingResult(BaseModel):
    date: str = ""
    topics: list[IssueTopic] = Field(default_factory=list)


class WordCloudNode(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int = 0
    name: str
    level: int = 0
    weight: float = 0.0


class WordCloudResult(BaseModel):
    nodes: list[WordCloudNode] = Field(default_factory=list)


class TimeLinePoint(BaseModel):
    label: str  # day=yyyyMMdd, month=yyyyMM, year=yyyy
    hits: int = 0


class TimeLineResult(BaseModel):
    total_hits: int = 0
    time_line: list[TimeLinePoint] = Field(default_factory=list)


class QuotationDocument(BaseModel):
    """8.3 출력 결과 documents[] — source(발언 주체), quotation(발언 내용)이 핵심."""

    model_config = ConfigDict(extra="allow")

    news_id: str
    published_at: str | None = None
    date: str | None = None
    provider: str = ""
    category: list[str] = Field(default_factory=list)
    category_incident: list[str] = Field(default_factory=list)
    source: str = ""
    quotation: str = ""
    hilight: str | None = None
    title: str | None = None
    byline: str | None = None

    @property
    def published_date(self) -> date | None:
        return parse_published(self.published_at or self.date)


class QuotationSearchResult(BaseModel):
    total_hits: int = 0
    documents: list[QuotationDocument] = Field(default_factory=list)


class ChangeTrackerItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    media_id: str = ""
    newsitem_id: str
    news_status: Literal["Update", "Cancelled"] | str = "Update"
    insert_dt: str | None = None
    update_dt: str | None = None
    pub_date: str | None = None
    sync_dt: str | None = None


class ChangeTrackerResult(BaseModel):
    total_count: int = 0
    size: int = 3000
    message: str = "OK"
    items: list[ChangeTrackerItem] = Field(default_factory=list)

    @field_validator("total_count", "size", mode="before")
    @classmethod
    def _to_int(cls, v: Any) -> int:
        return int(v) if v not in (None, "") else 0

    @model_validator(mode="before")
    @classmethod
    def _flatten(cls, data: Any) -> Any:
        # 지침서 형식: {"dataList": [{"data": {...}}, ...]}
        if isinstance(data, dict) and "dataList" in data and "items" not in data:
            data = dict(data)
            data["items"] = [row.get("data", row) for row in data.pop("dataList") or []]
        return data


class ApiEnvelope(BaseModel):
    result: int
    return_object: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None
    message: str | None = None
