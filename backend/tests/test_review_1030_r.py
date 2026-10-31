"""2026-10-30 수정검증(코드리뷰_2026-10-30_수정검증.md) 회귀 — R1~R5·D1·D2 와 2장 부분 수정 항목(A2·A4·B5·B7·B9·B11·B13)."""

from __future__ import annotations

import csv
from datetime import date
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.llm import rules_adapter as R
from app.rules.actors import is_public_actor, normalize_actor_org


# ----------------------------------------------------------------------------- R1 이름이 붙은 기관명·접미 조각
@pytest.mark.parametrize(
    "src,expected",
    [
        ("박종철부산시 의원", "부산시"),  # 별칭 왼쪽 경계가 생기며 '박종철부산시' 가 통째로 기관이 되던 것
        ("윤호중행정안전부 장관", "행정안전부"),
        ("한국철도공사 관계자", "한국철도공사"),  # '한' 이 성씨라고 '국철도공사' 로 자르지 않는다
        ("한강유역환경청 관계자", "한강유역환경청"),
        ("한국수자원공사 관계자", "한국수자원공사"),
    ],
)
def test_person_name_glued_to_org_is_stripped(src, expected):
    assert normalize_actor_org(src, "부산시", ["부산시"]) == expected if expected == "부산시" else normalize_actor_org(src) == expected


@pytest.mark.parametrize("src", ["당시 관계자", "군위부 관계자", "부부 관계자", "경시 관계자"])
def test_one_or_two_char_stems_are_not_orgs(src):
    assert normalize_actor_org(src) is None


def test_deputy_director_is_not_an_org():
    assert normalize_actor_org("김여정 노동당 부부장") is None
    assert not is_public_actor("김여정 노동당 부부장")


# ----------------------------------------------------------------------------- R2 공공 주체가 민간으로 떨어진 것
@pytest.mark.parametrize(
    "src",
    [
        "국방부 대변인", "김동현 고용부 산업안전과장", "소방본부장", "홍영근 전남 소방본부장", "김병환 금융위원장", "사고조사위원장", "국토교통위원장",
        "식량원예 과장", "경비안전 과장", "항공안전 과장", "사회재난 과장", "이요한 서울경찰청 항공 대장", "안전총괄과장", "소방서 관계자", "경찰서 수사과장",
        "강원도 부지사", "서울시 수석대변인", "국방부 관계자", "해양경찰청 대변인",
    ],
)
def test_public_actors_stay_public(src):
    assert is_public_actor(src), src


@pytest.mark.parametrize(
    "src",
    ["김여정 노동당 부부장", "라이더유니온 지부장", "대한적십자사 지부 회장", "더불어민주당 대변인", "박정하 수석대변인", "곽성일 NH농협은행 인천본부장",
     "아시아나항공 본부장", "라이온스클럽 위원장", "김 본부장", "이 실장", "박 팀장", "선대위원장", "김 수석대변인", "한국노총 위원장", "민주노총 대변인"],
)
def test_private_actors_stay_private(src):
    assert not is_public_actor(src), src


# ----------------------------------------------------------------------------- B1 지역은 별칭이 아니다
def test_region_in_lexicon_is_returned_as_itself():
    lex = ["군위군", "행정안전부", "행안부"]
    assert normalize_actor_org("김진열 군위군수", "행정안전부", ["행안부"], lexicon=lex) == "군위군"
    assert normalize_actor_org("행안부 관계자", "행정안전부", ["행안부"], lexicon=lex) == "행정안전부"
    assert normalize_actor_org("군위군 관계자", "행정안전부", ["행안부"], lexicon=lex) == "군위군"


def test_extract_passes_region_separately_from_aliases():
    import inspect

    from app.pipeline import extract as X

    src = inspect.getsource(X.resolve_actor_org)
    assert "event.org_aliases" in src and "lexicon=lexicon" in src


# ----------------------------------------------------------------------------- R4 예방 꼬리 가드가 진짜 재발까지 지우던 것
def _exprs(t: str) -> list[str]:
    return [s for s, _ in R._expressions(t, True, True, True)]


