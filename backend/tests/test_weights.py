"""사건별 단어 가중치(rules/weights.py) · 가중치 게이트(link.decide) · 규칙 후속 신호의 문장 선택(rules-v2)."""

from __future__ import annotations

from collections import Counter

import pytest

from app.config import get_settings
from app.llm.client import validate_span
from app.llm.rules_adapter import RulesLLM
from app.llm.schemas import FollowupInput, PromiseRef
from app.pipeline.link import decide
from app.rules import weights as W


def _stats(docs: list[str], n_filler: int = 200) -> W.CorpusStats:
    """코퍼스: 주어진 문서 + 일반 재난 기사 n_filler 건 (일반어 DF 를 높인다)."""
    rows = [(d, "", []) for d in docs]
    rows += [(f"{i}번째 호우 피해 지역 사고 점검 안전 대책 마련 주민", "가온시 호우 피해 복구 지원", []) for i in range(n_filler)]
    return W.corpus_stats(rows)


CORE = [
    "가온시 달빛지하차도 침수 배수펌프 용량 부족",
    "달빛지하차도 배수펌프 고장 침수 사고 가온시",
    "가온시 달빛지하차도 차단기 미작동 침수",
]


@pytest.fixture(scope="module")
def weights() -> dict[str, float]:
    stats = _stats(CORE)
    docs = [W.text_tokens(d) for d in CORE]
    return W.build_weights(docs, stats, seeds=W.seed_terms("달빛지하차도", ["지하차도 침수"], '"달빛지하차도" AND "가온시"'), regions=W.region_terms("가온시"))


def test_norm_token_strips_particles():
    assert W.norm_token("배수펌프를") == "배수펌프"
    assert W.norm_token("달빛지하차도") == "달빛지하차도"  # '…도' 는 명사 끝
    assert W.norm_token("3명") is None and W.norm_token("를") is None
    assert {"통복", "터널", "사고"} <= W.article_tokens("", "", ["통복 터널 사고"])


def test_unique_terms_high_generic_and_region_capped(weights):
    assert weights["달빛지하차도"] >= W.FLOOR  # 시설명 = 시드 고유어 하한
    assert weights["배수펌프"] > 0.5  # 핵심 문서 3건 중 2건, 코퍼스에서 드물다
    for generic in ("가온시", "가온", "침수", "사고"):
        assert weights.get(generic, 0.0) <= W.CAP  # 지역명·일반 재난어는 상한
    assert max(weights.values()) == pytest.approx(1.0)


def test_seed_floor_skips_common_words():
    stats = _stats(["임야 산불"], n_filler=0)
    stats.df.update({"임야": 10_000})
    stats.n_docs += 10_000
    w = W.build_weights([W.text_tokens("순천 임야 산불 월등면")], stats, seeds={"임야"}, regions=W.region_terms("순천시"))
    assert w.get("임야", 0.0) < W.FLOOR  # 코퍼스에서 흔한 시설명은 하한을 받지 않는다


def test_weighted_overlap_prefers_event_specific_article(weights):
    same = "달빛지하차도 배수펌프 증설 착공"
    other = "가온시 호우 침수 피해 사고 점검"  # 같은 지역·재난어만 겹치는 다른 기사
    assert W.weighted_overlap(weights, same) > 0.5
    assert W.weighted_overlap(weights, other) < 0.1
    assert W.weighted_overlap({}, same) == 0.0


def test_sentence_weight(weights):
    assert W.sentence_weight(weights, "달빛지하차도 차단기 설치 완료") >= 0.9
    assert W.sentence_weight(weights, "가온시 축제 개막") <= W.CAP


def _settings(**kw):
    return get_settings().model_copy(update={"link_auto": 0.55, "link_review": 0.40, **kw})


def test_decide_without_weights_is_previous_behavior():
    s = _settings(weight_min=0.0, weight_alpha=0.0, weight_rule_min=0.0)
    assert decide(True, False, 0.1, 0.0, s)[:2] == ("link", "rule")
    assert decide(False, False, 0.6, 0.0, s)[:2] == ("link", "embedding")
    assert decide(False, False, 0.45, 0.0, s)[0] == "review"
    assert decide(False, False, 0.3, 0.0, s)[0] == "drop"
    assert decide(False, True, 0.7, 0.0, s)[0] == "review"  # 다른 사건 시설명
    assert decide(False, False, 0.6, 0.0, s)[2] == pytest.approx(0.6)


def test_decide_weight_gate_and_blend():
    s = _settings(weight_min=0.2, weight_alpha=0.5, weight_rule_min=0.0)
    d, _, score, notes = decide(False, False, 0.9, 0.1, s)
    assert d == "drop" and "weight-low" in notes  # 임베딩이 높아도 사건 고유어가 없으면 게이트 탈락
    d, m, score, _ = decide(False, False, 0.5, 0.7, s)
    assert (d, m) == ("link", "embedding") and score == pytest.approx(0.6)  # (0.5+0.7)/2
    assert decide(True, False, 0.2, 0.0, s)[:2] == ("link", "rule")  # 강한 규칙은 WEIGHT_MIN 을 보지 않는다
    s2 = _settings(weight_min=0.2, weight_alpha=0.5, weight_rule_min=0.1)
    d, _, _, notes = decide(True, False, 0.2, 0.05, s2)
    assert d == "drop" and "rule-weight-low" in notes


