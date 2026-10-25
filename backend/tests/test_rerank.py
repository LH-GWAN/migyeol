"""학습 기반 재순위(pipeline/rerank.py, LINK_RERANKER) — 피처 추출 · 확률 임계 · 모델 파일 · 설정 끔 동작 · link 단계 연동."""

from __future__ import annotations

import math
import os
from datetime import date

import numpy as np
import pytest

from app.config import Settings, get_settings
from app.db.models import Article, Event, EventArticle
from app.pipeline import link as L
from app.pipeline import rerank as RR

S = Settings(_env_file=None)
_IDS = iter(range(20000, 29999))

HYUNDAI = dict(
    title="인천 동구 현대시장 화재", region="인천 동구", facility="현대시장", responsible_org="인천 동구", org_aliases=["동구청", "인천시 동구", "인천"],
    keywords=["현대시장", "전통시장 화재", "피해복구"], occurred_at=date(2023, 3, 4), promise_terms=["화재", "위해", "상인", "연소"],
    term_weights={"현대시장": 1.0, "현대": 0.759, "송림동": 0.698, "점포": 0.632, "방화": 0.338, "상인": 0.331},
)


def ev(**kw) -> Event:
    base = dict(
        id=next(_IDS), key="t", title="", incident_type="사고>산업사고", region="", facility="", responsible_org="", org_aliases=[], keywords=[],
        expansions=[], promise_terms=[], occurred_at=date(2024, 1, 1), seed_query="", query_override="", summary="", term_weights={},
        source_news_id=None,
    )
    base.update(kw)
    return Event(**base)


def art(title: str, published: date, hilight: str = "", kws: list[str] | None = None) -> Article:
    return Article(news_id=title[:20], title=title, hilight=hilight, content_200="", tms_keywords=kws or [], published_at=published, category_incident=[])


# ---------------------------------------------------------------- 피처
def test_specific_facility_separates_generic_names():
    for f in ("산사태", "집중호우", "어선", "산불", "폭설", "하천 제방", "침수 도로", "물류센터", "산사태 피해지", "임야"):
        assert not RR.specific_facility(f), f
    for f in ("135금성호", "제22서경호", "궁평2지하차도", "현대시장", "만덕~센텀 대심도 터널 공사장", "서울역"):
        assert RR.specific_facility(f), f


def test_pair_features_title_date_and_gate_reasons():
    e = ev(**HYUNDAI)
    a = art("인천 동구 현대시장 화재 복구 완료… 상인들 영업 재개", date(2024, 7, 2), hilight="현대시장 점포 복구", kws=["현대시장", "인천"])
    ok, reasons = L.gate(e, a, S)
    assert ok, reasons
    f = RR.pair_features(e, a, S, gate_reasons=reasons, cos=0.7, overlap=0.5, strong=True, other_hit=False, other_facilities=["산사태", "제22서경호"], n_candidates=100)
    assert set(f) == set(RR.FEATURES)
    assert len(RR.feature_vector(f)) == len(RR.FEATURES)
    assert f["cos"] == 0.7 and f["overlap"] == 0.5 and f["strong"] == 1.0
    assert f["t_fac"] == 1.0 and f["t_region"] == 1.0 and f["g_facility_title"] == 1.0
    days = (date(2024, 7, 2) - date(2023, 3, 4)).days
    assert f["days_log"] == pytest.approx(math.log1p(days))
    assert f["late"] == 1.0  # 발생 + 90일 창 뒤
    assert f["t_frame"] == 1.0  # 복구·완료 — 돌아보는 기사
    assert f["t_uniq_n"] >= 1.0
    # 이 사건 시설명이 있으면 '다른 시드 시설명' 은 보지 않는다 (link.assess 의 other_hit 와 같은 규칙)
    assert f["other_fac_specific"] == 0.0 and f["other_fac_generic"] == 0.0
    assert f["ev_natural"] == 0.0 and f["ev_traffic"] == 0.0
    assert f["ev_cands_log"] == pytest.approx(math.log1p(100))