@pytest.mark.parametrize("t", ["또 사고 났는데 재발 방지책은 없었다", "또 붕괴, 대비 소홀 지적", "같은 공장서 또 사망 방지 대책 무용지물", "또다시 침수 피해 발생"])
def test_real_recurrence_is_kept(t):
    assert "새로운문제" in _exprs(t), t


@pytest.mark.parametrize(
    "t",
    ["이런 사고가 다시 발생하지 않도록 전수 점검에 나섰다", "재발 방지를 위해 또다시 침수 피해가 없도록 배수로를 정비했다", "추가 피해지 조사에 나섰다",
     "추가 붕괴 우려에 주민 대피", "추가 붕괴가 우려돼 통제", "중단돼 왔다가 재개됐다", '기관사는 "작업 중단, 응답하라" 고 무전했다',
     '"선로에 작업자 있다, 운행 중단" 이라는 교신이 있었다'],
)
def test_prevention_background_and_radio_are_not_new_problems(t):
    assert "새로운문제" not in _exprs(t) and "중단" not in _exprs(t), t


# ----------------------------------------------------------------------------- A4 완료 오독 추가 표현·요구 꼬리 범위
@pytest.mark.parametrize(
    "t",
    ["장마 전까지 복구를 끝내야 한다", "공사를 완료돼야 입주한다", "공사가 마무리돼도 문제다", "복구 작업이 끝나도 남는다", "공사가 마무리되는 시점에 재개한다",
     "공사가 끝나기 전까지 통제한다", "사용승인을 받아야 완료된다", "복구 공사를 끝내라"],
)
def test_more_completion_misreads_are_blocked(t):
    assert "완료" not in _exprs(t), t


def test_second_half_completion_is_progress_not_done():
    assert _exprs("하반기 설치 완료") == ["진행"]


def test_demand_tail_needs_adjacent_verb():
    assert "완료" in _exprs("자체 조사 완료, 개선 건의")  # '완료 … 건의' 는 요구 꼬리가 아니다 ('완료 후 …' 는 원래 순서 표현이라 제외)
    assert "완료" in _exprs("자체 조사 완료해 개선 건의")
    assert "완료" not in _exprs("조속한 복구 완료를 당부했다")


# ----------------------------------------------------------------------------- D1 규칙 모델명
def test_phrase_fix_rules_have_their_own_model_name():
    assert R.RulesLLM(Settings(_env_file=None, followup_phrase_fixes=True)).model == "rules-v6"
    assert R.RulesLLM(Settings(_env_file=None, followup_phrase_fixes=False, followup_recurrence=True)).model == "rules-v5"


# ----------------------------------------------------------------------------- R5 바닥 연결 최소 확률·재발 기사
def _link(p: float, reason: str):
    return SimpleNamespace(is_active=True, confirmed=False, link_method="embedding", link_score=p, link_reason=reason)


def test_default_min_prob_keeps_genuine_late_completion_links():
    from app.pipeline.judge import usable_followup_link

    st = Settings(_env_file=None, followup_min_link_prob=0.6)
    assert st.followup_kept_late_min_prob <= 0.11
    assert usable_followup_link(_link(0.109, "late+t_fac+p=0.109+keep-late-facility"), st)  # 175 '옥룡제 보수 완료'
    assert not usable_followup_link(_link(0.02, "late+t_fac+p=0.02+keep-late-facility"), st)  # 헌혈 캠페인


def test_recurrence_article_is_usable_regardless_of_probability():
    from app.pipeline.judge import usable_followup_link

    st = Settings(_env_file=None, followup_min_link_prob=0.6, followup_recurrence=True)
    ev = SimpleNamespace(facility="만덕~센텀 도시고속화도로", occurred_at=date(2024, 9, 21))
    art = SimpleNamespace(title="만덕~센텀 도시고속화도로 공사장서 또 지반 침하", published_at=date(2025, 3, 2))
    assert usable_followup_link(_link(0.04, "late+t_fac+p=0.04+keep-late-facility"), st, art, ev)
    other = SimpleNamespace(title="만덕~센텀 도시고속화도로 인근 헌혈 캠페인", published_at=date(2025, 3, 2))
    assert not usable_followup_link(_link(0.04, "late+t_fac+p=0.04+keep-late-facility"), st, other, ev)


