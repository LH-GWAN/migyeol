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
from app.notices.matcher import event_terms, foreign_regions, own_regions, region_term, score_title
from app.notices.parser import parse_body, parse_date, parse_list
from app.notices.robots import parse_robots, rules_for_status
from app.notices.sites import Site, load_sites, notice_org, sites_for_org
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
    # 등록부에 없는 하위 기관은 상위 기관 게시판으로: 지역 해양경찰서 → 해양경찰청(정책브리핑). canonical_org 는 그대로
    assert canonical_org("완도해양경찰서") == "완도해양경찰서" and notice_org("완도해양경찰서") == "해양경찰청"
    assert [s.key for s in sites_for_org("사천해경")] == ["korea-press"] and notice_org("해양경찰청") == "해양경찰청"
    assert {"국가유산청", "국무총리", "국무조정실", "중앙재난안전대책본부"} <= set(next(s for s in sites if s.key == "korea-press").orgs)
    assert canonical_org("국무총리실") == "국무총리" and canonical_org("문화재청") == "국가유산청"


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
    assert [t.generic for t in terms] == [False, True, False, False]  # '지하차도 침수' 는 일반어 구
    m = score_title("가온시, 달빛지하차도 배수펌프 증설 완료", terms)
    assert m.score == pytest.approx(1.0) and m.terms == ["달빛지하차도", "배수펌프"] and m.bonuses == ["조치 어휘: 완료"]
    assert m.labels == ["달빛지하차도", "배수펌프", "+조치 어휘: 완료"]
    m = score_title("지하차도 침수 대비 배수펌프 점검", terms)  # 시설명 없이 일반어 구 + 고유 행동 키워드 + 조치 어휘
    assert m.score == pytest.approx(0.8) and set(m.terms) == {"지하차도 침수", "배수펌프"}
    m = score_title("여름철 배수펌프 점검", terms)
    assert m.score == pytest.approx(0.4)  # 고유 키워드 하나 + 조치 어휘 — 임계값 0.5 미만, 저장하지 않는다
    m = score_title("부산불꽃축제 숙박요금 캠페인", terms)
    assert m.score == 0.0 and m.bonuses == []  # 용어가 하나도 없으면 가점도 없다


def test_match_does_not_double_count_contained_terms():
    terms = event_terms("서리천 제방", ["서리천", "제방 유실"], [])
    m = score_title("서리천 제방 보강공사 준공", terms)
    assert m.terms == ["서리천 제방"] and m.score == pytest.approx(0.5)  # '서리천' 은 '서리천 제방' 에 포함 → 한 번만; 0.4 + 조치 어휘


def test_short_generic_facility_alone_does_not_pass():
    """사건 171: 시설명 '송전선'(3자 일반어)은 단독으로 임계값을 못 넘고, 핵심어·약속어가 하나 더 맞아야 한다."""
    terms = event_terms("송전선", ["산불"], ["한전", "함께"])
    assert all(t.generic for t in terms) and [t.weight for t in terms] == [0.2, 0.2, 0.2, 0.2]
    assert score_title("산림청, 송전선로 공사현장 산사태 사전대비 역량 집중", terms).score == pytest.approx(0.3)  # < 0.5
    assert score_title("해저 송전선로 확산을 견인하는 해저케이블 포설선 취항", terms).score == pytest.approx(0.2)
    assert score_title("송전선로 주변 산불 예방 벌채 점검", terms).score == pytest.approx(0.5)  # 시설명 + 핵심어 + 조치 어휘
    m = score_title("산림청-한전 산불로부터 국가 전력시설 함께 지킨다", terms)
    assert m.score == pytest.approx(0.6) and m.terms == ["산불", "한전", "함께"]  # 시설명 없이 핵심어·약속어 셋
    m = score_title("영암산림항공관리소 한전과 산불대응 간담회 가져", terms)
    assert m.score == pytest.approx(0.4) and m.bonuses == []  # 일반어 둘만 — 시설명·지역명이 없으면 조치 어휘 가점도 없다