def test_pair_features_other_facility_kinds_and_title_cues():
    e = ev(**HYUNDAI)
    gen = art("인천 동구 송림동 점포 화재 대책 점검… 산사태 위험도 겹쳐", date(2023, 3, 6))
    f = RR.pair_features(e, gen, S, gate_reasons=["region", "kw=2"], cos=0.5, overlap=0.3, strong=False, other_hit=True, other_facilities=["산사태", "제22서경호"])
    assert f["other_fac_generic"] == 1.0 and f["other_fac_specific"] == 0.0
    assert f["g_kw"] == 2.0 and f["g_region"] == 1.0 and f["g_facility"] == 0.0
    assert f["t_policy"] == 1.0  # 대책·점검
    assert f["late"] == 0.0 and f["days_log"] == pytest.approx(math.log1p(2))
    spec = art("제22서경호 수색 계속… 인천 동구 송림동 점포 화재", date(2023, 3, 6))
    f = RR.pair_features(e, spec, S, gate_reasons=["region", "kw=2"], cos=0.5, overlap=0.3, strong=False, other_hit=True, other_facilities=["산사태", "제22서경호"])
    assert f["other_fac_specific"] == 1.0 and f["other_fac_generic"] == 0.0


def test_text_doc_keeps_title_and_marks_hilight_tokens():
    a = art("인천 동구 현대시장 복구 완료", date(2024, 7, 2), hilight="점포 상인 영업 재개")
    doc = RR.text_doc(a).split()
    assert "현대시장" in doc and "h:점포" in doc
    assert not any(t.startswith("h:") and t[2:] == "현대시장" for t in doc)


# ---------------------------------------------------------------- 확률 임계
@pytest.mark.parametrize("p,expected", [(0.99, "link"), (0.5, "link"), (0.49, "review"), (0.2, "review"), (0.19, "drop"), (0.0, "drop")])
def test_rerank_decision_thresholds(p, expected):
    s = Settings(_env_file=None, rerank_auto=0.5, rerank_review=0.2)
    assert RR.rerank_decision(p, s) == expected


# ---------------------------------------------------------------- 모델 파일
def _tiny_bundle(path, features=None, fmt=RR.BUNDLE_FORMAT, embedding="st:jhgan/ko-sroberta-multitask"):
    import joblib

    rng = np.random.default_rng(0)
    X = rng.normal(size=(200, len(RR.FEATURES)))
    y = (X[:, 0] > 0).astype(int)
    docs = ["현대시장 복구 h:점포"] * 100 + ["날씨 특보 h:비"] * 100
    full = RR.FeatureModel(kind="avg", hgb_params=dict(max_iter=20)).fit(X, docs, y)
    folds = [RR.FeatureModel(kind="lr").fit(X[i::2], docs[i::2], y[i::2]) for i in range(2)]
    RR.save_bundle(path, full, folds, {"x-a": 0, "x-b": 1}, {"kind": "avg", "embedding": embedding})
    if features is not None or fmt != RR.BUNDLE_FORMAT:
        b = joblib.load(path)
        b["features"] = features if features is not None else b["features"]
        b["format"] = fmt
        joblib.dump(b, path)
    return path


def test_reranker_off_or_missing_model_is_previous_behaviour(tmp_path, caplog):
    assert RR.load_reranker(Settings(_env_file=None)) is None  # 기본값 none
    s = Settings(_env_file=None, link_reranker="features", rerank_model_path=str(tmp_path / "없음.joblib"))
    with caplog.at_level("WARNING", logger="migyeol.rerank"):
        assert RR.load_reranker(s) is None
    assert "모델 파일이 없다" in caplog.text


