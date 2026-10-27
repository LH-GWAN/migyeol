"""v2 §3·§7.3 게시판 확인 트리거·저장·API — 가상 픽스처 DB(기준일 2026-09-11) + 고정 HTML (네트워크 없음).

가상 사건 상태(픽스처): 1 달빛지하차도 후속보도 부족(마지막 후속 2026-03-05, 190일) · 6 청솔산 후속보도 부족(2025-12-10)
· 7 한빛대교 판단 불가(2026-08-05, 37일) · 2·5 조치 완료 · 4·8 조치 진행 · 3 새로운 문제.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import DISCLAIMER, STATUS_NO_FOLLOWUP, STATUS_UNDETERMINED, get_settings
from app.db.models import Event
from app.db.session import init_db, reset_engine, session_factory
from app.notices import service
from app.notices.fetcher import FixtureFetcher
from app.notices.models import NoticeCheck, OrgNotice
from app.notices.sites import Site

TODAY = date(2026, 9, 11)

GAON = Site(
    key="gaon-press", name="가온시 보도자료(테스트)", orgs=("가온시",), domain="gaon.go.kr",
    list_url="https://www.gaon.go.kr/press?page={page}",
    search_url="https://www.gaon.go.kr/press?q={query}&page={page}",
    item="table tbody tr", title="td.subject a", link="td.subject a", date="td.date", body="div.view",
)


def _row(i: int, title: str, d: str) -> str:
    return f'<tr><td>{i}</td><td class="subject"><a href="/press/view?id={i}">{title}</a></td><td class="date">{d}</td></tr>'


def _page(rows: list[str]) -> str:
    return "<table><tbody>" + "".join(rows) + "</tbody></table>"


PAGES = {
    # 시설명 검색 결과 1쪽: 매칭 2건 + 비매칭 1건 + 발생일 이전 글 1건
    "https://www.gaon.go.kr/press?q=%EB%8B%AC%EB%B9%9B%EC%A7%80%ED%95%98%EC%B0%A8%EB%8F%84&page=1": _page(
        [
            _row(31, "달빛지하차도 배수펌프 증설 공사 준공", "2026-06-30"),
            _row(30, "가온시, 달빛지하차도 진입 차단시설 설치 완료", "2026-04-02"),
            _row(29, "가온시 시민 체육대회 개최", "2026-03-01"),
            _row(12, "달빛지하차도 개통 10주년", "2015-07-01"),
        ]
    ),
    "https://www.gaon.go.kr/press?q=%EB%8B%AC%EB%B9%9B%EC%A7%80%ED%95%98%EC%B0%A8%EB%8F%84&page=2": _page([]),
    "https://www.gaon.go.kr/press?q=%EC%A7%80%ED%95%98%EC%B0%A8%EB%8F%84%20%EC%B9%A8%EC%88%98&page=1": _page([]),
    "https://www.gaon.go.kr/press/view?id=31": '<div class="view">가온시는 달빛지하차도 배수펌프 용량을 2배로 늘리는 공사를 마쳤다고 밝혔다.</div>',
    "https://www.gaon.go.kr/press/view?id=30": '<div class="view">진입 차단시설 4곳 설치.</div>',
}


@pytest.fixture(scope="module")
def notices_db(tmp_path_factory, fixtures_ready):
    from app.bigkinds import build_client
    from app.llm.client import build_llm
    from app.pipeline.embeddings import build_embedding
    from app.pipeline.runner import Pipeline

    url = f"sqlite:///{tmp_path_factory.mktemp('notices') / 'notices.db'}"
    get_settings.cache_clear()
    os.environ["DATABASE_URL"] = url
    settings = get_settings()
    reset_engine()
    init_db(url, drop=True)
    session = session_factory()()
    Pipeline(session, settings, build_client(settings), build_llm(settings), build_embedding(settings)).run("all")
    session.close()
    return url


@pytest.fixture
def session(notices_db):
    get_settings.cache_clear()
    os.environ["DATABASE_URL"] = notices_db
    reset_engine()
    init_db(notices_db)
    s = session_factory()()
    s.query(OrgNotice).delete()
    s.query(NoticeCheck).delete()
    for ev in s.query(Event):
        if hasattr(Event, "org_notice_found"):
            ev.org_notice_found = False
    s.commit()
    yield s
    s.close()


def _ev(session, title_part: str) -> Event:
    return next(e for e in session.query(Event).all() if title_part in e.title)


class _NoNetwork:
    """트리거되지 않은 사건·게시판 없는 사건이 요청을 보내면 실패."""

    requests = 0

    def get(self, url: str) -> str:  # pragma: no cover - 호출되면 테스트 실패
        raise AssertionError(f"요청하면 안 된다: {url}")


# ---------------------------------------------------------------------------
# 트리거 (§3)
# ---------------------------------------------------------------------------


def test_trigger_rules_on_fixture_events(session):
    st = get_settings()
    got = {e.id: service.trigger_for(session, e, st, TODAY) for e in session.query(Event).all()}
    by_title = {e.title: got[e.id] for e in session.query(Event).all()}
    assert by_title["가온시 달빛지하차도 침수 사고"].triggered  # 후속보도 부족, 190일
    assert by_title["누리군 청솔산 산사태"].triggered
    t7 = by_title["가온시 한빛대교 접속도로 연쇄 추돌 사고"]
    assert not t7.triggered and t7.days == 37 and "90일 미만" in t7.reason  # 판단 불가지만 기간 미달
    for e in session.query(Event).all():
        if e.status not in (STATUS_NO_FOLLOWUP, STATUS_UNDETERMINED):
            assert not got[e.id].triggered and "게시판 확인은" in got[e.id].reason


def test_trigger_basis_is_max_of_followup_promise_and_occurrence(session):
    st = get_settings()
    ev = _ev(session, "달빛지하차도")
    t = service.trigger_for(session, ev, st, TODAY)
    assert t.basis_date == date(2026, 3, 5)  # 마지막 후속 기사일이 가장 늦다
    # 기준일 89일 뒤는 미충족, 90일 뒤는 충족 (경계)
    assert not service.trigger_for(session, ev, st, t.basis_date + timedelta(days=89)).triggered
    assert service.trigger_for(session, ev, st, t.basis_date + timedelta(days=90)).triggered
    # 후속 기사가 없으면 약속 발언일·발생일 중 늦은 날이 기준
    saved = ev.last_followup_at
    ev.last_followup_at = None
    try:
        t2 = service.trigger_for(session, ev, st, TODAY)
        spoken = service.latest_promise_date(session, ev)
        assert spoken is not None and spoken > ev.occurred_at  # 가온시 대응 발언(7월 중순) > 발생일 2025-07-15
        assert t2.basis_date == spoken
    finally:
        ev.last_followup_at = saved
        session.rollback()


def test_not_triggered_event_sends_no_request(session):
    st = get_settings()
    ev = _ev(session, "한빛대교")
    out = service.check_event(session, ev, st, TODAY, fetcher=_NoNetwork(), sites=[GAON])
    assert out["outcome"] == "not_triggered" and out["requests"] == 0
    chk = session.query(NoticeCheck).filter_by(event_id=ev.id).one()
    assert chk.triggered is False and chk.outcome == "not_triggered"


def test_triggered_event_without_registered_site_sends_no_request(session):
    st = get_settings()
    ev = _ev(session, "청솔산")  # 누리군 — 등록부에 없음
    out = service.check_event(session, ev, st, TODAY, fetcher=_NoNetwork(), sites=[GAON])
    assert out["triggered"] and out["outcome"] == "no_site" and out["requests"] == 0


# ---------------------------------------------------------------------------
# 확인 실행·매칭·저장
# ---------------------------------------------------------------------------


def test_check_event_matches_titles_and_stores_snippets(session):
    st = get_settings()
    ev = _ev(session, "달빛지하차도")
    f = FixtureFetcher(PAGES, allowed_csv=st.notice_allowed_domains, extra_domains=[GAON.domain])
    out = service.check_event(session, ev, st, TODAY, fetcher=f, sites=[GAON])
    assert out["outcome"] == "found" and out["items_found"] == 2
    rows = session.query(OrgNotice).filter_by(event_id=ev.id).order_by(OrgNotice.published_at.desc()).all()
    assert [r.title for r in rows] == ["달빛지하차도 배수펌프 증설 공사 준공", "가온시, 달빛지하차도 진입 차단시설 설치 완료"]
    assert rows[0].match_score >= st.notice_match_threshold and "달빛지하차도" in rows[0].matched_terms and "배수펌프" in rows[0].matched_terms
    assert rows[0].snippet.startswith("가온시는 달빛지하차도") and len(rows[0].snippet) <= 300
    assert rows[0].url == "https://www.gaon.go.kr/press/view?id=31" and rows[0].org == "가온시"
    # 상세 본문은 매칭 항목만, 비매칭·발생일 이전 글은 요청하지 않는다
    assert "https://www.gaon.go.kr/press/view?id=29" not in f.urls and "https://www.gaon.go.kr/press/view?id=12" not in f.urls
    assert len([u for u in f.urls if "/press?" in u]) <= st.notice_max_pages
    if hasattr(Event, "org_notice_found"):
        session.refresh(ev)
        assert ev.org_notice_found is True
    # 다시 실행해도 중복 저장하지 않는다
    service.check_event(session, ev, st, TODAY, fetcher=FixtureFetcher(PAGES, allowed_csv=st.notice_allowed_domains, extra_domains=[GAON.domain]), sites=[GAON])
    assert session.query(OrgNotice).filter_by(event_id=ev.id).count() == 2


def test_check_event_page_budget_and_fetch_error_is_recorded(session):
    st = get_settings()
    ev = _ev(session, "달빛지하차도")
    noisy = Site(key="noisy", name="잡음", orgs=("가온시",), domain="gaon.go.kr", list_url="https://www.gaon.go.kr/all?page={page}", item="table tbody tr", title="td.subject a", link="td.subject a", date="td.date")
    pages = {f"https://www.gaon.go.kr/all?page={p}": _page([_row(100 + p, f"공지 {p}", "2026-09-01")]) for p in range(1, 20)}
    f = FixtureFetcher(pages, allowed_csv=st.notice_allowed_domains, extra_domains=["gaon.go.kr"])
    out = service.check_event(session, ev, st, TODAY, fetcher=f, sites=[noisy])
    assert f.requests == st.notice_max_pages and out["outcome"] == "not_found"  # 사이트당 목록 최대 N쪽
    broken = FixtureFetcher({}, allowed_csv=st.notice_allowed_domains, extra_domains=["gaon.go.kr"])
    out = service.check_event(session, ev, st, TODAY, fetcher=broken, sites=[noisy])
    assert out["outcome"] == "error" and out["sites"][0]["error"]


def test_fixture_event_check_uses_real_registry_without_sites_for_fictional_orgs(session):
    """실제 등록부에는 가상 기관(가온시·누리군)이 없다 → 트리거된 사건도 '등록된 게시판 없음', 요청 0건."""
    out = service.run_checks(session, get_settings(), today=TODAY, fetcher=_NoNetwork())
    assert out["events"] == 8 and out["triggered"] == 2 and out["requests"] == 0
    assert {r["outcome"] for r in out["results"]} == {"not_triggered", "no_site"}


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.fixture
def client(session):
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_api_states(client, session):
    ev1, ev7 = _ev(session, "달빛지하차도"), _ev(session, "한빛대교")
    body = client.get(f"/api/notices/events/{ev7.id}").json()
    assert body["triggered"] is False and body["state"] == "not_triggered" and body["state_label"] == "트리거 조건 미충족"
    assert body["items"] == [] and body["disclaimer"] == DISCLAIMER and "상태(5종)를 바꾸지 않습니다" in body["note"]
    body = client.get(f"/api/notices/events/{ev1.id}").json()
    assert body["triggered"] is True and body["state"] == "no_site" and body["sites"] == [] and body["org"] == "가온시"
    assert client.get("/api/notices/events/999999").status_code == 404
    assert client.get("/api/notices/events/0").status_code == 422


def test_api_shows_stored_items(client, session):
    st = get_settings()
    ev = _ev(session, "달빛지하차도")
    service.check_event(session, ev, st, TODAY, fetcher=FixtureFetcher(PAGES, allowed_csv=st.notice_allowed_domains, extra_domains=[GAON.domain]), sites=[GAON])
    body = client.get(f"/api/notices/events/{ev.id}").json()
    assert body["state"] == "found" and body["state_label"] == "기관 발표 확인" and body["checked_at"]
    assert [i["published_at"] for i in body["items"]] == ["2026-06-30", "2026-04-02"]
    item = body["items"][0]
    assert set(item) >= {"title", "published_at", "url", "snippet", "match_score", "matched_terms"}


def test_api_run_requires_admin_token_and_checks(client, session, monkeypatch):
    st = get_settings()
    monkeypatch.setattr(st, "admin_token", "secret")
    assert client.post("/api/notices/run", json={}).status_code == 401
    monkeypatch.setattr(service, "HttpFetcher", lambda **kw: (_ for _ in ()).throw(AssertionError("요청하면 안 된다")))
    r = client.post("/api/notices/run", json={}, headers={"X-Admin-Token": "secret"})
    assert r.status_code == 200
    body = r.json()
    assert body["events"] == 8 and body["triggered"] == 2 and body["requests"] == 0 and len(body["results"]) == 8
    assert client.post("/api/notices/run", json={"event_id": 999999}, headers={"X-Admin-Token": "secret"}).status_code == 404


# ---------------------------------------------------------------------------
# 재점수 (--rescore) — 저장된 항목을 현재 규칙으로 다시 매기고 임계값 미만은 삭제, 플래그·확인 기록 정합
# ---------------------------------------------------------------------------


def _junk(ev: Event, title: str, url: str, published_at: date) -> OrgNotice:
    """옛 규칙(시설명 단독 0.6)으로 저장된 것처럼 꾸민 잡음 항목."""
    return OrgNotice(event_id=ev.id, org="가온시", site_key="gaon-press", site_url=GAON.first_page_url, title=title, url=url, published_at=published_at, snippet="", match_score=0.6, matched_terms=["지하차도"])


def test_rescore_removes_items_below_threshold_and_fixes_flag_and_check(session):
    st = get_settings()
    ev = _ev(session, "달빛지하차도")
    service.check_event(session, ev, st, TODAY, fetcher=FixtureFetcher(PAGES, allowed_csv=st.notice_allowed_domains, extra_domains=[GAON.domain]), sites=[GAON])
    session.add(_junk(ev, "지하차도 사진 공모전 수상작 전시", "https://www.gaon.go.kr/press/view?id=77", date(2026, 5, 1)))
    # 다른 사건(청솔산): 잡음만 하나 있고 플래그·확인 기록이 '찾음' 으로 남은 상태
    ev6 = _ev(session, "청솔산")
    session.add(_junk(ev6, "산 사진 공모전", "https://www.gaon.go.kr/press/view?id=78", date(2026, 5, 2)))
    ev6.org_notice_found = True
    session.add(NoticeCheck(event_id=ev6.id, triggered=True, reason="t", outcome="found", items_found=1, requests=3))
    session.commit()
    assert session.query(OrgNotice).filter_by(event_id=ev.id).count() == 3

    dry = service.rescore_all(session, st, dry_run=True)
    assert dry["dry_run"] and dry["events"] == 2 and dry["before"] == 4 and dry["after"] == 2 and dry["deleted"] == 2
    assert session.query(OrgNotice).count() == 4  # dry-run 은 지우지 않는다

    out = service.rescore_all(session, st)
    assert (out["before"], out["after"], out["deleted"]) == (4, 2, 2)
    r1 = next(r for r in out["results"] if r["event_id"] == ev.id)
    assert [e["title"] for e in r1["dropped"]] == ["지하차도 사진 공모전 수상작 전시"] and r1["dropped"][0]["after"] < st.notice_match_threshold
    rows = session.query(OrgNotice).filter_by(event_id=ev.id).order_by(OrgNotice.published_at.desc()).all()
    assert [r.title for r in rows] == ["달빛지하차도 배수펌프 증설 공사 준공", "가온시, 달빛지하차도 진입 차단시설 설치 완료"]
    assert rows[0].match_score == pytest.approx(1.0) and rows[0].matched_terms == ["달빛지하차도", "배수펌프", "+조치 어휘: 준공"]
    session.refresh(ev)
    session.refresh(ev6)
    assert ev.org_notice_found is True
    assert session.query(OrgNotice).filter_by(event_id=ev6.id).count() == 0 and ev6.org_notice_found is False
    chk6 = session.query(NoticeCheck).filter_by(event_id=ev6.id).order_by(NoticeCheck.id.desc()).first()
    assert chk6.outcome == "not_found" and chk6.items_found == 0
    chk1 = session.query(NoticeCheck).filter_by(event_id=ev.id, triggered=True).order_by(NoticeCheck.id.desc()).first()
    assert chk1.outcome == "found" and chk1.items_found == 2
    # 한 사건만 지정
    one = service.rescore_all(session, st, event_id=ev.id)
    assert one["events"] == 1 and one["before"] == 2 and one["deleted"] == 0


def test_rescore_cli_runs_without_network(session, capsys, monkeypatch):
    """scripts/run_notices.py --rescore --dry-run: 요청 없이 사건별 전후 건수를 출력한다."""
    import importlib.util
    from pathlib import Path

    st = get_settings()
    ev = _ev(session, "달빛지하차도")
    service.check_event(session, ev, st, TODAY, fetcher=FixtureFetcher(PAGES, allowed_csv=st.notice_allowed_domains, extra_domains=[GAON.domain]), sites=[GAON])
    session.add(_junk(ev, "지하차도 사진 공모전", "https://www.gaon.go.kr/press/view?id=79", date(2026, 5, 1)))
    session.commit()
    # scripts/ 패키지 import 대신 파일로 로드 (iCloud 자리표시 __init__.py 가 읽기 타임아웃을 낼 수 있다)
    spec = importlib.util.spec_from_file_location("run_notices_cli", Path(__file__).resolve().parent.parent / "scripts" / "run_notices.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "HttpFetcher", lambda **kw: (_ for _ in ()).throw(AssertionError("요청하면 안 된다")))
    monkeypatch.setattr(service, "HttpFetcher", lambda **kw: (_ for _ in ()).throw(AssertionError("요청하면 안 된다")))
    monkeypatch.setattr("sys.argv", ["run_notices.py", "--rescore", "--dry-run"])
    assert mod.main() == 0
    out = capsys.readouterr().out
    assert "dry_run=True" in out and "before=3 after=2 deleted=1" in out and "삭제 2026-05-01" in out
    assert session.query(OrgNotice).filter_by(event_id=ev.id).count() == 3  # dry-run
    monkeypatch.setattr("sys.argv", ["run_notices.py", "--rescore", str(ev.id)])
    assert mod.main() == 0
    session.expire_all()
    assert session.query(OrgNotice).filter_by(event_id=ev.id).count() == 2