def _inp(title: str, hilight: str = "", tw: dict | None = None) -> FollowupInput:
    return FollowupInput(
        event_summary="",
        promises=[PromiseRef(id=7, actor_org="가온시", action="달빛지하차도 배수펌프 용량을 증설한다")],
        article_title=title,
        hilight=hilight,
        published_at="2026-06-20",
        term_weights=tw or {},
    )


def test_title_counts_as_sentence_without_promise_overlap(weights):
    # 약속 행동 키워드(달빛지하차도·배수펌프·증설)는 없지만 사건 고유어(차단기)가 무거운 제목 — 이전 규칙은 '불명확'
    tw = {**weights, "차단기": 0.9}
    inp = _inp("가온시 차단기 교체 완료했다", "가온시는 여름 축제를 준비하고 있다.", tw)
    r = validate_span(RulesLLM().classify_followup(inp), inp)
    assert r.signal == "완료" and r.promise_id == 7 and r.matches_promise
    assert r.evidence_span == "가온시 차단기 교체 완료했다"
    assert validate_span(RulesLLM().classify_followup(_inp(inp.article_title, inp.hilight)), inp).signal == "불명확"  # 가중치 없으면 이전과 같다


def test_unrelated_sentence_not_selected(weights):
    # '완료' 표현은 무관한 문장(다른 시설)에만 있다 — 상위 1문장만 보면 그 문장은 뽑히지 않는다
    s = get_settings().model_copy(update={"followup_top_sentences": 1})
    inp = _inp("달빛지하차도 배수펌프 증설 논의", "한편 시립도서관 리모델링 공사는 지난달 완료했다.", weights)
    r = validate_span(RulesLLM(s).classify_followup(inp), inp)
    assert r.signal == "불명확" and not r.matches_promise


def test_confidence_reflects_relevance(weights):
    hi = _inp("달빛지하차도 배수펌프 증설 공사 마쳤다", "", weights)
    lo = _inp("배수펌프 증설 공사 마쳤다", "", weights)  # 같은 표현, 사건 고유어(시설명) 없음
    r_hi = validate_span(RulesLLM().classify_followup(hi), hi)
    r_lo = validate_span(RulesLLM().classify_followup(lo), lo)
    assert r_hi.signal == r_lo.signal == "완료"
    assert r_hi.confidence > r_lo.confidence
    assert len(r_hi.evidence_span) <= 100


def test_term_weights_not_sent_to_llm(weights):
    assert "term_weights" not in _inp("제목", "", weights).model_dump_json()


def test_corpus_df_counts_documents_once():
    st = W.corpus_stats([("배수펌프 배수펌프", "배수펌프", ["배수펌프"]), ("다른 기사", "", [])])
    assert st.df == Counter({"배수펌프": 1, "다른": 1, "기사": 1})


def test_label_scores_and_unlinked_rows_excluded(tmp_path):
    import csv

    from app.experiment import label_eval as LE
    from app.experiment.labels import CLUSTER_COLS, CLUSTER_UNLINKED, EXPERIMENT_COLS, label_metrics

    cl = {("e1", "a"): True, ("e1", "b"): False, ("e2", "c"): True, ("e2", "d"): False}
    s = LE.cluster_scores({("e1", "a"), ("e2", "c"), ("e2", "d")}, cl)
    assert s["cluster_precision"] == pytest.approx(0.667, abs=1e-3) and s["o_retained"] == 1.0 and s["x_removed"] == 0.5
    f = LE.followup_scores({"e1": True, "e2": True, "e3": False}, {"e1": True, "e2": False, "e3": True})
    assert (f["tp"], f["fp"], f["fn"], f["followup_precision"], f["followup_recall"]) == (1, 1, 1, 0.5, 0.5)

    def write(name, cols, rows):
        with (tmp_path / name).open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)

    write("cluster_sample.csv", CLUSTER_COLS, [
        {"사건키": "e1", "news_id": "a", "자동판단": "rule", "사람_OK(O/X)": "O"},
        {"사건키": "e1", "news_id": "b", "자동판단": CLUSTER_UNLINKED, "사람_OK(O/X)": "X"},  # 빠진 연결은 분모에서 제외
    ])
    write("experiment_labels.csv", EXPERIMENT_COLS, [{"사건키": "e1", "시스템_후속보도": "있음", "사람_후속보도(O/X)": "O"}])
    write("org_sample.csv", ["원문", "출처", "사람_OK(O/X)"], [])
    assert label_metrics(tmp_path)["cluster_precision"] == 1.0
