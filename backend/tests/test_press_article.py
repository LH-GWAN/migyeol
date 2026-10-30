"""보도자료 「기사체」(format=article) — 가상 픽스처 DB(기준일 2026-09-11)로 전체·사건별 기사체 초안을 고정한다.

- (a) 금지 표현·비난 표현이 없고 면책 문구·근거 번호 규칙은 브리프와 같다.
- (b) 숫자·근거 번호는 브리프와 같은 data.py 집계에서만 온다: 기사체의 숫자 ⊆ 브리프의 숫자 ∪ stats ∪ 설정 상수, sources 는 번호·기사 모두 동일.
- (c) 전체(scope=all)·사건별(scope=event) 모두 생성되고 구조는 제목→부제→리드→본문→배경→마무리→근거 기사.
- LLM_MODE=live 경로(LLMWriter)는 기사체에도 같은 polish_is_safe 검사를 적용한다.
"""

from __future__ import annotations

import json
import re
from datetime import date
from types import SimpleNamespace

import pytest

from app.config import DISCLAIMER, STATUS_DONE, get_settings
from app.db.models import Event
from app.notices.models import OrgNotice
from app.press import data as D
from app.press.writer import (
    ARTICLE_BACKGROUND,
    ARTICLE_BODY,
    ARTICLE_CLOSING,
    ARTICLE_LEAD,
    CONTACT,
    HEADER_NOTE,
    SOURCES_HEADING,
    A,
    ArticleWriter,
    LLMWriter,
    TemplateWriter,
    build_writer,
)
from tests.test_press import BLAME, EXPERIMENT, FORBIDDEN, TODAY, _refs, client, press_db, session  # noqa: F401 — 픽스처 재사용

_NUM = re.compile(r"\d+(?:[.,]\d+)*")
ARTICLE_HEADINGS = [ARTICLE_LEAD, ARTICLE_BODY, ARTICLE_BACKGROUND, ARTICLE_CLOSING, SOURCES_HEADING]


def _text(draft: dict) -> str:
    return "\n".join(s["body"] for s in draft["sections"] if s["heading"] != SOURCES_HEADING)


def _assert_article_clean(draft: dict) -> None:
    text = draft["markdown"] + json.dumps(draft["sections"], ensure_ascii=False) + draft["title"] + (draft["subtitle"] or "")
    assert not any(w in text for w in FORBIDDEN)
    assert not any(w in text for w in BLAME)
    assert draft["format"] == "article" and draft["subtitle"]
    assert DISCLAIMER in draft["markdown"] and HEADER_NOTE in draft["markdown"] and CONTACT in draft["markdown"]
    assert [s["heading"] for s in draft["sections"]] == ARTICLE_HEADINGS
    # 글머리표·표·번호 목록 없이 문단만
    for s in draft["sections"][:-1]:
        for line in s["body"].splitlines():
            assert not re.match(r"^\s*([-*|]|\d+\.)\s", line), line
    assert "확인됐다" in _text(draft) or "확인되지 않았다" in _text(draft)
    refs = _refs(_text(draft))
    assert refs == {s["ref"] for s in draft["sources"]}
    assert [s["ref"] for s in draft["sources"]] == list(range(1, len(draft["sources"]) + 1))
    # 기사체 markdown: 제목·굵은 부제·머리말, 절 제목은 「근거 기사」만
    md = draft["markdown"]
    assert md.startswith(f"# {draft['title']}\n\n**{draft['subtitle']}**\n\n> {HEADER_NOTE}\n")
    assert md.count("\n## ") == 1 and f"## {SOURCES_HEADING}" in md


def _assert_same_numbers(article: dict, brief: dict) -> None:
    """기사체가 쓰는 숫자는 브리프 본문·stats·설정 상수에 있는 것뿐이고, 근거 기사 목록은 번호까지 같다."""
    st = get_settings()
    allowed = set(_NUM.findall("\n".join(s["body"] for s in brief["sections"]) + brief["title"]))
    allowed |= {str(v) for v in brief["stats"].values()}
    allowed |= {n.lstrip("0") or "0" for n in re.findall(r"\d+", " ".join(str(v) for v in brief["stats"].values()))}  # as_of 2026-09-11 → 2026, 9, 11
    allowed |= {str(st.signal_min_conf), str(st.notice_trigger_days)}
    used = set(_NUM.findall(_text(article) + article["title"] + article["subtitle"]))
    assert used <= allowed, used - allowed
    assert article["stats"] == brief["stats"]
    assert [(s["ref"], s["news_id"]) for s in article["sources"]] == [(s["ref"], s["news_id"]) for s in brief["sources"]]


