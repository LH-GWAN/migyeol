"""LLM_MODE=rules 어댑터 (v2 §5): 가상 픽스처 stub 정답과의 일치율 출력 + 회귀 고정.

100% 일치가 목표가 아니다. 규칙이 바뀌어 일치율이 떨어지면 알아채기 위한 하한선과, 대표 문장 판정을 고정한다.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.config import get_settings
from app.llm.client import LLMFailure, build_llm, validate_span
from app.llm.rules_adapter import RULES_MODEL, RulesLLM, parse_relative_deadline
from app.llm.schemas import FollowupInput, PromiseInput, PromiseRef

# 측정값(2026-10-22, rules-v1): 약속 여부 100%(10건) · 강도 100% · 신호 94%(18건). 하한은 회귀 감지용 — 규칙을 고치면 다시 재고 올린다.
MIN_PROMISE_AGREE = 0.9
MIN_STRENGTH_AGREE = 0.9
MIN_SIGNAL_AGREE = 0.9


class _Capture:
    """stub 정답을 파이프라인에 돌려주면서, 같은 입력으로 규칙 어댑터 결과를 함께 기록한다."""

    mode = "stub"
    model = "stub"

    def __init__(self, stub, rules: RulesLLM):
        self.stub, self.rules = stub, rules
        self.promises: list[tuple[PromiseInput, object, object]] = []
        self.signals: list[tuple[FollowupInput, object, object]] = []

    def extract_promise(self, inp, *, key=""):
        gold = self.stub.extract_promise(inp, key=key)
        self.promises.append((inp, gold, self.rules.extract_promise(inp, key=key)))
        return gold

    def classify_followup(self, inp, *, key=""):
        gold = self.stub.classify_followup(inp, key=key)
        self.signals.append((inp, validate_span(gold, inp), validate_span(self.rules.classify_followup(inp, key=key), inp)))
        return gold


@pytest.fixture(scope="module")
def captured(tmp_path_factory, fixtures_ready):
    from app.bigkinds import build_client
    from app.db.session import init_db, reset_engine, session_factory
    from app.pipeline.embeddings import build_embedding
    from app.pipeline.runner import Pipeline

    import app.pipeline.runner as runner_mod

    lock_dir = tmp_path_factory.mktemp("rules_lock")
    orig_lock = runner_mod._lock_path
    runner_mod._lock_path = lambda: lock_dir / "pipeline.lock"
    settings = get_settings()
    cap = _Capture(build_llm(settings), RulesLLM(settings))
    url = f"sqlite:///{tmp_path_factory.mktemp('rules') / 'rules.db'}"
    reset_engine()
    init_db(url, drop=True)
    session = session_factory()()
    Pipeline(session, settings, build_client(settings), cap, build_embedding(settings)).run("all")
    session.close()
    reset_engine()
    runner_mod._lock_path = orig_lock
    return cap


def _rate(pairs, fn) -> float:
    return sum(1 for p in pairs if fn(p)) / len(pairs) if pairs else 0.0


def test_agreement_with_stub_answers(captured, capsys):
    pr = captured.promises
    sg = captured.signals
    assert pr and sg
    promise_agree = _rate(pr, lambda t: t[1].is_promise == t[2].is_promise)
    strength_agree = _rate(pr, lambda t: t[1].strength == t[2].strength)
    signal_agree = _rate(sg, lambda t: t[1].signal == t[2].signal)
    valid = {"완료", "진행", "새로운문제"}
    valid_agree = _rate(sg, lambda t: (t[1].signal in valid) == (t[2].signal in valid))
    with capsys.disabled():
        print(
            f"\n[rules-v1 vs stub] 약속 여부 {promise_agree:.0%} ({len(pr)}건) · 강도 {strength_agree:.0%} · "
            f"신호 {signal_agree:.0%} ({len(sg)}건) · 유효신호 여부 {valid_agree:.0%}"
        )
        for inp, g, r in sg:
            if g.signal != r.signal:
                print(f"   신호 불일치: stub={g.signal} rules={r.signal} | {inp.article_title[:40]}")
    assert promise_agree >= MIN_PROMISE_AGREE
    assert strength_agree >= MIN_STRENGTH_AGREE
    assert signal_agree >= MIN_SIGNAL_AGREE


def test_build_llm_selects_rules():
    s = get_settings().model_copy(update={"llm_mode": "rules"})
    llm = build_llm(s)
    assert isinstance(llm, RulesLLM) and llm.mode == "rules" and llm.model == RULES_MODEL == "rules-v2"


def _p(quotation: str, published: str = "2025-07-18", source: str = "가온시장") -> PromiseInput:
    return PromiseInput(event_summary="", responsible_org="가온시", source=source, quotation=quotation, article_title="", published_at=published)


@pytest.mark.parametrize(
    "quotation,strength,deadline",
    [
        ("내년 10월까지 달빛지하차도 배수펌프 용량을 2배로 늘리겠다", "확약", "2026-10-31"),
        ("내년 10월까지 달빛지하차도 배수펌프를 증설하겠다", "확약", "2026-10-31"),
        ("지하차도 진입 차단시설을 설치하고 침수 감지 시스템도 검토하겠다", "계획", None),
        ("차단시설 설치를 검토하겠다", "의사표명", None),
        ("연내 관내 물류창고 34곳을 전수조사하겠다", "확약", "2025-12-31"),
        ("하반기까지 정밀안전진단을 마치고 보강 공사를 추진할 계획이다", "확약", "2025-12-31"),
        ("밤 10시 40분쯤 신고를 받고 출동해 고립된 15명을 모두 구조했다", "해당없음", None),
        ("유가족의 아픔에 깊이 공감하며 최선을 다하겠다", "의사표명", None),
    ],
)
def test_promise_rules(quotation, strength, deadline):
    r = RulesLLM().extract_promise(_p(quotation))
    assert r.strength == strength
    assert r.deadline_date == deadline
    if r.action:
        assert r.action in quotation and len(r.action) <= 40
    assert r.actor_org == "가온시"


@pytest.mark.parametrize(
    "raw,published,expected",
    [
        ("3월까지 보강", "2025-07-18", "2026-03-31"),
        ("9월 말까지", "2025-07-18", "2025-09-30"),
        ("올해 안에", "2024-02-01", "2024-12-31"),
        ("상반기 중", "2025-07-18", "2026-06-30"),
        ("내년 상반기까지", "2025-07-18", "2026-06-30"),
        ("30일 이내", "2025-07-01", "2025-07-31"),
        ("2026년 6월까지", "2025-07-18", "2026-06-30"),
        ("기한 없음", "2025-07-18", None),
    ],
)
def test_relative_deadline(raw, published, expected):
    _, d, _ = parse_relative_deadline(raw, date.fromisoformat(published))
    assert (d.isoformat() if d else None) == expected


def _f(title: str, hilight: str = "", quotations: list[str] | None = None) -> FollowupInput:
    return FollowupInput(
        event_summary="",
        promises=[PromiseRef(id=7, actor_org="가온시", action="달빛지하차도 배수펌프 용량을 증설한다")],
        article_title=title,
        hilight=hilight,
        quotations=quotations or [],
        published_at="2026-06-20",
    )


@pytest.mark.parametrize(
    "title,hilight,signal,conf",
    [
        ("달빛지하차도 배수펌프 증설 공사 마쳤다", "", "완료", 0.75),
        ("가온시, 배수펌프 증설 착공", "", "진행", 0.65),
        ("배수펌프 증설 공사 완료 예정", "가온시는 배수펌프 증설을 다음 달 완료할 예정이다.", "진행", 0.65),
        ("달빛지하차도 또 침수… 배수펌프 증설 지연", "", "새로운문제", 0.75),
        ("가온시 여름 축제 개막", "축제가 열렸다.", "무관", 0.6),
        ("다른 도로 공사 완료", "도심 도로 포장 공사를 마쳤다.", "불명확", 0.4),
        ("배수펌프 증설 차질없이 마무리", "가온시는 배수펌프 증설을 차질 없이 마무리했다.", "완료", 0.65),  # '차질 없이' 는 새로운 문제가 아니다
    ],
)
def test_followup_rules(title, hilight, signal, conf):
    inp = _f(title, hilight)
    r = validate_span(RulesLLM().classify_followup(inp), inp)
    assert r.signal == signal
    assert r.confidence == pytest.approx(conf)
    if signal in ("완료", "진행", "새로운문제"):
        assert r.matches_promise and r.promise_id == 7
        assert r.evidence_span and len(r.evidence_span) <= 100


def test_rules_never_raise_llm_failure():
    llm = RulesLLM()
    try:
        llm.extract_promise(_p(""))
        llm.classify_followup(FollowupInput(event_summary="", promises=[], article_title="", hilight="", published_at="2025-01-01"))
    except LLMFailure:  # pragma: no cover
        pytest.fail("rules 어댑터는 LLMFailure 를 내지 않는다")
