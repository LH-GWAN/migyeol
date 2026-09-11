"""실제 빅카인즈 OpenAPI 호출 클라이언트 (HTTPS POST, JSON)."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from .protocol import DEFAULT_NEWS_FIELDS, BigKindsError
from .schemas import (
    ApiEnvelope,
    ChangeTrackerArgument,
    ChangeTrackerResult,
    IssueRankingArgument,
    IssueRankingResult,
    NewsByIdsArgument,
    NewsSearchArgument,
    NewsSearchResult,
    QuotationSearchArgument,
    QuotationSearchResult,
    TimeLineArgument,
    TimeLineResult,
    WordCloudArgument,
    WordCloudResult,
)

log = logging.getLogger("migyeol.bigkinds")

EP_SEARCH_NEWS = "/search/news"
EP_ISSUE_RANKING = "/issue_ranking"
EP_WORD_CLOUD = "/word_cloud"
EP_TIME_LINE = "/time_line"
EP_SEARCH_QUOTATION = "/search/quotation"
EP_CHANGE_TRACKER = "/changeTracker/news"

_RETRY_STATUS = {429, 500, 502, 503, 504}


class LiveBigKindsClient:
    mode = "live"

    def __init__(
        self,
        access_key: str,
        base_url: str = "https://tools.kinds.or.kr",
        rps: float = 2.0,
        timeout: float = 30.0,
        max_retries: int = 3,
        http: httpx.Client | None = None,
    ):
        if not access_key:
            raise ValueError("BIGKINDS_ACCESS_KEY 가 비어 있습니다 (.env 확인)")
        self.access_key = access_key
        self.base_url = base_url.rstrip("/")
        self.min_interval = 1.0 / rps if rps > 0 else 0.0
        self.max_retries = max_retries
        self._http = http or httpx.Client(timeout=timeout, headers={"Content-Type": "application/json; charset=utf-8"})
        self._last_call = 0.0
        self.call_log: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ core
    def _throttle(self) -> None:
        if self.min_interval <= 0:
            return
        wait = self.min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)

    def _call(self, endpoint: str, argument: dict[str, Any]) -> dict[str, Any]:
        """POST {access_key, argument} -> return_object. 429/5xx/타임아웃은 지수 백오프로 재시도."""
        body = {"access_key": self.access_key, "argument": argument}
        url = f"{self.base_url}{endpoint}"
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            started = time.monotonic()
            try:
                resp = self._http.post(url, content=json.dumps(body, ensure_ascii=False).encode("utf-8"))
                self._last_call = time.monotonic()
                elapsed_ms = int((self._last_call - started) * 1000)
                if resp.status_code in _RETRY_STATUS:
                    raise httpx.HTTPStatusError(f"status {resp.status_code}", request=resp.request, response=resp)
                resp.raise_for_status()
                payload = resp.json()
                env = ApiEnvelope.model_validate(payload)
                self._log_call(endpoint, argument, env, elapsed_ms)
                if env.result != 0:
                    raise BigKindsError(endpoint, env.result, payload)
                return env.return_object
            except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.TransportError) as exc:
                last_exc = exc
                backoff = 0.5 * (2**attempt)
                log.warning("bigkinds %s attempt %d failed (%s); retry in %.1fs", endpoint, attempt + 1, exc, backoff)
                time.sleep(backoff)
        raise BigKindsError(endpoint, None, repr(last_exc))

    def _log_call(self, endpoint: str, argument: dict[str, Any], env: ApiEnvelope, elapsed_ms: int) -> None:
        ro = env.return_object or {}
        summary = {
            "endpoint": endpoint,
            "query": argument.get("query") or argument.get("date") or argument.get("news_ids", "")[:3]
            if isinstance(argument.get("news_ids"), list)
            else argument.get("query") or argument.get("date") or argument.get("from"),
            "total_hits": ro.get("total_hits", ro.get("total_count")),
            "n": len(ro.get("documents") or ro.get("topics") or ro.get("nodes") or ro.get("time_line") or ro.get("dataList") or []),
            "elapsed_ms": elapsed_ms,
            "keys": sorted(ro.keys()),
        }
        self.call_log.append(summary)
        log.info("bigkinds %s", summary)

    # ------------------------------------------------------------- endpoints
    def search_news(self, arg: NewsSearchArgument) -> NewsSearchResult:
        if not arg.fields:
            arg = arg.model_copy(update={"fields": list(DEFAULT_NEWS_FIELDS)})
        return NewsSearchResult.model_validate(self._call(EP_SEARCH_NEWS, arg.argument()))

    def get_news_by_ids(self, news_ids: list[str], fields: list[str] | None = None) -> NewsSearchResult:
        arg = NewsByIdsArgument(news_ids=news_ids, fields=fields or list(DEFAULT_NEWS_FIELDS))
        return NewsSearchResult.model_validate(self._call(EP_SEARCH_NEWS, arg.argument()))

    def issue_ranking(self, date: str, provider: list[str] | None = None) -> IssueRankingResult:
        arg = IssueRankingArgument(date=date, provider=provider or [])
        return IssueRankingResult.model_validate(self._call(EP_ISSUE_RANKING, arg.argument()))

    def word_cloud(self, arg: WordCloudArgument) -> WordCloudResult:
        return WordCloudResult.model_validate(self._call(EP_WORD_CLOUD, arg.argument()))

    def time_line(self, arg: TimeLineArgument) -> TimeLineResult:
        return TimeLineResult.model_validate(self._call(EP_TIME_LINE, arg.argument()))

    def search_quotation(self, arg: QuotationSearchArgument) -> QuotationSearchResult:
        return QuotationSearchResult.model_validate(self._call(EP_SEARCH_QUOTATION, arg.argument()))

    def change_tracker(self, arg: ChangeTrackerArgument) -> ChangeTrackerResult:
        return ChangeTrackerResult.model_validate(self._call(EP_CHANGE_TRACKER, arg.argument()))
