"""게시판 목록·상세 HTML 파서 — 저장소에서 HTML 파서(bs4)를 import 하는 곳은 app/notices/ 뿐이다 (tests/test_repo_rules.py).

목록 페이지: 항목마다 제목·게시일·URL(+ 목록에 있으면 요약). 상세 페이지: 본문 첫 300자만 (첨부·이미지 없음).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .sites import Site

SNIPPET_CHARS = 300
_DATE_RE = re.compile(r"(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")
_SHORT_DATE_RE = re.compile(r"\b(\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})\b")
_WS = re.compile(r"\s+")


@dataclass
class NoticeItem:
    title: str
    url: str
    published_at: date | None
    snippet: str = ""


def clean(text: str | None) -> str:
    return _WS.sub(" ", text or "").strip()


def parse_date(text: str | None) -> date | None:
    """'2026-10-22' '2026.10.22' '2026. 10. 22.' '2026년 10월 22일' '26.10.22' → date. 없으면 None."""
    t = text or ""
    for rx, century in ((_DATE_RE, 0), (_SHORT_DATE_RE, 2000)):
        for m in rx.finditer(t):
            y, mo, d = (int(g) for g in m.groups())
            try:
                return date(y + century, mo, d)
            except ValueError:
                continue
    return None


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _item_url(site: Site, el, base_url: str) -> str | None:
    a = el.select_one(site.link) if site.link else el
    if a is None:
        return None
    href = (a.get("href") or "").strip()
    onclick = (a.get("onclick") or "").strip()
    if site.link_id_regex and site.link_template:
        m = re.search(site.link_id_regex, f"{href} {onclick}")
        if m:
            return site.link_template.format(id=m.group(1))
    if not href or href.startswith(("javascript:", "#")):
        return None
    return urljoin(base_url, href)


def parse_list(html: str, site: Site, base_url: str) -> list[NoticeItem]:
    """목록 페이지 → 항목. 제목·URL 이 없는 행(공지 머리글·'게시글이 없습니다')은 건너뛴다. 같은 URL 은 한 번만."""
    soup = _soup(html)
    out: list[NoticeItem] = []
    seen: set[str] = set()
    for el in soup.select(site.item):
        t_el = el.select_one(site.title) if site.title else (el.select_one(site.link) if site.link else el)
        title = clean(t_el.get_text(" ", strip=True) if t_el is not None else "")
        url = _item_url(site, el, base_url)
        if not title or not url or url in seen:
            continue
        d_el = el.select_one(site.date) if site.date else None
        published = parse_date(d_el.get_text(" ", strip=True) if d_el is not None else el.get_text(" ", strip=True))
        snippet = ""
        if site.snippet:
            s_el = el.select_one(site.snippet)
            snippet = clean(s_el.get_text(" ", strip=True) if s_el is not None else "")[:SNIPPET_CHARS]
        seen.add(url)
        out.append(NoticeItem(title=title[:300], url=url, published_at=published, snippet=snippet))
    return out


def parse_body(html: str, site: Site) -> str:
    """상세 페이지 본문 첫 300자. 선택자가 없거나 못 찾으면 빈 문자열(본문 전체를 추측해 긁지 않는다)."""
    if not site.body:
        return ""
    soup = _soup(html)
    el = soup.select_one(site.body)
    if el is None:
        return ""
    for tag in el.select("script, style, img, iframe"):
        tag.decompose()
    return clean(el.get_text(" ", strip=True))[:SNIPPET_CHARS]
