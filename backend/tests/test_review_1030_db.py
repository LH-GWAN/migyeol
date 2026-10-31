"""2026-10-30 버그 목록 A 항목 — 실험 DB 사본으로 확인하는 회귀 테스트.

실험 DB(backend/data/migyeol.db)가 없으면 건너뛴다. DB 는 tmp 로 복사해 쓰고 원본은 건드리지 않는다.
검사: 금지어 0건(사건 100건 × 초안 2형식 + 전체 초안 + 상세·근거 API + 대시보드), 데모 사건 160 발생일, 177 게시판 문구,
사례 선정(177 제외·219), 근거 화면 정렬(164), 발췌 앞 잘림(288), 대표 사례 상태 일치(250), 주체 실명 없음.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
EXP_DB = BACKEND / "data" / "migyeol.db"
FORBIDDEN = ("미" + "이행", "불" + "이행", "거짓" + "말")
PERSON_TITLE = ("지사", "장관", "시장", "군수", "구청장", "부시", "부군", "부구", "의원", "대변인", "본부장", "실장", "과장", "국장", "총리", "차관", "팀장", "위원장")


@pytest.fixture(scope="module")
def exp_client(tmp_path_factory):
    if not EXP_DB.is_file():
        pytest.skip("실험 DB 없음")
    tmp = tmp_path_factory.mktemp("expdb") / "exp.db"
    src = sqlite3.connect(str(EXP_DB))
    dst = sqlite3.connect(str(tmp))
    src.backup(dst)
    src.close()
    dst.close()
    from app.config import get_settings
    from app.db.session import init_db, reset_engine

    url = f"sqlite:///{tmp}"
    saved = {k: os.environ.get(k) for k in ("DATABASE_URL", "LLM_MODE", "BIGKINDS_MODE", "TODAY_OVERRIDE", "SEEDS_FILE", "GOLD_FILE")}
    os.environ.update({"DATABASE_URL": url, "LLM_MODE": "rules", "BIGKINDS_MODE": "record", "TODAY_OVERRIDE": "2026-10-22", "SEEDS_FILE": "", "GOLD_FILE": ""})
    for k in ("FOLLOWUP_EXPRESSION_FIXES", "FOLLOWUP_HEADLINE_EXPRESSIONS", "FOLLOWUP_EVENT_PATH", "FOLLOWUP_PHRASE_FIXES", "FOLLOWUP_CUTOFF", "FOLLOWUP_MIN_LINK_PROB", "LINK_RERANKER", "FOLLOWUP_EARLY_PROBLEM_DAYS"):
        saved[k] = os.environ.pop(k, None)  # 실험 기본값(config.py)으로
    get_settings.cache_clear()
    reset_engine()
    init_db(url)
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as tc:
        yield tc
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    get_settings.cache_clear()
    reset_engine()


def _hits(obj) -> list[str]:
    s = json.dumps(obj, ensure_ascii=False)
    return [w for w in FORBIDDEN if w in s]


def _event_ids(tc) -> list[int]:
    ids: set[int] = set()
    for page in (1, 2):
        ids |= {c["id"] for c in tc.get(f"/api/events?size=100&page={page}").json()["items"]}  # needs_check 필터 없이 전체
    return sorted(ids)


# ----------------------------------------------------------------------------- A1 금지어
def test_no_forbidden_words_in_any_public_output(exp_client):
    tc = exp_client
    ids = _event_ids(tc)
    assert len(ids) >= 90
    bad = {}
    for i in ids:
        for path in (f"/api/events/{i}", f"/api/events/{i}/evidence", f"/api/press/draft?event_id={i}", f"/api/press/draft?event_id={i}&format=article"):
            r = tc.get(path)
            assert r.status_code == 200, path
            if _hits(r.json()):
                bad[path] = _hits(r.json())
    for path in ("/api/press/draft", "/api/press/draft?format=article", "/api/dashboard/recent-followups?limit=50", "/api/dashboard/notice-cases?per_kind=3", "/api/events?size=100", "/api/events?size=100&page=2"):
        if _hits(tc.get(path).json()):
            bad[path] = _hits(tc.get(path).json())
    assert bad == {}


def test_markdown_download_is_scrubbed(exp_client):
    md = exp_client.get("/api/press/draft?event_id=153&format=article").json()["markdown"]
    assert not any(w in md for w in FORBIDDEN)
    assert "○" in md  # 금지어가 든 기사 제목이 가려진 채 들어간다


# ----------------------------------------------------------------------------- A3 160 발생일
def test_demo_event_160_occurred_on_2022_12_30(exp_client):
    card = exp_client.get("/api/events/160").json()["card"]
    assert card["occurred_at"] == "2022-12-30"
    draft = exp_client.get("/api/press/draft?event_id=160").json()
    assert "2022년 12월 30일" in draft["sections"][0]["body"] and "2022년 7월 1일" not in draft["markdown"]


# ----------------------------------------------------------------------------- A2 게시판 문구·사례
def test_completed_event_177_is_not_a_news_gap_case(exp_client):
    n = exp_client.get("/api/notices/events/177").json()
    assert n["news_gap"] is False  # 177 의 정책브리핑 글 4건은 모두 다른 부처 글이라 B7(발표 주체 일치)로 지워졌다 — 남은 글이 없어도 news_gap 은 아니다
    cases = exp_client.get("/api/dashboard/notice-cases?per_kind=5").json()["items"]
    assert all(c["event_id"] != 177 for c in cases)
    assert all(c["status"] in ("후속보도 부족", "판단 불가") for c in cases if c["kind"] == "notice_without_followup")
    draft = exp_client.get("/api/press/draft?event_id=177").json()
    assert "뉴스에는 확인되지 않았지만" not in draft["markdown"]


def test_same_day_announcements_are_not_news_gaps(exp_client):
    for eid in (160, 262):
        n = exp_client.get(f"/api/notices/events/{eid}").json()
        assert n["news_gap"] is False, eid


def test_cheongna_219_is_the_news_gap_example(exp_client):
    n = exp_client.get("/api/notices/events/219").json()
    assert n["news_gap"] is True and n["news_gap_items"] >= 1
    cases = exp_client.get("/api/dashboard/notice-cases?per_kind=5").json()["items"]
    assert any(c["event_id"] == 219 and c["kind"] == "notice_without_followup" for c in cases)


def test_press_notice_summary_counts_only_triggered_events(exp_client):
    body = exp_client.get("/api/press/draft").json()["sections"][4]["body"]
    import re

    m = re.search(r"지난 사건 (\d+)건은", body)
    parts = [int(x) for x in re.findall(r"사건 (\d+)건", body.split("결과는", 1)[1])] if "결과는" in body else []
    assert m and parts and sum(parts) == int(m.group(1))


# ----------------------------------------------------------------------------- A4 완료 오독
@pytest.mark.parametrize("eid", [177, 272, 281, 269, 158, 18, 193])
def test_misread_completion_events_are_no_longer_done_by_misread_only(exp_client, eid):
    ev = exp_client.get(f"/api/events/{eid}/evidence").json()
    counted = [s for s in ev["signals"] if s["counted"] and s["signal"] == "완료"]
    for s in counted:
        span = s["evidence_span"]
        assert not any(k in span for k in ("끝나지 않았다", "끝내지 않았다", "완료를 당부", "드러나면", "효과가 없었다", "완공을 촉구", "끝내야", "완료돼야", "마무리돼도", "되는 시점", "끝나기 전까지", "하반기 설치 완료")), (eid, span)


# ----------------------------------------------------------------------------- A5 발췌 어절 경계
def test_288_span_keeps_unregistered_contractor_wording(exp_client):
    ev = exp_client.get("/api/events/288/evidence").json()
    spans = [s["evidence_span"] for s in ev["signals"] if "등록업체" in s["evidence_span"]]
    assert spans and all(sp.startswith("…") or "무등록업체" in sp for sp in spans)
    assert not any(sp.startswith("등록업체") for sp in spans)


# ----------------------------------------------------------------------------- A6 대표 사례는 사건 상태
def test_press_cases_describe_event_status(exp_client):
    import re

    d = exp_client.get("/api/press/draft").json()
    body = d["sections"][2]["body"]
    cards = {c["id"]: c for c in (exp_client.get("/api/events?size=100").json()["items"] + exp_client.get("/api/events?size=100&page=2").json()["items"])}
    by_title = {c["title"]: c["status"] for c in cards.values()}
    label = {"조치 완료 근거 확인": "조치 완료", "조치 진행 정황 확인": "조치 진행", "새로운 문제 발생": "새로운 문제"}
    for line in body.split("\n"):
        m = re.match(r"\d\. (.+?): ", line)
        if m and m.group(1) in by_title:
            assert f"({label[by_title[m.group(1)]]})" in line, line


# ----------------------------------------------------------------------------- A7 주체 기관
def test_no_person_name_or_bogus_org_as_actor(exp_client):
    import re

    ids = _event_ids(tc := exp_client)
    bad = []
    for i in ids:
        d = tc.get(f"/api/events/{i}").json()
        for p in d["promises"]:
            a = p["actor_org"]
            if a in ("부시", "부군", "부구", "행정부시") or re.fullmatch(r"[가-힣]{1,3}\s+(지사|장관|군수|시장|부지사|과장|국장|실장|본부장|대변인|팀장|차관|총리|위원장)", a):
                bad.append((i, a))
    assert bad == []
    d = exp_client.get("/api/events/177").json()
    assert all("김진열" not in p["actor_org"] for p in d["promises"])


def test_press_subjects_are_orgs_not_named_officials(exp_client):
    md = exp_client.get("/api/press/draft?event_id=16&format=article").json()["markdown"]
    assert "부시장은" not in md and "부시는" not in md
    for eid in (160, 262, 219):
        d = exp_client.get(f"/api/press/draft?event_id={eid}").json()
        lines = d["sections"][1]["body"].split("\n")
        for line in lines:
            if line.startswith("- "):
                who = line.split(":")[0].split()[-1] if ":" in line else ""
                assert not (len(who) in (2, 3) and who.endswith(PERSON_TITLE) and " " in line.split(":")[0]), line


# ----------------------------------------------------------------------------- A8·A9·A11
def test_dashboard_summary_has_casualty_denominator(exp_client):
    s = exp_client.get("/api/dashboard/summary").json()["casualties"]
    assert {"dead", "injured", "events", "events_total"} <= set(s) and s["events_total"] >= s["events"]


def test_evidence_signals_counted_first(exp_client):
    sig = exp_client.get("/api/events/164/evidence").json()["signals"]
    counted = [s["counted"] for s in sig]
    first_uncounted = counted.index(False) if False in counted else len(counted)
    assert all(counted[:first_uncounted]) and not any(counted[first_uncounted:])


def test_pending_quotations_are_resolved_in_rules_mode(exp_client):
    ids = _event_ids(exp_client)
    cards = [exp_client.get(f"/api/events/{i}").json()["card"] for i in ids]
    assert sum(c["pending_quotations"] for c in cards) == 0
    assert sum(1 for c in cards if c["needs_review"]) < len(cards)  # '검토 필요' 가 거의 모든 사건에 붙지 않는다
    d = exp_client.get("/api/events/160").json()
    assert "호출 상한" not in (d["status_reason_text"] or "")


# ----------------------------------------------------------------------------- 10-31 2차: R1·R2·R3·R5 DB 확인
def test_no_glued_names_or_fragment_orgs_as_actor(exp_client):
    import re

    bad = []
    for i in _event_ids(exp_client):
        for p in exp_client.get(f"/api/events/{i}").json()["promises"]:
            a = p["actor_org"]
            if a in ("당시", "경시", "군위부", "부부", "부시", "박시", "김군", "당국", "대처", "부처", "구청", "박종철부산시", "윤호중행정안전부") or re.fullmatch(r"[가-힣]{3}(부산시|서울시|인천시|행정안전부|국토교통부)", a):
                bad.append((i, a))
    assert bad == []


def test_public_sources_are_flagged_public_in_db(exp_client):
    """R2: '국방부 대변인'·'소방본부장'·'금융위원장'·'식량원예 과장'·'항공 대장' 같은 공공 주체가 민간으로 저장돼 있지 않다."""
    import sqlite3

    from app.config import get_settings

    url = get_settings().database_url
    con = sqlite3.connect(url.replace("sqlite:///", ""))
    rows = con.execute("select source, is_public_actor from quotations").fetchall()
    con.close()
    priv = {s for s, f in rows if not f}
    for key in ("국방부 대변인", "소방본부장", "금융위원장", "식량원예 과장", "항공 대장"):
        hits = [s for s in priv if key in s]
        assert hits == [], (key, hits[:5])


def test_unknown_org_promises_are_not_duplicated_tenfold(exp_client):
    """R3: 기관 미상('') 약속이 같은 문구로 여러 개 남아 있지 않다 (275 ×10)."""
    from collections import Counter

    worst = []
    for i in _event_ids(exp_client):
        c = Counter((p["action"] or "")[:40] for p in exp_client.get(f"/api/events/{i}").json()["promises"] if not p["actor_org"])
        for k, n in c.items():
            if n >= 3:
                worst.append((i, k, n))
    assert worst == []


def test_175_late_completion_link_is_counted(exp_client):
    """R5: 바닥 규칙으로 남은 늦은 연결(p 0.109)이 완료 근거로 쓰인다 — 최소 확률 0.3 이 뺐던 것."""
    ev = exp_client.get("/api/events/175/evidence").json()
    assert any(s["counted"] and s["signal"] == "완료" for s in ev["signals"]), [s["signal"] for s in ev["signals"] if s["counted"]]


def test_links_in_result_phase_have_valid_signals(exp_client):
    """R5: 유효 신호가 없는 기사가 '결과확인'·'조치진행' 칸에 남아 있지 않다 (헌혈 캠페인). tmp 사본에서 재판정해 확인."""
    from datetime import date

    from app.config import get_settings
    from app.db.models import Event
    from app.db.session import session_factory
    from app.pipeline.judge import rejudge_event

    settings = get_settings()
    session = session_factory()()
    stale = []
    try:
        for i in _event_ids(exp_client):
            ev = session.get(Event, i)
            before = {l.news_id: l.phase for l in ev.links}
            j = rejudge_event(session, ev, settings, date(2026, 10, 22))
            valid = {s.news_id for s in ev.signals if s.id in set(j.valid_signal_ids)}
            for l in ev.links:
                if l.is_active and l.phase in ("결과확인", "조치진행") and l.news_id not in valid:
                    stale.append((i, l.news_id, l.phase))
                assert l.phase == before[l.news_id], (i, l.news_id, before[l.news_id], l.phase)  # 저장된 DB 가 이미 재판정 결과와 같다
    finally:
        session.close()
    assert stale == []
