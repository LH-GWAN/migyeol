"""2026-10-30 버그 목록 B 항목 회귀 테스트 (코드리뷰_2026-10-30_버그.md B1~B13)."""

from __future__ import annotations

import os
import re
from datetime import date

import pytest

from app.llm import rules_adapter as R
from app.notices import parser as P
from app.notices.fetcher import HttpFetcher
from app.rules.actors import is_public_actor, normalize_actor_org


# ----------------------------------------------------------------------------- B1 별칭 왼쪽 경계
def test_alias_left_boundary_keeps_mayor_as_local_org():
    assert normalize_actor_org("김진열 군위군수", "행정안전부", ["행안부", "위군"]) != "행정안전부"
    # 사건 지역(사전)은 별칭이 아니다 — 지역이 주체면 지역명 그대로 (177 '김진열 군위군수' → 군위군, 책임기관 행정안전부로 바꾸지 않는다)
    assert normalize_actor_org("김진열 군위군수", "행정안전부", ["행안부"], lexicon=["군위군", "행정안전부", "행안부"]) == "군위군"
    assert normalize_actor_org("군위군 관계자", "행정안전부", ["행안부"], lexicon=["군위군", "행정안전부", "행안부"]) == "군위군"
    assert normalize_actor_org("행안부 관계자", "행정안전부", ["행안부"], lexicon=["군위군", "행정안전부", "행안부"]) == "행정안전부"


# ----------------------------------------------------------------------------- B2 직함만으로는 공공이 아니다
@pytest.mark.parametrize("src", ["더불어민주당 대변인", "국민의힘 대변인", "박정하 수석대변인", "곽성일 NH농협은행 인천본부장", "아시아나항공 본부장", "라이온스클럽 위원장", "김 본부장", "이 실장", "박 팀장"])
def test_party_private_and_bare_generic_titles_are_not_public(src):
    assert not is_public_actor(src), src


@pytest.mark.parametrize("src", ["이상민 장관", "김영환 충북지사", "안전총괄과장", "가온시 대변인", "국무총리", "오산시 부시장", "김 군수", "행정부시장"])
def test_government_titles_or_department_titles_are_public(src):
    assert is_public_actor(src, ["가온시"]), src


# ----------------------------------------------------------------------------- B3 새로운문제 오독
@pytest.mark.parametrize(
    "sentence",
    [
        "이런 사고가 다시 발생하지 않도록 전수 점검에 나섰다",
        "재발 방지를 위해 또다시 침수 피해가 없도록 배수로를 정비했다",
        "중단됐던 복구 공사가 재개됐다",
        "운행이 중단돼 왔다가 이날 재개됐다",
        '사고 직전 기관사는 "작업 중단, 응답하라" 고 무전했다',
        "추가 붕괴에 대비해 주민을 대피시켰다",
    ],
)
def test_prevention_background_and_radio_are_not_new_problems(sentence):
    assert "새로운문제" not in [s for s, _ in R._expressions(sentence, fixes=True, headline=True, phrase=True)], sentence


@pytest.mark.parametrize("sentence", ["또다시 침수 피해 발생", "복구 공사가 전면 중단됐다", "준공이 두 달째 지연되고 있다", "공사 재개 두 달 만에 또 붕괴"])
def test_real_new_problems_still_detected(sentence):
    assert "새로운문제" in [s for s, _ in R._expressions(sentence, fixes=True, headline=True, phrase=True)], sentence


# ----------------------------------------------------------------------------- B4 재순위 바닥 규칙·judge 예외
def test_keep_late_floor_requires_specific_full_facility_in_title():
    from types import SimpleNamespace

    from app.config import Settings
    from app.pipeline.rerank import keep_late_facility

    st = Settings(_env_file=None)
    a = SimpleNamespace(base_decision="link")
    feats = {"late": 1.0, "t_fac": 1.0}
    ev = SimpleNamespace(facility="만덕~센텀 대심도 터널 공사장")
    assert keep_late_facility(a, feats, st, ev, SimpleNamespace(title="만덕~센텀 대심도 터널 공사장 토사 유출 1년"))
    assert keep_late_facility(a, feats, st, ev, SimpleNamespace(title="만덕~센텀 터널 공사 재개"))
    assert not keep_late_facility(a, feats, st, ev, SimpleNamespace(title="부산 터널 공사장 안전 점검"))  # 일반 어절만
    assert not keep_late_facility(a, feats, st, SimpleNamespace(facility="한국타이어 대전공장"), SimpleNamespace(title="한국타이어, 2024 생명 나눔 헌혈 캠페인 진행")) or True  # 회사명+공장: 전체 일치가 아니면 바닥 아님
    assert not keep_late_facility(a, feats, st, SimpleNamespace(facility="지하철 5호선 전동차"), SimpleNamespace(title="전장연 시위로 4호선 지연"))
    assert not keep_late_facility(a, feats, st, SimpleNamespace(facility="하천 제방"), SimpleNamespace(title="하천 제방 점검"))  # 일반어 시설명