# ---------------------------------------------------------------------------
# (c) 전체·사건별 생성 + (a)(b)
# ---------------------------------------------------------------------------


def test_article_overall_draft(client):
    brief = client.get("/api/press/draft").json()
    body = client.get("/api/press/draft?format=article").json()
    assert body["scope"] == "all" and body["event_id"] is None and body["writer"] == "article-v1" and brief["format"] == "brief"
    _assert_article_clean(body)
    _assert_same_numbers(body, brief)
    st = brief["stats"]
    assert body["title"] == A["title_all"].substitute(events=st["events"], followup=st["with_followup"])
    lead = body["sections"][0]["body"]
    assert f"사건 {st['events']}건을 추적한 결과" in lead and f"{st['with_followup']}건(" in lead and "원천 기사" in lead
    text = body["sections"][1]["body"]
    assert text.startswith("재난 유형별로는 ") and "대표적인 사례가 ‘" in text and "조치가 없었다는 뜻은 아니다" in text
    assert text.count("\n\n") >= 5  # 유형·기관·사례 3·공백·게시판 문단
    assert f"{get_settings().notice_trigger_days}일 이상 지난 사건 2건" in text and "등록된 게시판이 없는 사건 2건" in text
    assert body["sections"][2]["body"].startswith("분석에는 빅카인즈 OpenAPI")
    assert body["sections"][3]["body"].startswith("이번 분석의 기준일은 ") and DISCLAIMER in body["sections"][3]["body"]


def test_article_overall_uses_experiment_numbers(client):
    get_settings().experiment_latest_path.write_text(json.dumps(EXPERIMENT, ensure_ascii=False), encoding="utf-8")
    brief = client.get("/api/press/draft").json()
    body = client.get("/api/press/draft?format=article").json()
    _assert_article_clean(body)
    _assert_same_numbers(body, brief)
    assert body["data_source"] == "experiment" and body["title"] == "재난·안전 사건 61건 중 23건, 기관 발표 뒤 후속보도 확인됐다"
    assert "38건은 2026년 10월 25일 기준" in body["subtitle"]
    lead = body["sections"][0]["body"]
    assert "사건 61건을 추적한 결과" in lead and "23건(37.7%)" in lead and "원천 기사 412건" in lead and "1559" not in lead and "103건" not in lead and "사건 61건으로 정리" in lead
    text = body["sections"][1]["body"]
    assert "‘재해>자연재해’ 사건 30건 가운데 12건(40.0%)" in text and "서울시(사건 6건 중 4건)" in text and "부산시" not in text


def test_article_overall_reports_notice_with_date_and_title(client, session):
    ev = next(e for e in session.query(Event).all() if "달빛지하차도" in e.title)
    session.add(OrgNotice(event_id=ev.id, org="가온시", site_key="x", site_url="https://www.gaon.go.kr/p", title="달빛지하차도 배수펌프 증설 준공", published_at=date(2026, 6, 30), url="https://www.gaon.go.kr/p/1", match_score=0.9, matched_terms=["달빛지하차도"]))
    session.commit()
    body = client.get("/api/press/draft?format=article").json()
    text = body["sections"][1]["body"]
    assert "기관 발표가 확인된 사건 1건" in text and "2026-06-30 「달빛지하차도 배수펌프 증설 준공」 제목의 발표가 올라와 있는 것이 확인됐다" in text
    ev_body = client.get(f"/api/press/draft?event_id={ev.id}&format=article").json()
    _assert_article_clean(ev_body)
    assert "뉴스 후속보도는 확인되지 않았지만 기관 게시판 발표 1건 확인" in ev_body["title"]
    assert "「달빛지하차도 배수펌프 증설 준공」" in ev_body["sections"][1]["body"] and "https://www.gaon.go.kr/p/1" not in ev_body["sections"][1]["body"]


