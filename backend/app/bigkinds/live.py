"""실제 빅카인즈 OpenAPI 호출 클라이언트 (HTTPS POST, JSON).

- 재시도: 타임아웃·전송 오류·429/5xx 만, 지수 백오프(429 는 Retry-After 우선). 그 밖의 4xx 는 즉시 BigKindsError.
- 비JSON 응답, envelope 검증 실패, result != 0 도 모두 BigKindsError 로 통일한다 (파이프라인이 사건 단위로 잡는다).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

import httpx
from pydantic import ValidationError

from ..redact import register_secret
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
MAX_RETRY_WAIT = 60.0


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
        require_key: bool = True,
    ):
        if require_key and not access_key:
            raise ValueError("BIGKINDS_ACCESS_KEY 가 비어 있습니다 (.env 확인)")
        self.access_key = access_key
        register_secret(access_key)  # 오류 메시지·기록에 키가 남지 않게
        self.base_url = base_url.rstrip("/")
        self.min_interval = 1.0 / rps if rps > 0 else 0.0
        self.max_retries = max_retries
        self._http = http or httpx.Client(timeout=timeout, headers={"Content-Type": "application/json; charset=utf-8"})
        self._last_call = 0.0
        self._throttle_lock = threading.Lock()  # 실험 수집(트랙 A)이 스레드 몇 개로 동시에 부를 때도 시작 간격(rps)을 지킨다
        self.call_log: list[dict[str, Any]] = []

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------ core
    def _throttle(self) -> None:
        if self.min_interval <= 0:
            return
        with self._throttle_lock:
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()  # 시작 시각을 예약한다 (응답 뒤에도 갱신된다)

    def _call(self, endpoint: str, argument: dict[str, Any]) -> dict[str, Any]:
        """POST {access_key, argument} -> return_object."""
        if not self.access_key:
            raise BigKindsError(endpoint, None, "access_key 없음 (replay_only 녹화 미스)")
        body = {"access_key": self.access_key, "argument": argument}
        url = f"{self.base_url}{endpoint}"
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            started = time.monotonic()
            try:
                resp = self._http.post(url, content=json.dumps(body, ensure_ascii=False).encode("utf-8"))
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                self._last_call = time.monotonic()
                self._backoff(endpoint, attempt, exc, None)
                continue
            self._last_call = time.monotonic()
            elapsed_ms = int((self._last_call - started) * 1000)
            if resp.status_code in _RETRY_STATUS:
                last_exc = httpx.HTTPStatusError(f"status {resp.status_code}", request=resp.request, response=resp)
                self._backoff(endpoint, attempt, last_exc, resp)
                continue
            if resp.status_code >= 400:
                raise BigKindsError(endpoint, resp.status_code, resp.text[:300])
            try:
                payload = resp.json()
            except ValueError as exc:
                raise BigKindsError(endpoint, resp.status_code, f"non-JSON response: {resp.text[:300]}") from exc
            try:
                env = ApiEnvelope.model_validate(payload)
            except ValidationError as exc:
                raise BigKindsError(endpoint, None, f"envelope 검증 실패: {exc.errors()[:2]} payload={str(payload)[:200]}") from exc
            self._log_call(endpoint, argument, env, elapsed_ms)
            if env.result != 0:
                raise BigKindsError(endpoint, env.result, payload)
            return env.return_object
        raise BigKindsError(endpoint, None, f"재시도 {self.max_retries + 1}회 실패: {last_exc!r}")

    def _backoff(self, endpoint: str, attempt: int, exc: Exception, resp: httpx.Response | None) -> None:
        if attempt >= self.max_retries:
            return  # 마지막 시도 뒤에는 기다리지 않는다
        wait = 0.5 * (2**attempt)
        if resp is not None and resp.status_code == 429:
            ra = resp.headers.get("Retry-After")
            if ra and ra.isdigit():
                wait = max(wait, min(float(ra), MAX_RETRY_WAIT))  # 아주 큰 Retry-After 로 Lock 을 쥔 채 오래 자지 않는다
        log.warning("bigkinds %s attempt %d failed (%s); retry in %.1fs", endpoint, attempt + 1, exc, wait)
        time.sleep(wait)

    def _log_call(self, endpoint: str, argument: dict[str, Any], env: ApiEnvelope, elapsed_ms: int) -> None:
        ro = env.return_object or {}
        if isinstance(argument.get("news_ids"), list):
            q: Any = f"{len(argument['news_ids'])} ids"
        else:
            q = argument.get("query") or argument.get("date") or argument.get("from")
        summary = {
            "endpoint": endpoint,
            "query": q,
            "total_hits": ro.get("total_hits", ro.get("total_count")),
            "n": len(ro.get("documents") or ro.get("topics") or ro.get("nodes") or ro.get("time_line") or ro.get("dataList") or []),
            "elapsed_ms": elapsed_ms,
            "keys": sorted(ro.keys()),
        }
        docs = ro.get("documents")
        if isinstance(docs, list) and docs and isinstance(docs[0], dict):
            summary["document_keys"] = sorted(docs[0].keys())  # 첫 실호출에서 실제 반환 필드 확인용
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