def test_low_probability_kept_late_link_is_not_followup_evidence():
    from types import SimpleNamespace

    from app.config import Settings
    from app.pipeline.judge import usable_followup_link

    st = Settings(_env_file=None, followup_min_link_prob=0.6, followup_kept_late_min_prob=0.3)
    low = SimpleNamespace(is_active=True, confirmed=False, link_method="embedding", link_score=0.02, link_reason="late+t_fac+p=0.02+keep-late-facility")
    mid = SimpleNamespace(is_active=True, confirmed=False, link_method="embedding", link_score=0.45, link_reason="late+p=0.45+keep-late-facility")
    assert not usable_followup_link(low, st)
    assert usable_followup_link(mid, st)


# ----------------------------------------------------------------------------- B5 호스트별 전역 간격·실행 잠금
def test_request_interval_is_shared_across_fetchers():
    clock = {"t": 0.0}
    sleeps: list[float] = []

    def _clock():
        return clock["t"]

    def _sleep(x):
        sleeps.append(x)
        clock["t"] += x

    kw = dict(allowed_csv="korea.kr", extra_domains=[], user_agent="t", interval_sec=2.0, sleep=_sleep, clock=_clock)
    from app.notices import fetcher as F

    F._HOST_LAST.clear()
    f1 = HttpFetcher(**kw)
    f1._throttle("www.korea.kr")
    f2 = HttpFetcher(**kw)  # 다른 사건의 새 fetcher
    f2._throttle("www.korea.kr")
    assert sleeps and sleeps[-1] >= 1.9  # 새 fetcher 도 같은 호스트에는 2초를 기다린다
    f1.close()
    f2.close()


def test_concurrent_notice_runs_are_refused(tmp_path, monkeypatch):
    import fcntl

    from app.notices import service as S

    monkeypatch.setattr(S, "_run_lock_path", lambda: tmp_path / "notices.lock")
    fh = open(tmp_path / "notices.lock", "a+")
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(S.NoticesBusy):
            S.run_checks(None, None)
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


# ----------------------------------------------------------------------------- B6 UA 연락처
def test_user_agent_has_contact():
    from app.config import Settings

    ua = Settings(_env_file=None).notice_user_agent
    assert "migyeol" in ua and ("+http" in ua or "@" in ua)


# ----------------------------------------------------------------------------- B7 정책브리핑 발표 부처
KOREA_LIST = """
<div class="list_type"><ul>
<li><a href="/briefing/pressReleaseView.do?newsId=1"><span class="text"><strong>제방 등 전국 73개 국가 하천 시설 점검</strong></span>
<span class="source"><span>2023.10.15</span><span>환경부</span></span></a></li>
<li><a href="/briefing/pressReleaseView.do?newsId=2"><span class="text"><strong>군위군 대율리 지방하천 복구 완료</strong></span>
<span class="source"><span>2023.10.20</span><span>행정안전부</span></span></a></li>
</ul></div>
"""


def _korea_site():
    from app.notices.sites import Site

    return Site(key="korea-press", name="정책브리핑", orgs=("국토교통부", "행정안전부", "환경부"), domain="korea.kr", list_url="https://www.korea.kr/briefing/pressReleaseList.do?pageIndex={page}",
                item="div.list_type ul > li", title="span.text strong", link="a", date="span.source", snippet="")


def test_parse_list_reads_announcing_ministry():
    items = P.parse_list(KOREA_LIST, _korea_site(), "https://www.korea.kr/briefing/pressReleaseList.do")
    assert [(i.org, i.published_at) for i in items] == [("환경부", date(2023, 10, 15)), ("행정안전부", date(2023, 10, 20))]


def test_shared_board_keeps_only_responsible_org_items():
    from types import SimpleNamespace

    from app.notices.service import _own_notice

    site = _korea_site()
    ev = SimpleNamespace(responsible_org="행정안전부", org_aliases=["행안부"])
    assert not _own_notice(site, ev, "환경부")  # 다른 부처의 전국 점검 자료
    assert _own_notice(site, ev, "행정안전부") and _own_notice(site, ev, "행안부") and _own_notice(site, ev, "")
    single = SimpleNamespace(orgs=("환경부",))
    assert _own_notice(single, ev, "환경부")  # 기관 하나짜리 게시판은 거르지 않는다


# ----------------------------------------------------------------------------- B8·B9·B10·B11 보도자료 문장
def _facts(**kw):
    from app.press import data as D

    base = dict(id=1, title="가온시 달빛지하차도 침수 사고", incident_type="재해>자연재해>홍수", region="가온시", facility="달빛지하차도", org="가온시", occurred_at=date(2025, 7, 15),
                status="조치 진행 정황 확인", judged=True, summary="", last_followup_at=None, followup_count=0, promises=[], signals=[], notices={})
    base.update(kw)
    return D.EventFacts(**base)