def test_long_specific_facility_alone_passes():
    terms = event_terms("통복터널", ["SRT", "단전"], [])
    m = score_title("통복터널 사고 민간자문단 특별위원회 활동 결과 발표", terms)
    assert m.score == pytest.approx(0.6) and m.terms == ["통복터널"] and not terms[0].generic
    assert score_title("가온시, 달빛지하차도 통행 재개", event_terms("달빛지하차도", [], [])).score == pytest.approx(0.6)


def test_generic_phrase_facility_needs_action_word_or_another_term():
    """사건 177: '하천 제방'(일반어 구) 은 0.4 — 조치 어휘(점검·보강·준설…)나 다른 용어가 있어야 저장된다."""
    terms = event_terms("하천 제방", ["태풍 카눈", "군위 제방"], ["특별재난지역"])
    assert terms[0].generic and terms[0].weight == 0.4 and [t.weight for t in terms[1:]] == [0.4, 0.4, 0.3]
    assert score_title("제방 등 전국 73개 국가하천 시설 점검", terms).score == pytest.approx(0.5)
    assert score_title("환경부 장관 “내년 홍수 피해 없도록 하천 준설 등 제방 보강 철저”", terms).score == pytest.approx(0.5)
    assert score_title("하천 제방 사진 공모전 수상작 전시", terms).score == pytest.approx(0.4)  # < 0.5
    assert score_title("태풍 카눈 피해 군위 제방 복구 완료", terms, own=own_regions("군위군", "행정안전부")).score == pytest.approx(0.9)  # 두 구 + 지역 어간 → 조치 어휘 가점
    assert score_title("태풍 카눈 피해 군위 제방 복구 완료", terms).score == pytest.approx(0.8)  # own 을 안 주면 일반어 구 둘만 — 가점 없음


def test_two_generic_keywords_do_not_pass_without_facility_or_region():
    """사건 11·210: '열차'·'사고'·'호우'·'긴급' 같은 일반어 단일 용어는 둘이 맞아도 0.4 이고 가점도 없다.
    시설명·지역명(또는 일반어 구·고유어)이 하나 맞아야 가점이 붙어 저장된다."""
    terms = event_terms("", ["열차", "사고"], ["철도", "선로"], region="대전", org="국토교통부")
    assert score_title("[참고] 철도경찰, 열차 내 상습 절도범 검거", terms).score == pytest.approx(0.4)
    m = score_title("[참고] KTX-산천 열차 궤도 이탈 사고 복구 완료", terms)
    assert m.score == pytest.approx(0.4) and m.terms == ["열차", "사고"] and m.bonuses == []  # 다른 열차 사고 글 — 저장 안 함
    m = score_title("대전 조차장 열차 궤도이탈 사고 복구 완료", terms)
    assert m.score == pytest.approx(0.8) and set(m.terms) == {"열차", "사고", "대전"} and m.bonuses == ["조치 어휘: 복구"]
    terms = event_terms("장동 주택가", ["장동 낙석", "충청권 호우"], ["긴급", "호우"], region="대덕구", org="대덕구")
    assert score_title("한성숙 국무총리, 집중 호우 관련 긴급지시", terms).score == pytest.approx(0.4)  # 호우+긴급 — 빠진다
    assert score_title("농촌진흥청, 경남 거제 호우 피해 농가 긴급 복구 일손 돕기", terms).score == pytest.approx(0.4)
    assert score_title("장동 주택가 낙석 긴급 복구", terms).score == pytest.approx(1.0)  # 시설명 구 0.4 + 핵심어 구 0.4 + 긴급 0.2 + 조치 어휘 → 상한
    assert score_title("장동 주택가 비 피해 지원", terms).score == pytest.approx(0.5)  # 시설명(일반어 구) + 조치 어휘


def test_two_specific_keywords_pass_without_facility():
    terms = event_terms("달빛지하차도", ["지하차도 침수"], ["배수펌프", "차단시설"])
    assert score_title("배수펌프와 차단시설 현황 자료", terms).score == pytest.approx(0.6)  # 고유 키워드 둘 — 조치 어휘 없어도 저장
    assert score_title("배수펌프·차단시설 설치 완료", terms).score == pytest.approx(0.7)


