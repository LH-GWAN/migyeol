"""공공기관 게시판 등록부(sites.yaml) — v2 §3.

등록부는 기관(canonical_org 키) → 공식 보도자료·공지사항 게시판의 목록 URL·항목 선택자·도메인이다.
한 게시판이 여러 기관을 맡을 수 있고(행정통합 뒤의 새 누리집, 정책브리핑의 중앙부처), 한 기관이 여러 게시판을 가질 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from ..rules.orgs import canonical_org, wide_alias
from .guard import is_news_host

SITES_FILE = Path(__file__).resolve().parent / "sites.yaml"


@dataclass(frozen=True)
class Site:
    key: str
    name: str
    orgs: tuple[str, ...]  # canonical_org 키
    domain: str  # 등록 도메인 (요청 허용 목록에 더해진다)
    list_url: str  # {page} 자리표시 (1부터)
    item: str  # 목록 항목 CSS 선택자
    title: str | None = None  # 항목 안 제목 선택자 (없으면 링크 글자)
    link: str | None = "a"  # 항목 안 링크 선택자
    date: str | None = None  # 항목 안 게시일 선택자 (없으면 항목 글자에서 날짜 정규식)
    snippet: str | None = None  # 목록에 요약이 있으면 그 선택자 (매칭 항목만 저장)
    body: str | None = None  # 상세 페이지 본문 선택자 (매칭 항목만, 첫 300자)
    link_id_regex: str | None = None  # javascript: 링크에서 글 번호 추출
    link_template: str | None = None  # {id} 로 상세 URL 조립
    search_url: str | None = None  # {query} {page} — 게시판 자체 제목 검색 (목록 페이지와 같은 형식)
    encoding: str | None = None
    note: str = ""
    verified: dict = field(default_factory=dict, compare=False, hash=False)  # {date, items, pages} 확인 기록

    def page_url(self, page: int, query: str | None = None) -> str:
        if query and self.search_url:
            from urllib.parse import quote

            return self.search_url.format(page=page, query=quote(query))
        return self.list_url.format(page=page)

    @property
    def first_page_url(self) -> str:
        return self.page_url(1)


def _site_from(raw: dict) -> Site:
    orgs = tuple(canonical_org(o) for o in raw.get("orgs") or [])
    site = Site(
        key=str(raw["key"]),
        name=str(raw.get("name") or raw["key"]),
        orgs=orgs,
        domain=str(raw["domain"]).lower(),
        list_url=str(raw["list_url"]),
        item=str(raw["item"]),
        title=raw.get("title"),
        link=raw.get("link", "a"),
        date=raw.get("date"),
        snippet=raw.get("snippet"),
        body=raw.get("body"),
        link_id_regex=raw.get("link_id_regex"),
        link_template=raw.get("link_template"),
        search_url=raw.get("search_url"),
        encoding=raw.get("encoding"),
        note=str(raw.get("note") or ""),
        verified=dict(raw.get("verified") or {}),
    )
    if not site.orgs:
        raise ValueError(f"sites.yaml {site.key}: orgs 가 비었다")
    if is_news_host(site.domain):
        raise ValueError(f"sites.yaml {site.key}: 뉴스 도메인은 등록할 수 없다 ({site.domain})")
    if "{page}" not in site.list_url:
        raise ValueError(f"sites.yaml {site.key}: list_url 에 {{page}} 가 없다")
    return site


def load_sites(path: Path | None = None) -> list[Site]:
    if path is None:
        return list(_load_default())
    return _load(path)


def _load(path: Path) -> list[Site]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raws = data.get("sites") if isinstance(data, dict) else data
    sites = [_site_from(r) for r in raws or []]
    keys = [s.key for s in sites]
    if len(keys) != len(set(keys)):
        raise ValueError("sites.yaml: key 중복")
    return sites


@lru_cache
def _load_default() -> tuple[Site, ...]:
    return tuple(_load(SITES_FILE))


# 등록부에 없는 하위 기관을 상위 기관 게시판으로 묶는 규칙 (정규 키 접미 → 상위 정규 키). canonical_org 는 바꾸지 않는다.
PARENT_ORG_SUFFIX: dict[str, str] = {"해양경찰서": "해양경찰청"}  # '완도해양경찰서' → 해양경찰청(정책브리핑)


def notice_org(org: str | None) -> str:
    """게시판을 찾을 때 쓰는 기관 키: 정규화 뒤, 등록부에 없는 하위 기관은 상위 기관으로."""
    key = canonical_org(org)
    for suffix, parent in PARENT_ORG_SUFFIX.items():
        if key.endswith(suffix) and key != suffix and len(key) > len(suffix):
            return parent
    return key


def sites_for_org(org: str | None, sites: list[Site] | None = None) -> list[Site]:
    key = notice_org(org)
    if not key:
        return []
    pool = sites if sites is not None else load_sites()
    found = [s for s in pool if key in s.orgs]
    if not found and (wide := wide_alias(org)):
        found = [s for s in pool if wide in s.orgs]  # '인천 동구' → 동구 게시판이 없으면 인천시 게시판 (광역은 별칭)
    return found
