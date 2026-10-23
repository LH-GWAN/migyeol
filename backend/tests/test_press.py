"""v2 §6 화면 4·§7.2 보도자료 초안 — 가상 픽스처 DB(기준일 2026-09-11)로 전체·사건별 초안을 고정한다 (트랙 C).

- 숫자는 판정·목록 API 와 같은 기준이어야 한다(후속보도 확인 = 현재 약속 집합의 유효 신호 ≥1).
- 모든 사례 문장은 근거 기사 번호 [n] 이 있고 그 번호는 sources 에 있다.
- 금지어·비난 표현 없음, 면책 문구 고정, 실험 리포트가 있으면 그 숫자를 쓴다.
"""

from __future__ import annotations

import copy
import json
import os
import re
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import DISCLAIMER, STATUS_DONE, STATUS_IN_PROGRESS, STATUS_NEW_PROBLEM, get_settings
from app.db.models import Event
from app.db.session import init_db, reset_engine, session_factory
from app.notices.models import NoticeCheck, OrgNotice
from app.press import data as D
from app.press.writer import CONTACT, HEADER_NOTE, T, LLMWriter, TemplateWriter, build_writer, has_batchim, josa, polish_is_safe

FORBIDDEN = ("미" + "이행", "불" + "이행", "거짓" + "말")
BLAME = ("책임을 회피", "방치", "외면", "무책임", "뒷짐", "나몰라라", "약속을 어겼")
TODAY = date(2026, 9, 11)

EXPERIMENT = {
    "generated_at": "2026-10-25T21:00:00+09:00", "as_of": "2026-10-25", "bigkinds_mode": "record",
    "seeds_file": "seeds/events.yaml", "embedding_backend": "st", "llm_mode": "rules",
    "source_articles": {"collected": 412, "unique": 103},
    "events": {"total": 61, "with_followup": 23, "followup_rate": 0.377, "by_status": {"조치 완료 근거 확인": 9, "조치 진행 정황 확인": 11, "새로운 문제 발생": 3, "후속보도 부족": 30, "판단 불가": 8},
               "with_org_notice": 4, "articles_per_event": {"median": 7, "max": 80}},
    "windows_days": {"30": 2, "90": 8, "180": 15, "365": 21},
    "signals": {"완료": 12, "진행": 20, "새로운문제": 4},
    "by_incident_type": [{"incident_type": "재해>자연재해", "events": 30, "with_followup": 12, "rate": 0.4}],
    "by_org": [{"org": "서울시", "events": 6, "with_followup": 4, "rate": 0.667, "latest_followup_at": "2025-08-01", "event_ids": [1]},
               {"org": "부산시", "events": 3, "with_followup": 0, "rate": 0.0, "latest_followup_at": None, "event_ids": [2]}],
    "timeseries": [], "daily": [],
    "labels": {"labeled": 0, "followup_precision": None, "followup_recall": None, "cluster_precision": None, "org_accuracy": None},
    "cost": {"api_calls": {"search_news": 0, "search_quotation": 0, "issue_ranking": 0, "time_line": 0, "word_cloud": 0}, "elapsed_sec": 0, "recordings": 0},
    "success": {"min_followup_events": 5, "target_followup_events": 20, "met_min": True, "met_target": True},
}


@pytest.fixture(scope="module")
def press_db(tmp_path_factory, fixtures_ready):
    from app.bigkinds import build_client
    from app.llm.client import build_llm
    from app.pipeline.embeddings import build_embedding
    from app.pipeline.runner import Pipeline

    url = f"sqlite:///{tmp_path_factory.mktemp('press') / 'press.db'}"
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
def session(press_db, tmp_path, monkeypatch):
    get_settings.cache_clear()
    os.environ["DATABASE_URL"] = press_db
    reset_engine()
    init_db(press_db)
    monkeypatch.setattr(get_settings(), "experiment_latest_path", tmp_path / "experiment_latest.json")
    s = session_factory()()
    s.query(OrgNotice).delete()
    s.query(NoticeCheck).delete()
    s.commit()
    yield s
    s.close()