def test_bundle_roundtrip_and_crossfit(tmp_path):
    pytest.importorskip("sklearn")
    path = _tiny_bundle(tmp_path / "m.joblib")
    s = Settings(_env_file=None, link_reranker="features", rerank_model_path=str(path))
    r = RR.load_reranker(s)
    assert r is not None and len(r.fold_models) == 2
    # 학습에 쓴 사건은 그 사건을 뺀 fold 모델, 새 사건은 전체 모델
    assert r.model_for("x-b") is r.fold_models[1]
    assert r.model_for("x-a") is r.fold_models[0]
    assert r.model_for("새 사건") is r.model
    p = r.predict("x-a", [dict.fromkeys(RR.FEATURES, 0.0), {**dict.fromkeys(RR.FEATURES, 0.0), "cos": 3.0}], ["현대시장", "날씨"])
    assert p.shape == (2,) and np.all((p >= 0) & (p <= 1))
    assert "crossfit=on" in r.describe()
    off = RR.load_reranker(s.model_copy(update={"rerank_crossfit": False}))
    assert off.model_for("x-b") is off.model


@pytest.mark.parametrize("features,fmt", [(["cos", "overlap"], RR.BUNDLE_FORMAT), (None, 99)])
def test_bundle_with_other_features_or_format_is_rejected(tmp_path, caplog, features, fmt):
    pytest.importorskip("sklearn")
    path = _tiny_bundle(tmp_path / "m.joblib", features=features, fmt=fmt)
    with caplog.at_level("WARNING", logger="migyeol.rerank"):
        assert RR.load_reranker(Settings(_env_file=None, link_reranker="features", rerank_model_path=str(path))) is None
    assert "다르다" in caplog.text


# ---------------------------------------------------------------- link 단계 연동 (가상 픽스처 DB)
class StubReranker:
    """모든 대상 후보에 같은 확률 p — link 단계가 대상·결정·점수를 어떻게 쓰는지 본다."""

    def __init__(self, p: float):
        self.p = p
        self.rows = 0

    def score(self, ctx, arts, feats):
        assert all(len(RR.feature_vector(f)) == len(RR.FEATURES) for f in feats) and len(arts) == len(feats)
        self.rows += len(feats)
        return np.full(len(feats), self.p)

    def describe(self) -> str:
        return f"stub p={self.p}"


@pytest.fixture
def mock_db(tmp_path, fixtures_ready):
    from app.bigkinds import build_client
    from app.db.session import init_db, reset_engine, session_factory
    from app.llm.client import build_llm
    from app.pipeline.embeddings import build_embedding
    from app.pipeline.runner import Pipeline

    url = f"sqlite:///{tmp_path / 'rerank.db'}"
    get_settings.cache_clear()
    os.environ["DATABASE_URL"] = url
    settings = get_settings()
    reset_engine()
    init_db(url, drop=True)
    session = session_factory()()
    embedder = build_embedding(settings)
    Pipeline(session, settings, build_client(settings), build_llm(settings), embedder).run("all")
    yield session, settings, embedder
    session.close()


def _snapshot(session) -> dict:
    """(event_id, news_id) → (needs_review, link_method, link_score, link_reason, auto_decision, 고정 여부). 고정 = 관리자 확정·해제·수동."""
    session.expire_all()
    return {
        (l.event_id, l.news_id): (l.needs_review, l.link_method, l.link_score, l.link_reason, l.auto_decision, l.confirmed or l.rejected or l.link_method == "manual")
        for l in session.query(EventArticle).all()
    }


def _domain(snap: dict) -> set:
    """재순위 대상 = 고정이 아니고 재순위 전 결정이 link·review 인 연결."""
    return {k for k, v in snap.items() if not v[5] and v[4] in ("link", "review")}


def _relink(session, settings, embedder) -> list[dict]:
    events = session.query(Event).filter(Event.active.is_(True)).all()
    return L.link_all(session, events, settings, embedder, all_facilities=[e.facility for e in events], all_events=events)


