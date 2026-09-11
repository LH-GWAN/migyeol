"""빅카인즈 클라이언트 프로토콜. 파이프라인은 이 인터페이스만 사용하며 mock/record/live 를 구분하지 않는다."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .schemas import (
    ChangeTrackerArgument,
    ChangeTrackerResult,
    IssueRankingResult,
    NewsSearchArgument,
    NewsSearchResult,
    QuotationSearchArgument,
    QuotationSearchResult,
    TimeLineArgument,
    TimeLineResult,
    WordCloudArgument,
    WordCloudResult,
)

# 뉴스 검색에서 기본으로 요청하는 fields (지침서 2.3 documents 참고). content 는 200자 제한 범위.
DEFAULT_NEWS_FIELDS: list[str] = [
    "title",
    "hilight",
    "published_at",
    "provider",
    "category",
    "category_incident",
    "byline",
    "provider_link_page",
    "tms_raw_stream",
    "provider_news_id",
]
DEFAULT_QUOTATION_FIELDS: list[str] = [
    "published_at",
    "provider",
    "category",
    "category_incident",
    "source",
    "quotation",
    "hilight",
]


class BigKindsError(RuntimeError):
    def __init__(self, endpoint: str, result: int | None, payload: object):
        super().__init__(f"bigkinds {endpoint} failed: result={result} payload={str(payload)[:300]}")
        self.endpoint = endpoint
        self.result = result
        self.payload = payload


@runtime_checkable
class BigKindsClient(Protocol):
    mode: str

    def search_news(self, arg: NewsSearchArgument) -> NewsSearchResult: ...

    def get_news_by_ids(self, news_ids: list[str], fields: list[str] | None = None) -> NewsSearchResult: ...

    def issue_ranking(self, date: str, provider: list[str] | None = None) -> IssueRankingResult: ...

    def word_cloud(self, arg: WordCloudArgument) -> WordCloudResult: ...

    def time_line(self, arg: TimeLineArgument) -> TimeLineResult: ...

    def search_quotation(self, arg: QuotationSearchArgument) -> QuotationSearchResult: ...

    def change_tracker(self, arg: ChangeTrackerArgument) -> ChangeTrackerResult: ...