@pytest.fixture
def client(session):
    from app.main import app

    with TestClient(app) as c:
        yield c


def _refs(text: str) -> set[int]:
    return {int(n) for n in re.findall(r"\[(\d+)\]", text)}


def _assert_clean(draft: dict) -> None:
    text = draft["markdown"] + json.dumps(draft["sections"], ensure_ascii=False)
    assert not any(w in text for w in FORBIDDEN)
    assert not any(w in text for w in BLAME)
    assert DISCLAIMER in draft["markdown"] and draft["disclaimer"] == DISCLAIMER
    assert HEADER_NOTE in draft["markdown"]
    refs = _refs("\n".join(s["body"] for s in draft["sections"] if s["heading"] != "근거 기사"))
    src = {s["ref"] for s in draft["sources"]}
    assert refs == src, (refs, src)  # 인용한 번호 = 근거 목록 (빠진 것도 남는 것도 없음)
    assert [s["ref"] for s in draft["sources"]] == list(range(1, len(draft["sources"]) + 1))
    for s in draft["sources"]:
        assert s["news_id"] and s["title"] and set(s) >= {"news_id", "title", "link"}


# ---------------------------------------------------------------------------
# 전체 초안
# ---------------------------------------------------------------------------


def test_overall_draft_numbers_match_judgments(client, session):
    body = client.get("/api/press/draft").json()
    assert body["scope"] == "all" and body["event_id"] is None and body["data_source"] == "db"
    _assert_clean(body)
    events = session.query(Event).filter(Event.active.is_(True)).all()
    st = get_settings()
    with_fu = [e for e in events if D.valid_signals(session, e, st)]
    # 가상 픽스처: 완료·진행·새로운문제 상태 사건은 모두 유효 신호가 있고, 후속보도 부족 사건은 없다
    assert {e.status for e in with_fu} <= {STATUS_DONE, STATUS_IN_PROGRESS, STATUS_NEW_PROBLEM}
    assert all(e in with_fu for e in events if e.status in (STATUS_DONE, STATUS_IN_PROGRESS))
    stats = body["stats"]
    assert stats["events"] == len(events) == 8 and stats["with_followup"] == len(with_fu)
    assert f"사건 {len(events)}건" in body["sections"][0]["body"] and f"{len(with_fu)}건(" in body["sections"][0]["body"]
    assert body["title"] == T["title_all"].substitute(events=8, followup=len(with_fu))
    headings = [s["heading"] for s in body["sections"]]
    assert headings == ["요약", "재난 유형별 현황", "후속보도가 확인된 기관과 사례", "후속보도가 확인되지 않은 사건", "기관 공식 게시판 확인 결과", "분석 방법", "유의 사항", "문의", "근거 기사"]
    assert CONTACT in body["markdown"]
    assert body["markdown"].startswith(f"# {body['title']}\n")


def test_overall_draft_lists_followup_orgs_and_three_cases(client):
    body = client.get("/api/press/draft").json()
    sec = next(s["body"] for s in body["sections"] if s["heading"] == "후속보도가 확인된 기관과 사례")
    assert sec.startswith("후속보도가 확인된 기관은 ")
    assert "가온시(" in sec and "누리군(" in sec
    cases = re.findall(r"^\d\. ", sec, re.M)
    assert len(cases) == 3
    for line in sec.splitlines():
        if re.match(r"^\d\. ", line):
            assert "확인됐다" in line and _refs(line)  # 사례 문장마다 근거 기사
    gap = next(s["body"] for s in body["sections"] if s["heading"] == "후속보도가 확인되지 않은 사건")
    assert "조치가 없었다는 뜻은 아니다" in gap and "확인되지 않았다" in gap
    assert "누리군 청솔산 산사태" in gap or "가온시 달빛지하차도 침수 사고" in gap