def test_link_all_without_model_file_matches_reranker_off(mock_db, tmp_path):
    session, settings, embedder = mock_db
    stats = _relink(session, settings, embedder)
    assert stats[0]["reranker"] == "none" and "reranked" not in stats[0]
    before = _snapshot(session)
    missing = settings.model_copy(update={"link_reranker": "features", "rerank_model_path": str(tmp_path / "없음.joblib")})
    _relink(session, missing, embedder)
    assert _snapshot(session) == before


def test_collect_pairs_domain_is_current_link_and_review_set(mock_db):
    session, settings, embedder = mock_db
    _relink(session, settings, embedder)
    rows = RR.collect_pairs(session, settings, embedder)
    links = [l for l in session.query(EventArticle).all() if l.event.active]
    expected = {(l.event.key, l.news_id) for l in links if not (l.confirmed or l.rejected or l.link_method == "manual")}
    dom = {(r["event_key"], r["news_id"]) for r in rows if r["in_domain"]}
    assert dom and dom == expected  # 지금 연결(자동·검토 대기) = 재계산한 link·review 판정 — link 단계와 학습 데이터가 같은 판정 경로
    assert all(set(RR.FEATURES) <= set(r) for r in rows if r["in_domain"])
    assert {r["base_decision"] for r in rows if r["in_domain"]} <= {"link", "review"}


@pytest.mark.parametrize("p,state", [(0.9, "link"), (0.3, "review"), (0.05, "drop")])
def test_link_all_with_reranker_decides_by_probability(mock_db, monkeypatch, p, state):
    session, settings, embedder = mock_db
    _relink(session, settings, embedder)
    base = _snapshot(session)
    base_auto = _domain(base)
    assert base_auto
    stub = StubReranker(p)
    monkeypatch.setattr(RR, "load_reranker", lambda s, name=None: stub)
    on = settings.model_copy(update={"rerank_auto": 0.5, "rerank_review": 0.2, "rerank_keep_late_facility": False})  # 분류기 결정만 본다
    stats = _relink(session, on, embedder)
    after = _snapshot(session)
    assert stub.rows == len(base_auto)  # 대상 = 재순위 전 link·review 로 판정된 후보 (decide 가 버린 후보는 보지 않는다)
    assert stats[0]["reranker"] == f"stub p={p}" and sum(s["reranked"]["scored"] for s in stats) == len(base_auto)
    assert {k: v for k, v in after.items() if v[5]} == {k: v for k, v in base.items() if v[5]}  # 관리자·수동 연결은 그대로
    changed = {k: v for k, v in after.items() if not v[5]}
    if state == "drop":
        assert not changed  # 확률이 RERANK_REVIEW 미만이면 자동 연결·검토 큐 모두 지운다
        return
    assert set(changed) == base_auto
    for needs_review, method, score, reason, decision, _ in changed.values():
        assert decision == state and score == p and f"p={p:.2f}" in reason
        if state == "review":
            assert needs_review and method == "embedding"
        else:
            assert needs_review == ("multi-event" in reason)  # 여러 사건에 자동 연결되면 확률이 가장 높은 사건만 남는다


def test_link_all_with_reranker_keeps_rule_method_only_for_kept_rule_links(mock_db, monkeypatch):
    session, settings, embedder = mock_db
    _relink(session, settings, embedder)
    base = _snapshot(session)
    monkeypatch.setattr(RR, "load_reranker", lambda s, name=None: StubReranker(0.9))
    _relink(session, settings.model_copy(update={"rerank_auto": 0.5, "rerank_review": 0.2}), embedder)
    after = _snapshot(session)
    dom = _domain(base)
    assert any(base[k][1] == "rule" for k in dom) and any(base[k][4] == "review" for k in dom)
    for k in dom:
        was = base[k]
        assert after[k][1] == ("rule" if was[1] == "rule" and was[4] == "link" else "embedding")