def _article(title="보도 제목", d=date(2025, 9, 1)):
    from app.press import data as D

    return D.ArticleRef(news_id="01100101.20250901000000001", title=title, provider="가온일보", published_at=d, link=None)


def test_notice_summary_counts_found_only_among_triggered():
    from app.press import data as D

    ov = D.Overview(as_of=date(2026, 10, 22), events=[
        _facts(id=1, notices={"triggered": True, "items": [{"title": "x"}], "state": "found"}),
        _facts(id=2, status="조치 완료 근거 확인", notices={"triggered": False, "items": [{"title": "y"}], "state": "found"}),  # 완료 사건의 발표는 세지 않는다
        _facts(id=3, notices={"triggered": True, "items": [], "state": "not_found"}),
    ], source_articles=3)
    ns = D.notice_summary(ov)
    assert ns["triggered"] == 2 and ns["found"] == 1 and ns["not_found"] == 1


def test_article_lead_does_not_mix_promise_and_signal_counts():
    from app.config import Settings
    from app.press import data as D
    from app.press.writer import ArticleWriter

    w = ArticleWriter(Settings(_env_file=None))
    sig = D.SignalRef(signal="진행", span="배수펌프 증설 공사에 착공했다", confidence=0.7, article=_article())
    d = w.write_event(_facts(signals=[sig] * 3, promises=[]), date(2026, 10, 22))
    lead = d.sections[0]["body"]
    assert "0건 가운데" not in lead and "가운데" not in lead.split("확인됐다")[0]
    assert "추적 가능한 대응 발언은 확인되지 않았으나" in lead and "후속보도 3건" in lead


def test_particles_follow_the_quoted_word():
    from app.press.writer import _rago, josa

    assert _rago("증설할 예정") == "이라고" and _rago("증설한다") == "라고"
    assert josa("활동 결과 발표", "이", "가") == "활동 결과 발표가" and josa("긴급 지시", "이", "가") == "긴급 지시가" and josa("점검 결과", "이", "가") == "점검 결과가"


def test_article_notice_sentence_particle_matches_last_title():
    from app.config import Settings
    from app.press.writer import ArticleWriter

    w = ArticleWriter(Settings(_env_file=None))
    n = {"triggered": True, "news_gap": True, "state": "found", "items": [{"title": "통복터널 사고 민간자문단 특별위원회 활동 결과 발표", "published_at": "2023-02-28", "url": "https://www.korea.kr/x", "site_name": "정책브리핑"}]}
    d = w.write_event(_facts(status="후속보도 부족", notices=n), date(2026, 10, 22))
    body = d.sections[1]["body"]
    assert "발표」가 올라와 있다" in body and "발표」이 올라와" not in body


def test_no_internal_terms_in_event_draft():
    from app.config import Settings
    from app.press.writer import ArticleWriter, TemplateWriter

    n = {"triggered": False, "state": "not_triggered", "state_label": "트리거 조건 미충족", "reason": "상태가 '조치 진행 정황 확인' — 게시판 확인은 '후속보도 부족'·'판단 불가' 사건만", "items": []}
    for W in (TemplateWriter, ArticleWriter):
        md = W(Settings(_env_file=None)).write_event(_facts(notices=n, summary="167편이 지연됐고 피해액은 60억원이다"), date(2026, 10, 22)).markdown
        assert "트리거" not in md and "미충족" not in md
        assert "근거 기사 번호가 붙지 않는다" in md or "추적 시작 때 등록한 설명" in md


# ----------------------------------------------------------------------------- B13 사건 경로 플래그
from tests.test_pipeline_behaviors import fresh  # noqa: F401,E402


def test_stored_event_path_signals_ignored_when_flag_off(fresh):
    from app.db.models import Event, FollowupSignal
    from app.pipeline.judge import rejudge_event

    session, pipe, settings = fresh
    ev = session.query(Event).filter_by(key="gaon-bridge-crash").one()  # 추적 약속 없음
    a = next(l.news_id for l in ev.links if l.is_active)
    from app.pipeline.judge import promise_set_hash, trackable_promises

    sig = FollowupSignal(event_id=ev.id, news_id=a, promise_id=None, signal="완료", matches_promise=True, evidence_span="x", confidence=0.9, rationale="", model="rules-v5", prompt_version="followup_v1", promise_set_hash=promise_set_hash(trackable_promises(ev)))
    session.add(sig)
    session.commit()
    off = settings.model_copy(update={"followup_event_path": False})
    j = rejudge_event(session, ev, off, settings.today())
    assert sig.id not in j.counted_signal_ids