def test_rejudge_reverts_phase_of_links_without_valid_signal():
    import inspect

    from app.pipeline import judge as J

    src = inspect.getsource(J.rejudge_event)
    assert "initial_phase" in src and 'l.phase in ("조치진행", "결과확인")' in src


# ----------------------------------------------------------------------------- B13 신호 범위 한 곳
def test_signal_scope_is_shared_by_dashboard_metrics_and_press():
    import inspect

    from app.api import dashboard as D
    from app.experiment import metrics as M
    from app.pipeline.judge import signal_in_scope
    from app.press import data as PD

    st = Settings(_env_file=None, followup_event_path=False)
    assert not signal_in_scope(SimpleNamespace(promise_id=None), [], st)
    assert signal_in_scope(SimpleNamespace(promise_id=None), [], Settings(_env_file=None, followup_event_path=True))
    assert signal_in_scope(SimpleNamespace(promise_id=3), [], st)
    for mod in (D, M, PD):
        assert "signal_in_scope(" in inspect.getsource(mod), mod.__name__


# ----------------------------------------------------------------------------- B7 공용 게시판 기관 비교
def test_shared_board_org_compare_is_exact():
    from app.notices.service import _own_notice

    site = SimpleNamespace(orgs=["행정안전부", "해양경찰청", "경찰청"])
    ev = SimpleNamespace(responsible_org="해양경찰청", org_aliases=["해경"])
    assert _own_notice(site, ev, "해양경찰청")
    assert not _own_notice(site, ev, "경찰청")
    ev2 = SimpleNamespace(responsible_org="경찰청", org_aliases=[])
    assert not _own_notice(site, ev2, "해양경찰청")


# ----------------------------------------------------------------------------- A2 '뉴스엔 없었지만' 은 관련 발표만
def test_notice_specific_requires_facility_and_region_for_generic():
    from app.notices.service import notice_specific

    ev = SimpleNamespace(facility="하천 제방", region="경북 영천시")
    assert notice_specific(ev, "영천시", SimpleNamespace(matched_terms=["제방", "영천"], title="영천 제방 정비 결과"))
    assert not notice_specific(ev, "영천시", SimpleNamespace(matched_terms=["제방"], title="전국 하천 제방 일제 점검"))
    ev2 = SimpleNamespace(facility="거문도 방파제", region="전남 여수시")
    assert not notice_specific(ev2, "여수시", SimpleNamespace(matched_terms=["+요약:호우", "여수"], title="호우 피해 복구"))
    assert notice_specific(ev2, "여수시", SimpleNamespace(matched_terms=["거문도 방파제"], title="여수 거문도 방파제 보강"))


def test_article_title_uses_news_gap_not_item_count():
    import inspect

    from app.press import writer as W

    src = inspect.getsource(W)
    assert 'n.get("news_gap")' in src


# ----------------------------------------------------------------------------- B9 약속 0건 문구
def test_no_promise_followup_wording_does_not_mention_promises():
    from app.press.writer import A, T

    assert "약속" not in A["title_event_followup_nopromise"].template
    assert "약속" not in A["signal_intro_nopromise"].template
    assert "약속" not in T["event_followup_intro_nopromise"].template


# ----------------------------------------------------------------------------- B11 행동 절 조각
def test_action_clause_does_not_start_with_verb_tail():
    out = R._action_clause("이런 사고가 재발하지 않도록 민간자문단 특별위원회에서 제안한 방안을 적극 반영해 추진해 나가겠다")
    assert not out.startswith("않도록") and "민간자문단" in out


# ----------------------------------------------------------------------------- B5 잔여: 간격 하한·CLI 잠금
def test_fetcher_interval_floor_is_two_seconds():
    from app.notices.fetcher import HttpFetcher

    f = HttpFetcher(allowed_csv="korea.kr", extra_domains=[], user_agent="t", interval_sec=0.1)
    try:
        assert f.interval >= 2.0
    finally:
        f.close()