def test_late_facility_title_links_are_kept_as_floor(mock_db, monkeypatch):
    """창 뒤 자동 연결 + 제목에 사건 시설명 → 분류기 확률이 낮아도 자동 연결로 남긴다 (몇 년 뒤 후속 보호). 나머지는 폐기."""
    session, settings, embedder = mock_db
    _relink(session, settings, embedder)
    rows = RR.collect_pairs(session, settings, embedder)
    ids = {e.key: e.id for e in session.query(Event).all()}
    floor = {(ids[r["event_key"]], r["news_id"]) for r in rows if r["in_domain"] and r["base_decision"] == "link" and r["late"] > 0 and r["t_fac"] > 0}
    assert floor, "가상 픽스처에 창 뒤·제목 시설명 자동 연결이 있어야 한다"
    monkeypatch.setattr(RR, "load_reranker", lambda s, name=None: StubReranker(0.05))
    stats = _relink(session, settings.model_copy(update={"rerank_auto": 0.5, "rerank_review": 0.2, "rerank_keep_late_facility": True}), embedder)
    after = _snapshot(session)
    kept = {k for k, v in after.items() if not v[5]}
    assert kept == floor
    assert all("keep-late-facility" in after[k][3] and after[k][4] == "link" for k in kept)
    assert sum(s["reranked"]["kept_late_facility"] for s in stats) == len(floor)


def test_bundle_trained_on_other_embedding_is_not_used(tmp_path):
    """코사인 피처는 임베딩마다 분포가 다르다 — st 로 학습한 모델은 hash 임베딩(가상 픽스처)에서 쓰지 않는다."""
    pytest.importorskip("sklearn")
    path = _tiny_bundle(tmp_path / "m.joblib")
    s = Settings(_env_file=None, link_reranker="features", rerank_model_path=str(path))
    assert RR.load_reranker(s, "hash:4096") is None
    assert RR.load_reranker(s, "st:jhgan/ko-sroberta-multitask") is not None
    assert RR.load_reranker(s) is not None  # 이름을 모르면 막지 않는다


# ---------------------------------------------------------------- 교차 인코더 (LINK_RERANKER=cross)
@pytest.mark.parametrize("days,label", [(None, "[날짜 없음]"), (-1, "[발생 전]"), (0, "[당일]"), (7, "[1주 안]"), (30, "[1달 안]"), (90, "[3달 안]"), (365, "[1년 안]"), (366, "[1년 뒤]")])
def test_date_bucket(days, label):
    assert RR.date_bucket(days) == label


def test_cross_texts_match_training_format():
    e = ev(**HYUNDAI)
    a = art("인천 동구 현대시장 화재 복구 완료", date(2024, 7, 2), hilight="점포 상인 영업 재개")
    left, right = RR.cross_texts(e, a)
    assert left == RR.event_text(e) and left.startswith("인천 동구 현대시장 화재 · 인천 동구 · 현대시장")
    assert right == "[1년 뒤] 인천 동구 현대시장 화재 복구 완료 점포 상인 영업 재개"


class _Ctx:
    def __init__(self, key):
        self.event = ev(**{**HYUNDAI, "key": key})


def test_cross_reranker_uses_crossfit_cache_then_model_and_blends(monkeypatch):
    a1 = art("현대시장 복구 완료", date(2024, 7, 2))
    a2 = art("현대시장 상인 지원", date(2024, 7, 3))
    cr = RR.CrossReranker(model_dir=RR.MODELS_DIR / "없음", cache={("k", a1.news_id): 0.8}, crossfit=True)
    calls = []
    monkeypatch.setattr(cr, "infer", lambda pairs: calls.append(pairs) or np.full(len(pairs), 0.3))
    feats = [dict.fromkeys(RR.FEATURES, 0.0)] * 2
    p = cr.score(_Ctx("k"), [a1, a2], feats)
    assert list(p) == [0.8, 0.3] and len(calls) == 1 and len(calls[0]) == 1  # 캐시에 없는 쌍만 모델로
    assert cr.used == {"cache": 1, "model": 1}
    cr.crossfit = False  # 교차 적합을 끄면 캐시를 쓰지 않는다
    assert list(cr.score(_Ctx("k"), [a1], feats[:1])) == [0.3]
    cr.crossfit, cr.features, cr.blend = True, StubFeatures(0.2), 0.5
    assert list(cr.score(_Ctx("k"), [a1], feats[:1])) == pytest.approx([0.5])  # 0.5·0.2 + 0.5·0.8


