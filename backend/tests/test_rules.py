from datetime import date

from app.config import STATUS_DONE, STATUS_IN_PROGRESS, STATUS_NEW_PROBLEM, STATUS_NO_FOLLOWUP, STATUS_UNDETERMINED
from app.rules import priority
from app.rules.actors import is_public_actor, normalize_actor_org
from app.rules.status import PromiseIn, SignalIn, Thresholds, judge

TODAY = date(2026, 9, 11)
TH = Thresholds(signal_min_conf=0.6, completion_min_conf=0.7, stale_followup_days=60)


def sig(signal, conf, d, matches=True, pid=1, i=1):
    return SignalIn(id=i, news_id=f"n{i}", signal=signal, matches_promise=matches, confidence=conf, published_at=d, promise_id=pid)


def promise(deadline=None, strength="확약", trackable=True):
    return PromiseIn(id=1, deadline_date=deadline, strength=strength, is_trackable=trackable)


# ------------------------------------------------------------------ status rules
def test_r1_new_problem_wins_when_latest():
    signals = [sig("완료", 0.9, date(2025, 5, 15), i=1), sig("새로운문제", 0.85, date(2026, 7, 12), i=2)]
    j = judge(signals, [promise(date(2025, 6, 30))], [date(2025, 5, 15), date(2026, 7, 12)], TODAY, TH)
    assert j.status == STATUS_NEW_PROBLEM and j.reason == "R1_new_problem"
    assert j.counted_signal_ids == [2]
    # 그 전의 완료 근거는 채택하지 않고 '이전 완료 근거'로 남긴다
    assert j.prior_done_signal_ids == [1]
    assert [e["signal_id"] for e in j.evidence if e.get("role") == "prior_done"] == [1]
    assert "완료 근거 1건" in j.reason_text


def test_r1_without_prior_completion_has_no_prior_done():
    # R2 기준 미달 완료(0.65 한 건)는 '이전 완료 근거'가 아니다
    signals = [sig("완료", 0.65, date(2025, 5, 15), i=1), sig("새로운문제", 0.85, date(2026, 7, 12), i=2)]
    j = judge(signals, [promise()], [date(2025, 5, 15), date(2026, 7, 12)], TODAY, TH)
    assert j.status == STATUS_NEW_PROBLEM
    assert j.prior_done_signal_ids == [] and all("role" not in e for e in j.evidence)


def test_completion_after_new_problem_returns_to_done():
    signals = [sig("새로운문제", 0.85, date(2026, 3, 1), i=1), sig("완료", 0.9, date(2026, 7, 12), i=2)]
    j = judge(signals, [promise()], [date(2026, 3, 1), date(2026, 7, 12)], TODAY, TH)
    assert j.status == STATUS_DONE and j.prior_done_signal_ids == []


def test_r2_single_strong_completion():
    j = judge([sig("완료", 0.9, date(2025, 12, 22))], [promise(date(2025, 12, 31))], [date(2025, 12, 22)], TODAY, TH)
    assert j.status == STATUS_DONE and j.reason == "R2_completion_evidence"
    assert j.confidence == 0.9


def test_r2_two_weak_completions():
    signals = [sig("완료", 0.65, date(2025, 12, 1), i=1), sig("완료", 0.62, date(2025, 12, 5), i=2)]
    j = judge(signals, [promise()], [date(2025, 12, 5)], TODAY, TH)
    assert j.status == STATUS_DONE


def test_weak_single_completion_is_undetermined_not_done():
    j = judge([sig("완료", 0.65, date(2026, 8, 1))], [promise()], [date(2026, 8, 1)], TODAY, TH)
    assert j.status == STATUS_UNDETERMINED
    assert "0.65" in j.unresolved_reason


def test_r3_progress_even_if_deadline_passed():
    j = judge([sig("진행", 0.78, date(2026, 4, 15))], [promise(date(2026, 3, 31))], [date(2026, 4, 15)], TODAY, TH)
    assert j.status == STATUS_IN_PROGRESS


def test_completion_predicted_is_not_completion():
    # '완료 예정' 은 LLM 이 진행으로 분류하므로 R2 가 아니라 R3
    j = judge([sig("진행", 0.8, date(2026, 4, 15))], [promise(date(2026, 3, 31))], [date(2026, 4, 15)], TODAY, TH)
    assert j.status != STATUS_DONE


def test_r4_no_followup():
    j = judge([], [promise(date(2026, 10, 31))], [], TODAY, TH)
    assert j.status == STATUS_NO_FOLLOWUP and j.reason == "R4_no_followup"


def test_r4_stale_followup():
    j = judge([sig("무관", 0.9, date(2026, 6, 20), matches=False)], [promise(date(2026, 10, 31))], [date(2026, 6, 20)], TODAY, TH)
    assert j.status == STATUS_NO_FOLLOWUP and j.reason == "R4_stale_followup"


def test_r4_deadline_passed_without_signals():
    j = judge([sig("무관", 0.9, date(2026, 9, 1), matches=False)], [promise(date(2026, 6, 30))], [date(2026, 9, 1)], TODAY, TH)
    assert j.status == STATUS_NO_FOLLOWUP and j.reason == "R4_deadline_passed"


def test_r5_no_trackable_promise():
    j = judge([sig("불명확", 0.5, date(2026, 8, 5), matches=False)], [promise(strength="의사표명", trackable=False)], [date(2026, 8, 5)], TODAY, TH)
    assert j.status == STATUS_UNDETERMINED
    assert "추적 가능한 약속" in j.unresolved_reason


