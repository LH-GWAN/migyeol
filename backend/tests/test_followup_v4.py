"""3단계 재현율 보강 (docs/EXPERIMENT.md §14) — 제목형 표현(rules-v4) · 약속 없는 사건의 직접 후속 경로."""

from __future__ import annotations

import os

import pytest

from app.config import Settings, get_settings
from app.db.models import Event, FollowupSignal, Promise
from app.llm import rules_adapter as R
from app.llm.schemas import FollowupSignal as SignalOut
from app.pipeline import judge as J


def S(**kw) -> Settings:
    base = dict(_env_file=None, followup_expression_fixes=True, followup_cutoff="grace", followup_min_link_prob=0.0)
    base.update(kw)
    return Settings(**base)


# ---------------------------------------------------------------- 제목형 표현 (맹검 라벨 근거 기사의 제목에서 — 놓친 후속)
@pytest.mark.parametrize(
    "text,expected",
    [
        ("작년 산사태가 할퀸 곳…장마 앞두고 복구 완료", ["완료"]),
        ("국정자원 화재 95일 만에…709개 행정정보 시스템 전체 복구", ["완료"]),
        ("'경부선 열차 사고 작업중지' 연내 전면해제", ["진행", "진행"]),  # '연내' — 앞날의 해제는 진행
        ("구로역 장비열차 충돌사고, 작업대 선로 침범이 직접 원인", ["완료"]),
        ("‘성탄절 참변’ 도봉구 아파트 화재…‘담배꽁초’가 원인이었다니", ["완료"]),
        ("도봉구, 아파트 화재 유족에게 사회 재난 구호금 지급", ["진행"]),
        ("충남도, 호우피해 '논산 마산천·왕암천' 488억 투입", ["진행"]),
        ("‘방화’ 서울 지하철 대선 6월3일까지 24시간 현장순찰", ["진행"]),
        ("이제야 움직인 당국… 융건릉 노송, 9월부터 복구", ["진행"]),
        ("성탄절 화재 도봉구 아파트, 이재민 임시 숙소·주민 심리 상담소 운영", ["진행"]),
    ],
)
def test_headline_expressions_catch_noun_form_followups(text, expected):
    assert [s for s, _ in R._expressions(text, True, False)] == []  # rules-v3 는 못 읽던 제목
    assert [s for s, _ in R._expressions(text, True, True)] == expected


@pytest.mark.parametrize(
    "text",
    [
        "실종자 수색 마무리 수순",  # 응급 대응 자체의 마무리는 후속 조치가 아니다 (맹검 라벨 기준)
        "산불 진화 완료",
        "화재 원인 조사 중…감식 진행",  # 원인을 아직 모름
        "경찰, 화재 원인 추정",
        "김기웅 서천군수, 산림청장과 산사태 복구현장 안전점검 나서",  # 같은 점검의 전재 (남은 오탐 유형)
        "복구 완료 목표 시점은 내년",
        "안양 청과동이 전면 복구공사를 거쳐 다시 문을 연다",  # '전면 복구공사' 는 공사 이름이지 완료가 아니다
        "중도매인들은 가설건축물에서 복구공사 완료 때까지 영업",
        "검찰, 5호선 방화범 구속 기소...160명 살인미수 혐의 추가",  # 기소·재판은 후속 조치가 아니다
        "순천시, 산불방지 소각행위 집중단속",  # 예방 캠페인
    ],
)
def test_headline_expressions_do_not_fire_on_emergency_or_pending(text):
    assert [s for s, _ in R._expressions(text, True, True)] == []


def test_future_reopening_is_progress_with_headline_rules():
    """'내년 2월 재개장' — rules-v3 는 완료가 아니라서 버렸고, rules-v4 는 일정이 잡힌 복구(진행)로 본다 (안양 청과동)."""
    t = "안양농수산물도매시장 청과동, 내년 2월 재개장"
    assert [s for s, _ in R._expressions(t, True, False)] == []
    assert "진행" in [s for s, _ in R._expressions(t, True, True)]


def test_rules_model_v4_name():
    assert R.RulesLLM(S()).model == "rules-v3"
    assert R.RulesLLM(S(followup_headline_expressions=True)).model == "rules-v4"
    assert R.RulesLLM(S(followup_event_path=True)).model == "rules-v4"


# ---------------------------------------------------------------- 약속 없는 사건의 직접 후속 경로 (가상 픽스처 DB)
class FixedLLM:
    model = "fixed-v4"

    def classify_followup(self, inp, *, key: str = ""):
        pid = inp.promises[0].id if inp.promises else None
        return SignalOut(signal="완료", promise_id=pid, matches_promise=True, evidence_span=(inp.article_title or "")[:80], confidence=0.9, rationale="고정")


@pytest.fixture
def pipeline_db(tmp_path, fixtures_ready):
    from app.bigkinds import build_client
    from app.db.session import init_db, reset_engine, session_factory
    from app.llm.client import build_llm
    from app.pipeline.embeddings import build_embedding
    from app.pipeline.runner import Pipeline

    url = f"sqlite:///{tmp_path / 'fu4.db'}"
    get_settings.cache_clear()
    os.environ["DATABASE_URL"] = url
    settings = get_settings()
    reset_engine()
    init_db(url, drop=True)
    session = session_factory()()
    Pipeline(session, settings, build_client(settings), build_llm(settings), build_embedding(settings)).run("all")
    yield session, settings
    session.close()