def test_overall_draft_reports_notice_results(client, session):
    st = get_settings()
    body = client.get("/api/press/draft").json()
    sec = next(s["body"] for s in body["sections"] if s["heading"] == "기관 공식 게시판 확인 결과")
    assert f"{st.notice_trigger_days}일 이상 지난 사건 2건" in sec and "등록된 게시판이 없는 사건 2건" in sec
    ev = next(e for e in session.query(Event).all() if "달빛지하차도" in e.title)
    session.add(OrgNotice(event_id=ev.id, org="가온시", site_key="x", site_url="https://www.gaon.go.kr/p", title="달빛지하차도 배수펌프 증설 준공", published_at=date(2026, 6, 30), url="https://www.gaon.go.kr/p/1", match_score=0.9, matched_terms=["달빛지하차도", "배수펌프"]))
    session.commit()
    body = client.get("/api/press/draft").json()
    sec = next(s["body"] for s in body["sections"] if s["heading"] == "기관 공식 게시판 확인 결과")
    assert "기관 발표가 확인된 사건 1건" in sec and "달빛지하차도 배수펌프 증설 준공" in sec and "https://www.gaon.go.kr/p/1" in sec


def test_overall_draft_uses_experiment_report_when_present(client):
    get_settings().experiment_latest_path.write_text(json.dumps(EXPERIMENT, ensure_ascii=False), encoding="utf-8")
    body = client.get("/api/press/draft").json()
    _assert_clean(body)
    assert body["data_source"] == "experiment" and body["experiment_generated_at"] == EXPERIMENT["generated_at"]
    lead = body["sections"][0]["body"]
    assert "원천 기사 103건에서 사건 61건" in lead and "23건(37.7%)" in lead and "412건" in lead and "2026년 10월 25일 기준" in lead
    inc = next(s["body"] for s in body["sections"] if s["heading"] == "재난 유형별 현황")
    assert "재해>자연재해: 사건 30건, 후속보도 확인 12건(40.0%)" in inc
    orgs = next(s["body"] for s in body["sections"] if s["heading"] == "후속보도가 확인된 기관과 사례")
    assert "서울시(사건 6건 중 4건)" in orgs and "부산시" not in orgs.split("\n")[0]  # 후속보도 0건 기관은 명단에 없다


def test_overall_draft_survives_broken_experiment_file(client):
    get_settings().experiment_latest_path.write_text("{not json", encoding="utf-8")
    body = client.get("/api/press/draft").json()
    assert body["data_source"] == "db" and body["stats"]["events"] == 8


def test_broken_experiment_with_missing_keys_falls_back(client):
    bad = copy.deepcopy(EXPERIMENT)
    bad.pop("events")
    get_settings().experiment_latest_path.write_text(json.dumps(bad), encoding="utf-8")
    assert client.get("/api/press/draft").json()["data_source"] == "db"


# ---------------------------------------------------------------------------
# 사건별 초안
# ---------------------------------------------------------------------------


def test_event_draft_with_followup(client, session):
    ev = next(e for e in session.query(Event).all() if e.status == STATUS_DONE)
    body = client.get(f"/api/press/draft?event_id={ev.id}").json()
    _assert_clean(body)
    assert body["scope"] == "event" and body["event_id"] == ev.id and body["title"] == f"[사건 브리프] {ev.title} — {STATUS_DONE}"
    headings = [s["heading"] for s in body["sections"]]
    assert headings == ["사건 요약", "발표된 약속", "후속보도", "기관 발표 확인", "현재 상태", "문의", "근거 기사"]
    fu = next(s["body"] for s in body["sections"] if s["heading"] == "후속보도")
    assert "후속보도" in fu and "확인됐다" in fu and _refs(fu)
    assert body["stats"]["followup_signals"] >= 1