def test_cli_reports_busy_instead_of_traceback(monkeypatch, capsys):
    import scripts.run_notices as RN
    from app.notices.service import NoticesBusy

    def _busy(*a, **k):
        raise NoticesBusy("다른 실행 중")

    monkeypatch.setattr(RN, "run_checks", _busy)
    monkeypatch.setattr(RN, "init_db", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(RN, "session_factory", lambda *a, **k: (lambda: SimpleNamespace(close=lambda: None)), raising=False)
    monkeypatch.setattr("sys.argv", ["run_notices.py", "--all", "--today", "2026-10-22"])
    rc = RN.main()
    assert rc == 2 and "중단" in capsys.readouterr().out


# ----------------------------------------------------------------------------- D2 사람 라벨 행 보존
def test_labeled_rows_missing_from_new_aggregate_are_kept(tmp_path):
    from app.experiment.labels import _write

    path = tmp_path / "org_sample.csv"
    cols = ["원문", "출처", "자동", "사람"]
    _write(path, cols, [{"원문": "가온시 관계자", "출처": "x", "자동": "가온시", "사람": "가온시"}, {"원문": "누리군수", "출처": "y", "자동": "누리군", "사람": ""}], ["사람"], ["원문", "출처"])
    _write(path, cols, [{"원문": "새 주체", "출처": "z", "자동": "", "사람": ""}], ["사람"], ["원문", "출처"])
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = {(r["원문"], r["출처"]): r for r in csv.DictReader(f)}
    assert ("가온시 관계자", "x") in rows and rows[("가온시 관계자", "x")]["사람"] == "가온시"  # 라벨 있는 옛 행은 남는다
    assert ("누리군수", "y") not in rows  # 라벨 없는 옛 행은 지워진다
    assert ("새 주체", "z") in rows


# ----------------------------------------------------------------------------- A1 잔여: 기관 글·지원 발언도 가린다
def test_notices_and_support_outputs_are_scrubbed():
    import inspect

    from app.notices import service as NS
    from app.support import service as SS

    assert "scrub(r.title)" in inspect.getsource(NS.notices_out) and "scrub(r.snippet)" in inspect.getsource(NS.notices_out)
    assert "scrub(a.title)" in inspect.getsource(SS.support_out) and "scrub(m.evidence_span)" in inspect.getsource(SS.support_out)


# ----------------------------------------------------------------------------- 10-31 DB 확인에서 나온 것
@pytest.mark.parametrize("src", ["당국 관계자", "대처", "부처", "박시장", "김군수", "당시 기자"])
def test_non_org_tokens_give_unknown(src):
    assert normalize_actor_org(src, "안산시", []) is None


def test_district_office_is_the_district():
    assert normalize_actor_org("구청 관계자", "부산시", [], lexicon=["영도구", "부산시"]) == "영도구"


def test_wide_region_prefix_is_kept_for_fire_hq():
    assert normalize_actor_org("홍영근 전남 소방본부장") == "전남소방본부"
    assert normalize_actor_org("대구소방안전본부 관계자") in ("대구소방안전본부", "대구소방본부")


def test_future_relative_completion_is_not_done():
    assert "완료" not in _exprs("공사가 마무리되는 2027년께에는 생산능력이 늘어난다")
    assert "완료" not in _exprs("복구가 완료되는 내년 상반기에 재개한다")


def test_floor_link_review_threshold_matches_judge_min_prob():
    import inspect

    from app.pipeline import rerank as RR

    src = inspect.getsource(RR.rerank_event) if hasattr(RR, "rerank_event") else inspect.getsource(RR)
    assert "followup_kept_late_min_prob" in src and "recurrence_article" in src


def test_experiment_resets_rules_budget_per_event():
    import inspect

    from app.experiment import runner as ER

    assert 'pipe.call_budget["remaining"] = self.settings.llm_max_calls_per_run' in inspect.getsource(ER.Experiment.stage_followup)