def _event_without_promises(session) -> Event:
    for ev in session.query(Event).all():
        if J.trackable_promises(ev) and len([l for l in ev.links if l.is_active]) >= 3:
            for p in session.query(Promise).filter_by(event_id=ev.id).all():
                p.is_trackable = False  # 추적 약속이 없는 사건으로 만든다
            session.commit()
            session.refresh(ev)
            assert not J.trackable_promises(ev)
            return ev
    pytest.skip("가상 픽스처에 연결이 있는 사건이 없다")


def test_event_path_classifies_events_without_promises(pipeline_db):
    session, settings = pipeline_db
    ev = _event_without_promises(session)
    off = settings.model_copy(update={"followup_event_path": False})
    J.classify_followups(session, ev, FixedLLM(), off)
    assert session.query(FollowupSignal).filter_by(event_id=ev.id, model="fixed-v4").count() == 0  # 이전 동작: 약속이 없으면 분류하지 않는다
    on = settings.model_copy(update={"followup_event_path": True, "followup_event_min_prob": 0.0})
    stats = J.classify_followups(session, ev, FixedLLM(), on)
    session.expire_all()
    sigs = session.query(FollowupSignal).filter_by(event_id=ev.id, model="fixed-v4").all()
    assert sigs and stats["event_path"] == len(sigs)
    assert all(s.promise_id is None and s.matches_promise for s in sigs)  # 가짜 약속은 저장하지 않고, 일치로 센다
    assert all("직접 후속" in s.rationale for s in sigs)
    j = J.rejudge_event(session, ev, on, on.today())
    assert j.status == "조치 완료 근거 확인"  # 상태 규칙 R2 가 그대로 받는다


def test_event_path_needs_high_probability_links(pipeline_db):
    session, settings = pipeline_db
    ev = _event_without_promises(session)
    strict = settings.model_copy(update={"followup_event_path": True, "followup_event_min_prob": 2.0})
    stats = J.classify_followups(session, ev, FixedLLM(), strict)
    assert stats["event_path"] == 0
    assert session.query(FollowupSignal).filter_by(event_id=ev.id, model="fixed-v4").count() == 0


def test_event_path_ref_uses_facility_and_unique_words():
    ev = Event(key="k", title="대전 국가정보자원관리원 화재 전산망 마비", facility="국가정보자원관리원", responsible_org="행정안전부",
               term_weights={"국정자원": 0.95, "전산망": 0.7, "대전": 0.05, "행정": 0.3})
    ref = J.event_path_ref(ev, S())
    assert ref.id == J.EVENT_PATH_PROMISE_ID and ref.actor_org == "행정안전부"
    assert "국가정보자원관리원" in ref.action and "국정자원" in ref.action and "전산망" in ref.action
    assert "대전" not in ref.action.split()  # 가중치 낮은 지역어는 대상어가 아니다


def test_cause_result_signal_is_tagged():
    """조사 결과 표현으로 난 신호는 rationale 에 표지를 남긴다 — judge 가 원천 발언 직후면 내린다."""
    from app.llm.schemas import FollowupInput, PromiseRef

    inp = FollowupInput(event_summary="", promises=[PromiseRef(id=1, actor_org="국토교통부", action="구로역 장비열차 충돌 사고 원인을 철저히 조사하겠다")],
                        article_title="구로역 장비열차 충돌사고, 작업대 선로 침범이 직접 원인", hilight="", published_at="2025-11-18")
    sig = R.RulesLLM(S(followup_headline_expressions=True)).classify_followup(inp)
    assert sig.signal == "완료" and R.CAUSE_RESULT_TAG in sig.rationale


class CauseLLM(FixedLLM):
    model = "fixed-v4-cause"

    def classify_followup(self, inp, *, key: str = ""):
        pid = inp.promises[0].id if inp.promises else None
        return SignalOut(signal="완료", promise_id=pid, matches_promise=True, evidence_span=(inp.article_title or "")[:80], confidence=0.9,
                         rationale=f"고정 · {R.CAUSE_RESULT_TAG}")


def test_cause_result_right_after_source_is_demoted(pipeline_db):
    """원천 발언 7일 안의 '원인은 …' 보도는 같은 발표의 전재일 때가 많다 (통복터널: 2-28 자문단 원인 발표 → 3-01 '원인은 부적절 자재')."""
    from datetime import timedelta

    session, settings = pipeline_db
    for ev in session.query(Event).all():
        if J.trackable_promises(ev) and len(J.followup_articles(session, ev, settings)) >= 2:
            break
    else:
        pytest.skip("후속 기사가 있는 사건이 없다")
    J.classify_followups(session, ev, CauseLLM(), settings)
    session.expire_all()
    src = J.source_date(session, ev) or ev.occurred_at
    for sig in session.query(FollowupSignal).filter_by(event_id=ev.id, model="fixed-v4-cause").all():
        a = session.get(__import__("app.db.models", fromlist=["Article"]).Article, sig.news_id)
        if a.published_at <= src + timedelta(days=J.CAUSE_RESULT_MIN_DAYS):
            assert sig.signal == "불명확" and not sig.matches_promise
        elif "약속 발언일" not in sig.rationale:
            assert sig.signal == "완료"


def test_headline_completion_next_to_future_word_is_progress():
    assert [s for s, _ in R._expressions("국정자원 화재, 7주만 대전시스템 복구 완료…대구센터 이전도 연내 마무리", True, True)][0] == "완료"
    assert [s for s, _ in R._expressions("장마 앞두고 복구 완료", True, True)] == ["완료"]  # '앞두고' 는 앞날이 아니다
    assert "완료" not in [s for s, _ in R._expressions("청과동 지붕 복구 공사 연내 마무리 목표", True, True)]