def test_short_term_must_appear_contiguously():
    """3자 이하 용어는 띄어쓰기 없이 그대로 있어야 한다 — '벤처 4대 강국' 에 '4대강' 은 불일치 (숫자 포함 용어는 고유어)."""
    terms = event_terms("4대강", ["홍수"], [])
    assert not terms[0].generic and terms[0].weight == 0.6
    assert score_title("벤처, 정부 ｢벤처 4대 강국 도약 종합대책｣ 발표", terms).score == 0.0
    assert score_title("4대강 별 유역물관리종합계획 최초 수립", terms).score == pytest.approx(0.7)
    assert score_title("5호선 열차 방화 관련 긴급 지시", event_terms("", ["5호선"], [])).score == pytest.approx(0.4)  # 고유 0.3 + 조치


def test_region_term_and_bonuses():
    assert region_term("군위군", "행정안전부") == "군위" and region_term("부평구", "인천시") == "부평" and region_term("대전", "국토교통부") == "대전"
    assert region_term("서울시", "서울시") == "" and region_term("대덕구", "대덕구") == "" and region_term("", "환경부") == ""  # 기관 자신의 이름은 제외
    terms = event_terms("하천 제방", ["하천 범람"], ["선포"], region="군위군", org="행정안전부")
    reg = terms[-1]
    assert reg.kind == "region" and reg.text == "군위" and reg.weight == 0.3 and not reg.generic
    m = score_title("군위 하천 제방 복구 착공", terms)
    assert m.score == pytest.approx(0.8) and set(m.terms) == {"하천 제방", "군위"}
    # 요약 가점: 제목에 없는 지역명이 본문 요약에 있을 때
    m = score_title("국가하천 제방 긴급 점검", terms, snippet="행정안전부는 군위군 등 수해 지역의 제방을 점검했다")
    assert m.score == pytest.approx(0.6) and m.bonuses == ["조치 어휘: 점검", "요약: 군위"]
    # 발생 직후 가점: 발생 전날~7일 뒤
    m = score_title("하천 제방 유실 현장 점검", terms, published_at=date(2023, 8, 12), occurred_at=date(2023, 8, 10))
    assert m.score == pytest.approx(0.6) and "발생 7일 내" in m.bonuses
    assert "발생 7일 내" not in score_title("하천 제방 유실 현장 점검", terms, published_at=date(2023, 9, 12), occurred_at=date(2023, 8, 10)).bonuses
    # 가점은 용어가 하나도 맞지 않으면 붙지 않는다
    assert score_title("군수 동정", terms, snippet="군위군", published_at=date(2023, 8, 10), occurred_at=date(2023, 8, 10)).score == 0.0


def test_foreign_region_in_title_is_penalised_unless_specific_facility():
    """사건 157·195: '제주 해역 어선 전복 긴급지시' 는 옹진·여수 어선 전복 사건의 발표가 아니다 — 다른 지역명이 있으면 −0.3."""
    own = own_regions("옹진군", "해양경찰청")
    assert own == {"옹진", "인천"}
    assert own_regions("여수시", "여수시") == {"여수", "전남"} and own_regions("서울시", "서울시") == {"서울"}
    assert foreign_regions("한성숙 국무총리, 제주해역 어선 전복사고 관련 긴급지시", own) == ["제주"]
    assert foreign_regions("행정안전부 장관, 부산 기장군 어선 전복 사고 관련 긴급지시", own) == ["부산"]  # '기장' 은 보통명사와 겹쳐 사전에서 제외
    assert foreign_regions("인천 옹진 어선 전복 사고 수습", own) == [] and foreign_regions("어선 안전관리 강화", own) == []
    terms = event_terms("", ["어선 전복", "옹진"], ["긴급"], region="옹진군", org="해양경찰청")
    m = score_title("한성숙 국무총리, 제주해역 어선 전복사고 관련 긴급지시", terms, own=own)
    assert m.score == pytest.approx(0.3) and m.penalties == ["다른 지역: 제주"] and m.labels[-1] == "−다른 지역: 제주"  # 0.4+0.2−0.3, 지역 없어 가점 없음
    assert score_title("해경, 옹진 해상 어선 전복 사고 수색 지원", terms, own=own).score == pytest.approx(0.7)  # 자기 지역은 감점 없음 (0.4+옹진 0.2+조치 0.1)
    assert score_title("한성숙 국무총리, 제주해역 어선 전복사고 관련 긴급지시", terms).score == pytest.approx(0.6)  # own 을 안 주면 감점도 지역 가점도 없음 (0.4+0.2)
    # 고유 시설명이 맞으면 다른 지역명이 있어도 감점하지 않는다 (시설명이 사건을 특정한다)
    terms = event_terms("통복터널", ["SRT"], [], region="평택시", org="국토교통부")
    m = score_title("서울~부산 SRT 통복터널 단전 사고 조사 결과", terms, own=own_regions("평택시", "국토교통부"))
    assert m.score == pytest.approx(0.9) and m.penalties == []  # 통복터널 0.6 + SRT 0.2 + 조사 0.1