class StubFeatures:
    def __init__(self, p):
        self.p = p

    def score(self, ctx, arts, feats):
        return np.full(len(arts), self.p)


def test_cross_reranker_without_model_dir_falls_back_to_features_then_off(tmp_path, caplog):
    """교차 인코더(로컬 학습, gitignore)가 없으면 피처 분류기로, 피처 분류기도 없으면 이전 동작."""
    pytest.importorskip("sklearn")
    s = Settings(_env_file=None, link_reranker="cross", rerank_cross_dir=str(tmp_path / "없음"), rerank_model_path=str(tmp_path / "없음.joblib"))
    with caplog.at_level("WARNING", logger="migyeol.rerank"):
        assert RR.load_reranker(s) is None
    assert "교차 인코더가 없다" in caplog.text
    bundle = _tiny_bundle(tmp_path / "f.joblib")
    r = RR.load_reranker(s.model_copy(update={"rerank_model_path": str(bundle)}), "st:jhgan/ko-sroberta-multitask")
    assert isinstance(r, RR.Reranker)


def test_cross_reranker_reads_oof_cache_and_respects_training_embedding(tmp_path):
    """모델 디렉터리(config.json·meta.json·oof_scores.csv)만 읽는다 — 가중치는 첫 추론 때 읽는다."""
    import json

    pytest.importorskip("transformers")
    d = tmp_path / "cross"
    d.mkdir()
    (d / "config.json").write_text("{}", encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({"embedding": "st:jhgan/ko-sroberta-multitask", "max_len": 96}), encoding="utf-8")
    (d / "oof_scores.csv").write_text("event_key,news_id,fold,label,p\nk,n1,0,O,0.91\nk,n2,0,X,0.07\n", encoding="utf-8")
    s = Settings(_env_file=None, link_reranker="cross", rerank_cross_dir=str(d), rerank_blend=0.0)
    assert RR.load_reranker(s, "hash:4096") is None  # 가상 픽스처(hash)에서는 쓰지 않는다
    r = RR.load_reranker(s, "st:jhgan/ko-sroberta-multitask")
    assert isinstance(r, RR.CrossReranker) and r.cache == {("k", "n1"): 0.91, ("k", "n2"): 0.07} and r.max_len == 96 and r._model is None
    assert r.features is None and r.blend == 0.0  # RERANK_BLEND=0 이면 피처 분류기를 읽지 않는다
    # 기본 RERANK_BLEND=0.5 — 피처 분류기 묶음을 함께 읽어 섞는다 (묶음이 없으면 교차 인코더만)
    pytest.importorskip("sklearn")
    bundle = _tiny_bundle(tmp_path / "f.joblib")
    blended = RR.load_reranker(s.model_copy(update={"rerank_blend": 0.5, "rerank_model_path": str(bundle)}), "st:jhgan/ko-sroberta-multitask")
    assert isinstance(blended.features, RR.Reranker) and blended.blend == 0.5 and "features×0.5" in blended.describe()
    alone = RR.load_reranker(s.model_copy(update={"rerank_blend": 0.5, "rerank_model_path": str(tmp_path / "없음.joblib")}), "st:jhgan/ko-sroberta-multitask")
    assert alone.features is None and alone.blend == 0.0
