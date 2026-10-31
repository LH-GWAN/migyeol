"""게시판 요청기 — 모든 요청은 guard.check_url → robots.txt → 요청 간격을 거친다.

- 리다이렉트는 자동으로 따라가지 않고 한 단계씩 다시 검사한다(허용 도메인 밖·뉴스 도메인으로 튕기는 것 방지, 최대 3회).
- robots.txt 는 호스트당 한 번 받고(RFC 9309 의미론, app/notices/robots.py) 같은 Fetcher 안에서 캐시한다.
- 요청 간격은 `NOTICE_REQUEST_INTERVAL_SEC`(기본 2초) 이상, User-Agent 는 `NOTICE_USER_AGENT`.
- 테스트는 네트워크 없이 FixtureFetcher(고정 HTML)를 쓴다.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable
from typing import Protocol
from urllib.parse import urljoin, urlsplit

import httpx

from .guard import BlockedURL, check_url
from .robots import RobotsRules, rules_for_status

log = logging.getLogger("migyeol.notices")

MAX_REDIRECTS = 3
MIN_INTERVAL_SEC = 2.0  # 명세 §3 요청 간격 하한
MAX_BYTES = 3_000_000  # 목록·상세 페이지 상한 (첨부파일을 받지 않는다)
# 요청 간격 상태는 프로세스 전역(호스트별)이다 — 사건마다 Fetcher 를 새로 만들어도 같은 호스트(korea.kr)에 0초 간격으로 가지 않는다 (10-30 notice-2)
_HOST_LAST: dict[str, float] = {}
_HOST_LOCK = threading.Lock()


class NoticeFetchError(RuntimeError):
    pass


class RobotsDisallowed(NoticeFetchError):
    pass


class Fetcher(Protocol):
    requests: int

    def get(self, url: str) -> str: ...


class HttpFetcher:
    def __init__(
        self,
        *,
        allowed_csv: str,
        extra_domains: Iterable[str],
        user_agent: str,
        interval_sec: float,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.allowed_csv = allowed_csv
        self.extra_domains = tuple(extra_domains)
        self.user_agent = user_agent
        self.interval = max(MIN_INTERVAL_SEC, interval_sec)  # §3: 2초 미만으로는 내리지 않는다
        self.requests = 0
        self._robots: dict[str, RobotsRules] = {}
        self._last: dict[str, float] = {}
        self._sleep = sleep
        self._clock = clock
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=False,
            headers={"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml"},
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpFetcher:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _check(self, url: str) -> str:
        return check_url(url, self.allowed_csv, self.extra_domains)

    def _throttle(self, host: str) -> None:
        with _HOST_LOCK:
            last = _HOST_LAST.get(host, self._last.get(host))
            now = self._clock()
            wait = self.interval - (now - last) if last is not None else 0.0
            # 다음 요청 시각을 먼저 예약해 두면 같은 호스트의 다른 fetcher 도 그 뒤에 줄을 선다
            _HOST_LAST[host] = self._last[host] = (now + wait) if wait > 0 else now
        if wait > 0:
            self._sleep(wait)

    def _raw_get(self, url: str) -> httpx.Response:
        host = self._check(url)
        self._throttle(host)
        self.requests += 1
        return self._client.get(url)

    def robots(self, url: str) -> RobotsRules:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            try:
                r = self._raw_get(origin + "/robots.txt")
                hops = 0
                while r.is_redirect and hops < MAX_REDIRECTS:  # robots.txt 리다이렉트도 검사하며 따라간다
                    hops += 1
                    r = self._raw_get(urljoin(str(r.url), r.headers.get("location", "")))
                rules = rules_for_status(r.status_code, r.text, self.user_agent)
            except BlockedURL:
                raise
            except httpx.HTTPError as exc:
                log.warning("robots.txt 접근 실패 %s: %s — 이 호스트는 읽지 않는다", origin, exc)
                rules = RobotsRules(disallow_all=True)
            self._robots[origin] = rules
        return self._robots[origin]

    def get(self, url: str) -> str:
        self._check(url)
        if not self.robots(url).can_fetch(url):
            raise RobotsDisallowed(f"robots.txt 가 허용하지 않음: {url[:120]}")
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            try:
                r = self._raw_get(current)
            except httpx.HTTPError as exc:
                raise NoticeFetchError(f"요청 실패 {current[:120]}: {type(exc).__name__}") from exc
            if r.is_redirect:
                nxt = urljoin(current, r.headers.get("location", ""))
                self._check(nxt)  # 허용 밖이면 BlockedURL
                if not self.robots(nxt).can_fetch(nxt):
                    raise RobotsDisallowed(f"robots.txt 가 허용하지 않음(리다이렉트): {nxt[:120]}")
                current = nxt
                continue
            if r.status_code >= 400:
                raise NoticeFetchError(f"HTTP {r.status_code}: {current[:120]}")
            ctype = r.headers.get("content-type", "")
            if ctype and not any(t in ctype for t in ("html", "xml", "text")):
                raise NoticeFetchError(f"HTML 이 아닌 응답({ctype}): {current[:120]}")
            if len(r.content) > MAX_BYTES:
                raise NoticeFetchError(f"응답이 너무 큼({len(r.content)}B): {current[:120]}")
            return r.text
        raise NoticeFetchError(f"리다이렉트가 너무 많음: {url[:120]}")


class FixtureFetcher:
    """테스트용: URL → HTML 고정 응답. 허용 검사(guard)는 실제와 똑같이 한다."""

    def __init__(self, pages: dict[str, str], *, allowed_csv: str, extra_domains: Iterable[str] = ()) -> None:
        self.pages = pages
        self.allowed_csv = allowed_csv
        self.extra_domains = tuple(extra_domains)
        self.requests = 0
        self.urls: list[str] = []

    def get(self, url: str) -> str:
        check_url(url, self.allowed_csv, self.extra_domains)
        self.requests += 1
        self.urls.append(url)
        if url not in self.pages:
            raise NoticeFetchError(f"픽스처 없음: {url}")
        return self.pages[url]