def test_low_confidence_signals_are_ignored():
    j = judge([sig("완료", 0.4, date(2026, 9, 1))], [promise()], [date(2026, 9, 1)], TODAY, TH)
    assert j.status == STATUS_UNDETERMINED


def test_status_strings_are_only_the_five():
    from app.config import STATUSES

    assert STATUSES == ("조치 완료 근거 확인", "조치 진행 정황 확인", "새로운 문제 발생", "후속보도 부족", "판단 불가")


# ------------------------------------------------------------------ priority
def test_priority_deadline_passed_ranks_above_stale():
    a = priority.score(STATUS_NO_FOLLOWUP, deadline_passed_days=73, days_since_last_followup=275, drop_ratio=0.9, has_firm_promise=True)
    b = priority.score(STATUS_NO_FOLLOWUP, deadline_passed_days=None, days_since_last_followup=83, drop_ratio=0.9, has_firm_promise=True)
    c = priority.score(STATUS_DONE, deadline_passed_days=100, days_since_last_followup=10, drop_ratio=0.9, has_firm_promise=True)
    assert a > b > c
    assert priority.needs_check(STATUS_IN_PROGRESS, 164) is True
    assert priority.needs_check(STATUS_DONE, 100) is False
    assert priority.needs_check(STATUS_IN_PROGRESS, None) is False


# ------------------------------------------------------------------ actors
def test_public_actor_detection():
    assert is_public_actor("가온시장")
    assert is_public_actor("가온시 안전건설국장")
    assert is_public_actor("누리철도공사 관계자")
    assert is_public_actor("가온소방서장")
    assert is_public_actor("행정안전부 장관")
    assert is_public_actor("소방당국")
    assert not is_public_actor("주민 김모씨")
    assert not is_public_actor("가온건설 대표")
    assert not is_public_actor("유가족")


def test_public_actor_regressions_from_review():
    # 1-3: '이름 직함' / 직함 단독 / 소방 관계자 는 공공
    for src in ("이상민 장관", "김영환 충북지사", "소방 관계자", "안전총괄과장", "가온시 대변인", "국무총리"):
        assert is_public_actor(src), src
    # 1-3: 민간은 한 글자 접미사·별칭 부분 일치로 통과하면 안 된다
    aliases = ["가온시청", "가온시장", "가온시"]
    for src in ("시공사 관계자", "유가족협의회", "박태구 씨", "가온시 주민 김모씨", "가온시민 A씨", "남문시장 상인회", "시공사 현장소장", "가온건설 본부장"):
        assert not is_public_actor(src, aliases), src
    # 시의회는 공공이지만 지자체(가온시) 별칭으로 귀속되면 안 된다
    assert is_public_actor("가온시의회 의원")
    assert normalize_actor_org("가온시의회 의원", "가온시", aliases) == "가온시의회"


def test_actor_normalization_to_org():
    assert normalize_actor_org("가온시장 홍길동", "가온시", ["가온시청", "가온시장"]) == "가온시"
    assert normalize_actor_org("가온시청 관계자", "가온시", ["가온시청"]) == "가온시"
    assert normalize_actor_org("누리군수", "누리군") == "누리군"
    assert normalize_actor_org("행정안전부 장관") == "행정안전부"
    assert normalize_actor_org("한국철도공사 관계자") == "한국철도공사"
    assert normalize_actor_org("주민 김모씨") is None


# ------------------------------------------------------------------ 동률·중복 규칙 (ev-13, ej-17, ej-21, ej-18)
def test_same_day_done_and_problem_prefers_problem_regardless_of_order():
    d = date(2026, 7, 12)
    a = [sig("완료", 0.9, d, i=1), sig("새로운문제", 0.85, d, i=2)]
    b = [sig("새로운문제", 0.85, d, i=2), sig("완료", 0.9, d, i=1)]
    assert judge(a, [promise()], [d], TODAY, TH).status == judge(b, [promise()], [d], TODAY, TH).status == STATUS_NEW_PROBLEM


def test_two_weak_completions_with_same_sentence_count_once():
    s1 = SignalIn(id=1, news_id="n1", signal="완료", matches_promise=True, confidence=0.65, published_at=date(2026, 8, 20), evidence_span="보강 공사를 완료했다")
    s2 = SignalIn(id=2, news_id="n2", signal="완료", matches_promise=True, confidence=0.62, published_at=date(2026, 8, 20), evidence_span="보강 공사를  완료했다")
    j = judge([s1, s2], [promise()], [date(2026, 8, 20)], TODAY, TH)
    assert j.status == STATUS_UNDETERMINED  # 전재된 같은 문장 2건은 1건
    s3 = SignalIn(id=3, news_id="n3", signal="완료", matches_promise=True, confidence=0.62, published_at=date(2026, 8, 25), evidence_span="재개통했다")
    assert judge([s1, s3], [promise()], [date(2026, 8, 25)], TODAY, TH).status == STATUS_DONE


def test_weak_new_problem_gives_specific_r5_reason():
    j = judge([sig("새로운문제", 0.65, date(2026, 8, 1))], [promise()], [date(2026, 8, 1)], TODAY, TH)
    assert j.status == STATUS_UNDETERMINED and "새로운 문제" in j.unresolved_reason and j.evidence and not j.counted_signal_ids
