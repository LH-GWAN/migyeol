"""v2 §3 공공기관 게시판 확인 — 네트워크 없는 단위 테스트 (트랙 C).

고정 HTML 픽스처(실제 게시판 구조를 줄인 것)로 파서를, httpx.MockTransport 로 요청기(robots.txt·리다이렉트·간격)를,
허용·차단 목록으로 도메인 검사를 고정한다. 등록부(sites.yaml)는 실제 게시판 ≥10곳이고 모두 허용 도메인이어야 한다.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from app.config import Settings
from app.notices.fetcher import FixtureFetcher, HttpFetcher, NoticeFetchError, RobotsDisallowed
from app.notices.guard import NEWS_DOMAINS, BlockedURL, check_url, is_news_host
from app.notices.matcher import event_terms, score_title
from app.notices.parser import parse_body, parse_date, parse_list
from app.notices.robots import parse_robots, rules_for_status
from app.notices.sites import Site, load_sites, sites_for_org
from app.rules.orgs import canonical_org

ALLOWED = ".go.kr,.or.kr,korea.kr"

# ---------------------------------------------------------------------------
# 픽스처 HTML (실제 목록 구조 축약: 서울시 표형, 부산시 갤러리형, 정책브리핑 목록형)
# ---------------------------------------------------------------------------

SEOUL_LIST = """
<div id="seoul-integrated-board"><table class="sib-lst-type-basic"><tbody>
<tr><td class="sib-lst-type-basic-tablet-hidden">64114</td>
<td class="sib-lst-type-basic-subject"><a href="javascript:fnTbbsView('468665');">(보도참고자료) 서울시, 고척돔 지붕 철골 97개 보강 완료</a></td>
<td class="sib-lst-type-basic-tablet-hidden">체육정책과</td><td>2026-10-22</td></tr>
<tr><td>64113</td><td class="sib-lst-type-basic-subject"><a href="javascript:fnTbbsView('468638');">서울시, 핼러윈 대비 인파안전관리 강화</a></td>
<td>재난안전예방과</td><td>2026-10-21</td></tr>
<tr><td colspan="4">게시글이 없습니다.</td></tr>
</tbody></table></div>
"""

BUSAN_LIST = """
<div class="boardTextGallery seWrap"><ul>
<li><a class="item" href="/nbtnewsBU/1760187?curPage=&amp;srchKey="><div class="tgCont se">
<div class="bTitle">부산시, 달빛지하차도 배수펌프 증설 공사 착공</div>
<div class="tgTxt"><div class="txtL writer"><strong>도로계획과</strong> | 051-888-0000 | 2026-03-02</div>
<div class="txtL"> ◈ 침수 피해 지하차도 배수펌프 용량 2배 증설 착공</div></div></div></a></li>
<li><a class="item" href="/nbtnewsBU/1760100"><div class="tgCont se"><div class="bTitle">부산불꽃축제 숙박요금 캠페인</div>
<div class="tgTxt"><div class="txtL writer">관광정책과 | 2025-06-30</div></div></div></a></li>
</ul></div>
"""

BUSAN_DETAIL = """<html><body><div class="boardView"><div class="form-group">부제목 ◈ 배수펌프 용량 2배 증설 착공 <script>x()</script></div></div></body></html>"""

KOREA_LIST = """
<div class="list_type"><ul>
<li><a href="/briefing/pressReleaseView.do?newsId=156786150&amp;pageIndex=1"><span class="text">
<strong>지하차도 침수 방지 대책 추진 현황 점검</strong><span class="lead">행정안전부는 전국 지하차도 진입 차단시설 설치 현황을 점검했다.</span>
<span class="source"><span>2026.10.22</span><span>행정안전부</span></span></span></a></li>
</ul></div>
"""


def _site(**kw) -> Site:
    base = dict(key="t", name="테스트", orgs=("서울시",), domain="seoul.go.kr", list_url="https://www.seoul.go.kr/list?p={page}", item="li")
    base.update(kw)
    return Site(**base)


SEOUL = _site(
    item="table.sib-lst-type-basic tbody tr",
    title="td.sib-lst-type-basic-subject a",
    link="td.sib-lst-type-basic-subject a",
    date="td:last-child",
    link_id_regex=r"fnTbbsView\('(\d+)'\)",
    link_template="https://www.seoul.go.kr/news/news_report.do?bbsNo=158&nttNo={id}",
)
BUSAN = _site(
    key="b", orgs=("부산시",), domain="busan.go.kr", list_url="https://www.busan.go.kr/nbtnewsBU?curPage={page}",
    item="div.boardTextGallery ul > li", title="div.bTitle", link="a.item", date="div.writer",
    snippet="div.tgTxt div.txtL:not(.writer)", body="div.boardView div.form-group",
)
KOREA = _site(key="k", orgs=("행정안전부",), domain="korea.kr", list_url="https://www.korea.kr/briefing/pressReleaseList.do?pageIndex={page}",
              item="div.list_type ul > li", title="span.text strong", link="a", date="span.source", snippet="span.lead")


# ---------------------------------------------------------------------------
# 파서
# ---------------------------------------------------------------------------


def test_parse_seoul_table_with_javascript_links():
    items = parse_list(SEOUL_LIST, SEOUL, SEOUL.page_url(1))
    assert [i.url for i in items] == [
        "https://www.seoul.go.kr/news/news_report.do?bbsNo=158&nttNo=468665",
        "https://www.seoul.go.kr/news/news_report.do?bbsNo=158&nttNo=468638",
    ]
    assert items[0].title.startswith("(보도참고자료) 서울시, 고척돔")
    assert items[0].published_at == date(2026, 10, 22) and items[1].published_at == date(2026, 10, 21)


def test_parse_busan_gallery_relative_links_snippet_and_body():
    items = parse_list(BUSAN_LIST, BUSAN, BUSAN.page_url(1))
    assert len(items) == 2
    assert items[0].url.startswith("https://www.busan.go.kr/nbtnewsBU/1760187")
    assert items[0].published_at == date(2026, 3, 2)
    assert "배수펌프" in items[0].snippet and len(items[0].snippet) <= 300
    body = parse_body(BUSAN_DETAIL, BUSAN)
    assert body.startswith("부제목") and "x()" not in body  # script 제거


def test_parse_korea_list_and_dates():
    items = parse_list(KOREA_LIST, KOREA, KOREA.page_url(1))
    assert items[0].published_at == date(2026, 10, 22) and items[0].snippet.startswith("행정안전부는")
    assert parse_date("2026. 10. 2.") == date(2026, 10, 2)
    assert parse_date("작성일 2025년 7월 18일") == date(2025, 7, 18)
    assert parse_date("26.07.18") == date(2026, 7, 18)
    assert parse_date("제목만") is None


def test_parse_body_without_selector_is_empty():
    assert parse_body("<div class='x'>본문</div>", SEOUL) == ""  # 선택자가 없으면 본문을 추측해 긁지 않는다


# ---------------------------------------------------------------------------
# 도메인 차단 (§2-1·§3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://www.chosun.com/national/2025/07/16/",
        "https://n.news.naver.com/mnews/article/001/0000000001",
        "https://news.kbs.co.kr/news/view.do?ncd=1",
        "https://www.yna.co.kr/view/AKR20250716",
        "https://www.busan.com/view/busan/view.php?code=1",  # 지역 일간지 (부산시청 busan.go.kr 과 다르다)
        "https://www.bigkinds.or.kr/v2/news/index.do",  # .or.kr 이지만 뉴스 서비스
        "https://www.kinds.or.kr/",
        "https://news.example.com/a",
    ],
)
def test_news_domains_are_blocked_even_if_suffix_allowed(url):
    with pytest.raises(BlockedURL, match="뉴스"):
        check_url(url, ALLOWED, extra_domains=["chosun.com", "busan.com"])  # 등록부에 넣어도 거부


@pytest.mark.parametrize("url", ["https://example.com/a", "ftp://www.seoul.go.kr/a", "https://evil-go.kr/a", "https://go.kr.evil.com/a"])
def test_non_allowed_domains_are_blocked(url):
    with pytest.raises(BlockedURL):
        check_url(url, ALLOWED)


def test_allowed_domains():
    assert check_url("https://www.seoul.go.kr/news/news_report.do", ALLOWED) == "www.seoul.go.kr"
    assert check_url("https://www.korea.kr/briefing/pressReleaseList.do", ALLOWED) == "www.korea.kr"
    assert check_url("https://gnews.gg.go.kr/briefing/brief_gongbo.do", ALLOWED)  # 공공 도메인의 news 라벨은 허용
    assert check_url("https://city.example.org/press", ALLOWED, extra_domains=["example.org"]) == "city.example.org"
    assert all(is_news_host("www." + d) for d in list(NEWS_DOMAINS)[:20])


def test_sites_registry_real_sites_are_allowed_and_canonical():
    sites = load_sites()
    assert len(sites) >= 10, "실제 게시판 10곳 이상 (v2 §11-C)"
    wide = {"서울시", "부산시", "대구시", "인천시", "광주시", "대전시", "울산시", "경기도", "강원도", "충북", "충남", "전북", "전남", "경북", "경남"}
    covered = {o for s in sites for o in s.orgs} & wide
    assert len(covered) >= 10, covered
    for s in sites:
        assert "{page}" in s.list_url
        for url in [s.page_url(1), s.page_url(2)] + ([s.page_url(1, "지하차도")] if s.search_url else []):
            check_url(url, ALLOWED, [x.domain for x in sites])  # 등록부의 모든 URL 은 허용 도메인 (뉴스 도메인 아님)
        assert all(canonical_org(o) == o for o in s.orgs), s.orgs  # orgs 는 정규화 키
    assert [s.key for s in sites_for_org("서울특별시")] == ["seoul-press"]
    assert {s.key for s in sites_for_org("광주광역시")} == {"gwangju-press-archive", "jeonnam-gwangju-press"}
    assert sites_for_org("가온시") == [] and sites_for_org("") == []


def test_sites_registry_rejects_news_domain(tmp_path):
    p = tmp_path / "sites.yaml"
    p.write_text('sites:\n  - {key: x, orgs: [서울시], domain: chosun.com, list_url: "https://www.chosun.com/?p={page}", item: li}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="뉴스 도메인"):
        load_sites(p)


# ---------------------------------------------------------------------------
# robots.txt (RFC 9309)
# ---------------------------------------------------------------------------


def test_robots_longest_match_allows_seoul_news():
    text = "User-agent: *\nDisallow: /\nDisallow: /seoul/sos\n\nAllow: /$\nAllow: /main\nAllow: /news\n"
    r = parse_robots(text, "migyeol/0.3 (x)")
    assert r.can_fetch("https://www.seoul.go.kr/news/news_report.do?bbsNo=158")
    assert r.can_fetch("https://www.seoul.go.kr/")
    assert not r.can_fetch("https://www.seoul.go.kr/seoul/sos")
    assert not r.can_fetch("https://www.seoul.go.kr/other")


def test_robots_groups_wildcards_and_status():
    text = "User-agent: Googlebot\nDisallow: /\n\nUser-agent: *\nAllow: /\nDisallow: /admin/*\nDisallow: /*.pdf$\n"
    r = parse_robots(text, "migyeol/0.3")
    assert r.can_fetch("https://x.go.kr/board/list.do")
    assert not r.can_fetch("https://x.go.kr/admin/a")
    assert not r.can_fetch("https://x.go.kr/file/a.pdf") and r.can_fetch("https://x.go.kr/file/a.pdf?x=1")
    assert not parse_robots("User-agent: migyeol\nDisallow: /\n\nUser-agent: *\nAllow: /\n", "migyeol/0.3").can_fetch("https://x.go.kr/a")
    assert not parse_robots("User-agent: *\nDisallow: /\n", "migyeol/0.3").can_fetch("https://x.go.kr/a")
    assert rules_for_status(401, "", "m").can_fetch("https://x.go.kr/a")  # 4xx: 제한 없음
    assert not rules_for_status(503, "", "m").can_fetch("https://x.go.kr/a")  # 5xx: 접근 불가


# ---------------------------------------------------------------------------
# 요청기 (MockTransport — 네트워크 없음)
# ---------------------------------------------------------------------------


def _fetcher(handler, sleeps: list[float]) -> HttpFetcher:
    t = [0.0]

    def clock() -> float:
        return t[0]

    def sleep(s: float) -> None:
        sleeps.append(s)
        t[0] += s

    return HttpFetcher(allowed_csv=ALLOWED, extra_domains=[], user_agent="migyeol/0.3 (test)", interval_sec=2.0, transport=httpx.MockTransport(handler), sleep=sleep, clock=clock)


def test_fetcher_respects_robots_interval_and_user_agent():
    seen: list[tuple[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((str(req.url), req.headers["user-agent"]))
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
        return httpx.Response(200, text="<html>ok</html>", headers={"content-type": "text/html"})

    sleeps: list[float] = []
    f = _fetcher(handler, sleeps)
    assert "ok" in f.get("https://www.seoul.go.kr/news/1")
    assert "ok" in f.get("https://www.seoul.go.kr/news/2")
    with pytest.raises(RobotsDisallowed):
        f.get("https://www.seoul.go.kr/private/x")
    assert [u for u, _ in seen] == ["https://www.seoul.go.kr/robots.txt", "https://www.seoul.go.kr/news/1", "https://www.seoul.go.kr/news/2"]
    assert all(ua == "migyeol/0.3 (test)" for _, ua in seen)
    assert sleeps and all(abs(s - 2.0) < 1e-9 for s in sleeps)  # 같은 호스트 연속 요청은 2초 간격
    assert f.requests == 3


def test_fetcher_blocks_redirect_to_news_domain_and_never_requests_it():
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"location": "https://www.chosun.com/article/1"})

    f = _fetcher(handler, [])
    with pytest.raises(BlockedURL):
        f.get("https://www.busan.go.kr/nbtnewsBU/1")
    assert not any("chosun" in u for u in seen)
    with pytest.raises(BlockedURL):
        f.get("https://n.news.naver.com/article/1")  # 처음부터 뉴스 도메인이면 robots.txt 조차 요청하지 않는다
    assert not any("naver" in u for u in seen)


def test_fetcher_unreachable_robots_means_no_access_and_rejects_binary():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(503)
        return httpx.Response(200, text="x")

    with pytest.raises(RobotsDisallowed):
        _fetcher(handler, []).get("https://www.incheon.go.kr/IC010205")

    def handler2(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})

    with pytest.raises(NoticeFetchError, match="HTML"):
        _fetcher(handler2, []).get("https://www.incheon.go.kr/file.pdf")


def test_fixture_fetcher_applies_same_guard():
    f = FixtureFetcher({"https://www.chosun.com/a": "x"}, allowed_csv=ALLOWED, extra_domains=["chosun.com"])
    with pytest.raises(BlockedURL):
        f.get("https://www.chosun.com/a")
    assert f.requests == 0


# ---------------------------------------------------------------------------
# 매칭
# ---------------------------------------------------------------------------


def test_match_scores_facility_keywords_and_promise_terms():
    terms = event_terms("달빛지하차도", ["지하차도 침수", "달빛지하차도"], ["배수펌프", "차단시설"])
    assert [t.text for t in terms] == ["달빛지하차도", "지하차도 침수", "배수펌프", "차단시설"]  # 중복 제거
    s, m = score_title("가온시, 달빛지하차도 배수펌프 증설 완료", terms)
    assert s == pytest.approx(0.9) and m == ["달빛지하차도", "배수펌프"]
    s, m = score_title("지하차도 침수 대비 배수펌프 점검", terms)  # 시설명 없이 핵심어 + 행동 키워드
    assert s == pytest.approx(0.6) and set(m) == {"지하차도 침수", "배수펌프"}
    s, _ = score_title("여름철 배수펌프 점검", terms)
    assert s == pytest.approx(0.3)  # 임계값 0.5 미만 — 저장하지 않는다
    s, _ = score_title("부산불꽃축제 숙박요금 캠페인", terms)
    assert s == 0.0


def test_match_does_not_double_count_contained_terms():
    terms = event_terms("서리천 제방", ["서리천", "제방 유실"], [])
    s, m = score_title("서리천 제방 보강공사 준공", terms)
    assert m == ["서리천 제방"] and s == pytest.approx(0.6)  # '서리천' 은 '서리천 제방' 에 포함 → 한 번만


def test_settings_defaults_used_by_notices():
    s = Settings(_env_file=None)
    assert s.notice_trigger_days == 90 and s.notice_max_pages == 5 and s.notice_request_interval_sec >= 2.0
    assert s.notice_match_threshold == 0.5 and "migyeol" in s.notice_user_agent