def test_event_draft_gap_and_notice_state(client, session):
    ev = next(e for e in session.query(Event).all() if "달빛지하차도" in e.title)
    body = client.get(f"/api/press/draft?event_id={ev.id}").json()
    _assert_clean(body)
    fu = next(s["body"] for s in body["sections"] if s["heading"] == "후속보도")
    assert "확인되지 않았다" in fu and DISCLAIMER in fu
    notice = next(s["body"] for s in body["sections"] if s["heading"] == "기관 발표 확인")
    assert "등록된 게시판 없음" in notice
    promises = next(s["body"] for s in body["sections"] if s["heading"] == "발표된 약속")
    assert "배수펌프" in promises and _refs(promises)


def test_event_draft_404_and_validation(client):
    assert client.get("/api/press/draft?event_id=999999").status_code == 404
    assert client.get("/api/press/draft?event_id=0").status_code == 422


def test_press_event_list(client):
    rows = client.get("/api/press/events").json()
    assert len(rows) == 8 and set(rows[0]) >= {"id", "title", "status", "org", "occurred_at", "has_followup", "notice_found"}
    assert sum(r["has_followup"] for r in rows) == client.get("/api/press/draft").json()["stats"]["with_followup"]


# ---------------------------------------------------------------------------
# 템플릿·작성기
# ---------------------------------------------------------------------------


def test_templates_have_no_forbidden_or_blame_words():
    for t in T.values():
        assert not any(w in t.template for w in FORBIDDEN + BLAME)


def test_josa_helper():
    assert josa("가온시", "은", "는") == "가온시는" and josa("가온소방서", "은", "는") == "가온소방서는"
    assert josa("행정안전부", "은", "는") == "행정안전부는" and josa("누리군", "은", "는") == "누리군은"
    assert has_batchim("3") and not has_batchim("2")


def test_build_writer_uses_template_without_live_llm():
    assert isinstance(build_writer(get_settings()), TemplateWriter)


def test_polish_guard():
    before = "사건 8건 중 5건(62.5%)이 확인됐다 [1]."
    assert polish_is_safe(before, "사건 8건 가운데 5건(62.5%)에서 확인됐다 [1].")
    assert not polish_is_safe(before, "사건 8건 중 6건(62.5%)이 확인됐다 [1].")  # 숫자 변경
    assert not polish_is_safe(before, "사건 8건 중 5건(62.5%)이 확인됐다.")  # 근거 번호 누락
    assert not polish_is_safe(before, "사건 8건 중 5건(62.5%)은 " + "미" + "이행 [1].")
    assert not polish_is_safe(before, "")


def test_llm_writer_falls_back_to_template_sentences(session):
    """LLM_MODE=live 경로: 다듬은 문장이 검증에 실패하거나 호출이 실패하면 템플릿 문장 그대로."""
    st = get_settings()

    class FakeMessages:
        def __init__(self, reply):
            self.reply = reply

        def create(self, **kw):
            assert kw["system"][0]["text"].startswith("너는 뉴스 애프터케어")
            if isinstance(self.reply, Exception):
                raise self.reply
            return SimpleNamespace(content=[SimpleNamespace(text=self.reply(kw["messages"][0]["content"]))])

    ov = D.overview(session, st, TODAY)
    base = TemplateWriter(st).write_all(ov)
    w = LLMWriter(st, client=SimpleNamespace(messages=FakeMessages(lambda text: "숫자 바꿈 999건")))
    out = w.write_all(ov)
    assert [s["body"] for s in out.sections] == [s["body"] for s in base.sections] and out.writer.startswith("llm-press-v1")
    w2 = LLMWriter(st, client=SimpleNamespace(messages=FakeMessages(RuntimeError("down"))))
    assert [s["body"] for s in w2.write_all(ov).sections] == [s["body"] for s in base.sections]
    w3 = LLMWriter(st, client=SimpleNamespace(messages=FakeMessages(lambda text: text.replace("밝혔다", "발표했다"))))
    lead = w3.write_all(ov).sections[0]["body"]
    assert "발표했다" in lead  # 검증을 통과한 다듬기는 채택