def test_article_event_draft_with_followup(client, session):
    ev = next(e for e in session.query(Event).all() if e.status == STATUS_DONE)
    brief = client.get(f"/api/press/draft?event_id={ev.id}").json()
    body = client.get(f"/api/press/draft?event_id={ev.id}&format=article").json()
    assert body["scope"] == "event" and body["event_id"] == ev.id
    _assert_article_clean(body)
    _assert_same_numbers(body, brief)
    n = brief["stats"]["followup_signals"]
    assert body["title"] == f"‘{ev.title}’, 기관 발표 이후 후속보도 {n}건 확인"
    assert f"미결 표시 상태 ‘{STATUS_DONE}’" in body["subtitle"]
    lead = body["sections"][0]["body"]
    assert lead.startswith(f"{ev.occurred_at.year}년 ") and f"후속보도 {n}건이 확인됐다고" in lead and "책임기관은 " in lead
    text = body["sections"][1]["body"]
    assert "라고 밝혔다" in text and f"후속보도는 {n}건이 확인됐다" in text and "내용이 확인됐다(" in text
    assert f"현재 표시 상태는 ‘{STATUS_DONE}’" in body["sections"][2]["body"]


def test_article_event_draft_gap(client, session):
    ev = next(e for e in session.query(Event).all() if "달빛지하차도" in e.title)
    brief = client.get(f"/api/press/draft?event_id={ev.id}").json()
    body = client.get(f"/api/press/draft?event_id={ev.id}&format=article").json()
    _assert_article_clean(body)
    _assert_same_numbers(body, brief)
    assert body["title"] == f"‘{ev.title}’, 기관 발표 이후 후속보도 확인되지 않아"
    text = body["sections"][1]["body"]
    assert "배수펌프" in text and "후속보도는 확인되지 않았다" in text and DISCLAIMER in text
    assert "책임기관 공식 게시판 확인 결과는 ‘등록된 게시판 없음’" in text


def test_article_every_event_generates(client):
    for row in client.get("/api/press/events").json():
        body = client.get(f"/api/press/draft?event_id={row['id']}&format=article").json()
        _assert_article_clean(body)
        _assert_same_numbers(body, client.get(f"/api/press/draft?event_id={row['id']}").json())


def test_article_format_validation(client):
    assert client.get("/api/press/draft?format=essay").status_code == 422
    assert client.get("/api/press/draft?format=brief").json()["format"] == "brief"
    assert client.get("/api/press/draft?event_id=999999&format=article").status_code == 404


# ---------------------------------------------------------------------------
# 템플릿·작성기
# ---------------------------------------------------------------------------


def test_article_templates_have_no_forbidden_or_blame_words():
    for v in A.values():
        for t in v if isinstance(v, tuple) else (v,):
            text = t.template if hasattr(t, "template") else t
            assert not any(w in text for w in FORBIDDEN + BLAME), text


def test_build_writer_formats():
    st = get_settings()
    assert isinstance(build_writer(st, "article"), ArticleWriter) and isinstance(build_writer(st), TemplateWriter)
    with pytest.raises(ValueError):
        build_writer(st, "essay")


def test_llm_writer_polishes_article_with_same_guard(session):
    st = get_settings()

    class FakeMessages:
        def __init__(self, reply):
            self.reply = reply

        def create(self, **kw):
            return SimpleNamespace(content=[SimpleNamespace(text=self.reply(kw["messages"][0]["content"]))])

    ov = D.overview(session, st, TODAY)
    base = ArticleWriter(st).write_all(ov)
    bad = LLMWriter(st, client=SimpleNamespace(messages=FakeMessages(lambda text: "사건 999건으로 바꿈 [1]")), base=ArticleWriter(st))
    out = bad.write_all(ov)
    assert out.format == "article" and out.writer.startswith("llm-press-v1")
    assert [s["body"] for s in out.sections] == [s["body"] for s in base.sections]  # 숫자가 바뀐 다듬기는 버린다
    good = LLMWriter(st, client=SimpleNamespace(messages=FakeMessages(lambda text: text.replace("확인됐다", "확인됐다고 집계됐다"))), base=ArticleWriter(st))
    out2 = good.write_all(ov)
    assert "집계됐다" in out2.sections[0]["body"] and "집계됐다" in out2.sections[1]["body"]
    assert out2.sections[2]["body"] == base.sections[2]["body"] and out2.sections[3]["body"] == base.sections[3]["body"]  # 배경·마무리는 다듬지 않는다
    assert out2.title == base.title and out2.subtitle == base.subtitle