def test_generic_keyword_pair_needs_region_in_title_or_snippet():
    """사건 195·219: '어선 전복'·'청라 전기차 화재' 같은 일반어 핵심어 쌍만 맞은 글은 사건 지역 어간이 제목이나 요약에 있어야 저장된다."""
    own = own_regions("여수시", "남해지방해양경찰청")
    assert own == {"여수", "전남"}
    terms = event_terms("통발어선", ["어선 전복", "작도", "통발어선", "여수 해상"], ["예방순찰", "훈련"], region="여수시", org="남해지방해양경찰청")
    title = "맞춤형 어선 안전관리로 전복·침몰사고 예방한다"
    m = score_title(title, terms, snippet="해수부 어선 안전관리 대책 마련. 제주와 남해안 해역의 통발어선 전복 사고를 분석", own=own)
    assert m.score == pytest.approx(0.4) and m.bonuses == []  # 지역명 없음 → 요약의 시설명·조치 어휘 가점도 없다
    m = score_title(title, terms, snippet="해수부는 여수 해역 통발어선 전복 사고를 분석해 대책을 마련", own=own)
    assert m.score == pytest.approx(0.6) and m.bonuses == ["조치 어휘: 예방", "요약: 통발어선"]  # 요약의 지역 어간이 가점을 열고, 요약 가점은 시설명이 먼저
    assert score_title("최상목 대통령 권한대행, 여수 거문도 어선 전복 사고 관련 긴급지시", terms, own=own).score == pytest.approx(0.8)  # 지역명이 제목에
    own = own_regions("인천시", "인천시")
    terms = event_terms("청라 아파트 지하주차장", ["청라 전기차 화재", "지하주차장"], ["감식"], region="인천시", org="인천시")
    title = "청라 전기차 화재 피해 아파트 무료 수돗물 수질검사 실시"
    m = score_title(title, terms, snippet="인천 청라국제도시 아파트 지하주차장 전기차 화재로 급수관이 파열", published_at=date(2024, 8, 8), occurred_at=date(2024, 8, 1), own=own)
    assert m.score == pytest.approx(0.6) and m.bonuses == ["발생 7일 내", "요약: 청라 아파트 지하주차장"]
    assert score_title(title, terms, snippet="", published_at=date(2024, 8, 8), occurred_at=date(2024, 8, 1), own=own).score == pytest.approx(0.4)
    # 시설명이 일반어 구여도 시설명이 맞으면 지역명 없이 가점을 받는다 (사건 177 '하천 제방', 217 '태풍 종다리')
    terms = event_terms("태풍 종다리", ["차량 침수"], [], region="울산시", org="중앙재난안전대책본부")
    assert score_title("제9호 태풍 종다리 북상 대비 대책 점검", terms, published_at=date(2024, 8, 19), occurred_at=date(2024, 8, 20), own={"울산"}).score == pytest.approx(0.6)


def test_settings_defaults_used_by_notices():
    s = Settings(_env_file=None)
    assert s.notice_trigger_days == 90 and s.notice_max_pages == 5 and s.notice_request_interval_sec >= 2.0
    assert s.notice_match_threshold == 0.5 and "migyeol" in s.notice_user_agent
