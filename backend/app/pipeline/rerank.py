"""학습 기반 재순위 (LINK_RERANKER) — 게이트를 통과한 후보에 분류기 확률을 매겨 자동 연결·검토 큐·폐기를 다시 정한다.

게이트(link.gate·scope_checks)·단어 가중치(rules/weights)·link.decide 는 그대로 두고, 그 위에서 '같은 사건 기사일 확률' p 를 낸다.
라벨은 gold/cluster_full.csv(Claude 에이전트 라벨, 사람 라벨 아님), 사건 단위 5-fold 교차검증으로 고르고 쟀다.
학습·재현: scripts/train_reranker.py · scripts/train_cross_encoder.py, 결과: docs/EXPERIMENT.md §9.

  LINK_RERANKER=none      이전 동작 (link.decide 의 임계값) — 기본값
  LINK_RERANKER=features  피처 분류기: 판정 재료·날짜·제목/하이라이트의 사건 단어·보도 성격·사건 메타 53개 + 제목·하이라이트 토큰 해싱
                          → 로지스틱 회귀와 그래디언트 부스팅의 확률 평균 (backend/models/reranker_features.joblib, scikit-learn)
  LINK_RERANKER=cross     교차 인코더(ko-sroberta 미세조정, backend/models/cross_encoder/ — gitignore, 직접 학습) 확률을
                          RERANK_BLEND 비율로 피처 분류기 확률과 섞는다 (기본 0.5 = 교차검증 최고)

  대상 = 게이트 통과 AND link.decide 가 link·review 인 후보 (학습 라벨이 있는 범위와 같다. decide 가 버린 후보는 그대로 버린다)
  p ≥ RERANK_AUTO → 자동 연결 · p ≥ RERANK_REVIEW → 검토 큐 · 미만 → 폐기. 강한 규칙(rule) 연결도 p 로 다시 정한다
  (강한 규칙으로 붙던 같은 시설의 다른 시기·전국 일반 기사가 남은 오염의 절반이다). x- 탈락·관리자 확정·원천 기사 고정은 그대로.
  바닥(RERANK_KEEP_LATE_FACILITY): 창 뒤 자동 연결 중 제목에 사건 시설명이 있는 기사는 내리지 않는다 — 몇 년 뒤 후속(만덕~센텀 2026
  개통·지반 침하)을 분류기는 다른 사건에서 배운 '늦은 같은 장소 기사 = 다른 사고' 와 구별하지 못한다.
  RERANK_CROSSFIT=true 면 학습에 쓴 사건은 그 사건을 뺀 fold 모델(교차 인코더는 교차검증 확률)로 점수를 매긴다 — 실험 DB 의 전후 수치가
  학습 데이터 재사용으로 부풀지 않게. 학습에 없던 새 사건은 전체 모델.
  모델을 학습한 임베딩과 지금 임베딩이 다르면(가상 픽스처의 hash) 쓰지 않는다. 모델·의존성이 없으면 경고 뒤 이전 동작.

피처는 모두 숫자이고 이름·순서는 FEATURES 로 고정한다(모델 파일에 함께 저장해 다르면 거부). 기사 텍스트는 API 반환 범위
(제목·하이라이트 200자·tms 키워드)만 쓴다.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

from ..config import BACKEND_DIR, Settings
from ..db.models import Article, Event, EventCandidate
from ..rules import weights as W
from . import link as L

log = logging.getLogger("migyeol.rerank")

MODELS_DIR = BACKEND_DIR / "models"
DEFAULT_MODEL = MODELS_DIR / "reranker_features.joblib"
BUNDLE_FORMAT = 1

FEATURES: tuple[str, ...] = (
    # 연결 점수 재료 (link.decide 와 같은 값)
    "cos", "overlap", "strong", "other_hit", "other_fac_specific", "other_fac_generic", "promise_cand",
    # 게이트 사유
    "g_region", "g_facility", "g_facility_title", "g_org", "g_incident", "g_seed_kw", "g_kw", "g_promise_kw",
    # 기간 창·장소 조건의 검토 큐 상한 (x- 탈락은 대상 밖)
    "cap_recur", "cap_place",
    # 날짜 (부호 있는 log1p 일수)
    "days_log", "late", "days_after_end_log", "src_days_log",
    # 제목·하이라이트·키워드의 사건 단어
    "t_overlap", "h_overlap", "k_overlap", "t_uniq_n", "th_uniq_n", "t_uniq_max", "t_weight_max", "n_hit_terms",
    "t_region", "h_region", "t_fac", "h_fac", "t_allowed_region", "t_other_region",
    # 제목의 보도 성격
    "t_new_incident", "t_frame", "t_action", "t_incident_word", "t_event_incident", "t_policy", "t_nationwide", "t_weather", "t_politics",
    "t_len", "t_ntok", "t_quote",
    # 사건 메타
    "wide", "ev_natural", "ev_traffic", "ev_cands_log", "t_promise_hits", "head_promise_hits",
)

# 제목의 보도 성격 — 남은 오염의 다수인 '예방·정책 일반'·'전국 재난 일반'·'무관(정치·날씨)' 기사의 단서. 가중치는 분류기가 정한다
_POLICY_RE = re.compile(r"(대책|점검|예방|안전관리|제도|법안|개정|입법|예산|국비|국회|국감|국정감사|토론회|간담회|정책|훈련|캠페인|교육|매뉴얼|가이드라인|종합계획)")
_NATION_RE = re.compile(r"(전국|곳곳|각지|전역|정부|중대본|행안부|행정안전부|국토부|국토교통부|소방청|산림청|기상청|총리|대통령)")
_WEATHER_RE = re.compile(r"(날씨|기상|예보|특보|주의보|경보|강수|장맛비|장마|폭염|한파|북상|비\s?소식|눈\s?소식)")
_POLITICS_RE = re.compile(r"(선거|후보|공천|당선|지방선거|총선|대선|여야|민주당|국민의힘|정의당|시정|도정|군정|취임|인사)")
_QUOTES = ("“", '"', "'", "‘")
_LEADING_NUM_RE = re.compile(r"(?<!\S)\d+")


def _log_days(d: int | None) -> float:
    if d is None:
        return 0.0
    return math.copysign(math.log1p(abs(d)), d)


def specific_facility(fac: str) -> bool:
    """시설명이 특정 장소를 가리키나 — 일반어로만 된 시설명(산사태·집중호우·어선·산불·폭설·하천 제방·침수 도로 …)은 아니다."""
    toks = W.text_tokens(_LEADING_NUM_RE.sub("", fac or ""))
    return any(t not in L._GENERIC_PLACE and not W.is_generic(t, set()) for t in toks)


def text_doc(article: Article) -> str:
    """로지스틱 회귀의 해싱 입력 — 제목 토큰(link._title_tokens 와 같은 규칙) + 'h:' 를 붙인 하이라이트 토큰."""
    title = sorted(L._title_tokens(article.title or ""))
    hil = sorted("h:" + t for t in W.text_tokens(article.hilight or ""))
    return " ".join([*title, *hil])


def pair_features(
    event: Event,
    article: Article,
    settings: Settings,
    *,
    gate_reasons: list[str],
    cos: float,
    overlap: float,
    strong: bool,
    other_hit: bool,
    promise_candidate: bool = False,
    source_date: date | None = None,
    other_facilities: list[str] | tuple[str, ...] = (),
    n_candidates: int = 0,
) -> dict[str, float]:
    """사건–기사 한 쌍의 피처. gate_reasons = link.gate 가 돌려준 사유(기간 창·지역 조건 포함, decide 의 덧붙임 제외)."""
    sc = L.event_scope(event, settings, source_date)
    title = article.title or ""
    hil = article.hilight or ""
    kws = " ".join(article.tms_keywords or [])
    head = " ".join([title, hil, kws])
    th = title + " " + hil
    text = L.article_text(article)
    weights = event.term_weights or {}
    pub = article.published_at
    days = (pub - event.occurred_at).days if pub is not None else None
    after_end = (pub - sc.early_end).days if (pub is not None and sc.early_end is not None) else None
    src_days = (pub - source_date).days if (pub is not None and source_date is not None) else None
    t_hits = [v for t, v in weights.items() if t in title]
    uniq_t = [u for u in sc.unique if u in title]
    uniq_th = [u for u in sc.unique if u in th]
    promise_kws = [k for k in dict.fromkeys(event.promise_terms or []) if len(k) >= 2 and not W.is_generic(k, set())]
    own_facility = "facility" in gate_reasons
    hit_others = [f for f in other_facilities if f and f in text] if not own_facility else []
    itype = event.incident_type or ""

    def g(name: str) -> float:
        for r in gate_reasons:
            if r == name:
                return 1.0
            if r.startswith(name + "="):
                try:
                    return float(r.split("=", 1)[1])
                except ValueError:
                    return 1.0
        return 0.0

    return {
        "cos": float(cos), "overlap": float(overlap), "strong": float(strong), "other_hit": float(other_hit),
        "other_fac_specific": float(any(specific_facility(f) for f in hit_others)),
        "other_fac_generic": float(any(not specific_facility(f) for f in hit_others)),
        "promise_cand": float(promise_candidate),
        "g_region": g("region"), "g_facility": g("facility"), "g_facility_title": g("facility-title"), "g_org": g("org"),
        "g_incident": g("incident"), "g_seed_kw": g("seed-kw"), "g_kw": g("kw"), "g_promise_kw": g("promise-kw"),
        "cap_recur": float("cap-recur" in gate_reasons), "cap_place": float(any(r.startswith("cap-place") for r in gate_reasons)),
        "days_log": _log_days(days), "late": float(after_end is not None and after_end > 0),
        "days_after_end_log": _log_days(after_end), "src_days_log": _log_days(src_days),
        "t_overlap": W.weighted_overlap(weights, title), "h_overlap": W.weighted_overlap(weights, hil), "k_overlap": W.weighted_overlap(weights, kws),
        "t_uniq_n": float(len(uniq_t)), "th_uniq_n": float(len(uniq_th)),
        "t_uniq_max": max((weights.get(u, 0.6) for u in uniq_t), default=0.0), "t_weight_max": max(t_hits, default=0.0),
        "n_hit_terms": float(sum(1 for t in weights if t in head)),
        "t_region": float(any(t in title for t in sc.region_terms)), "h_region": float(any(t in head for t in sc.region_terms)),
        "t_fac": float(any(x in title for x in sc.facility_terms)), "h_fac": float(any(x in head for x in sc.facility_terms)),
        "t_allowed_region": float(any(t in title for t in sc.allowed)), "t_other_region": float(L.other_region_in_title(sc, title) is not None),
        "t_new_incident": float(L.new_incident_title(title)), "t_frame": float(bool(L._FOLLOWUP_FRAME_RE.search(title))),
        "t_action": float(bool(L._ACTION_RE.search(title))), "t_incident_word": float(sum(1 for w in L._INCIDENT_WORDS if w in title)),
        "t_event_incident": float(any(w in title for w in sc.incident_words)),
        "t_policy": float(bool(_POLICY_RE.search(title))), "t_nationwide": float(bool(_NATION_RE.search(title))),
        "t_weather": float(bool(_WEATHER_RE.search(title))), "t_politics": float(bool(_POLITICS_RE.search(title))),
        "t_len": float(len(title)), "t_ntok": float(len(L._title_tokens(title))), "t_quote": float(any(q in title for q in _QUOTES)),
        "wide": float(sc.wide), "ev_natural": float(itype.startswith("재해>")), "ev_traffic": float(itype.startswith("사고>교통사고")),
        "ev_cands_log": math.log1p(max(0, n_candidates)),
        "t_promise_hits": float(sum(1 for k in promise_kws if k in title)), "head_promise_hits": float(sum(1 for k in promise_kws if k in head)),
    }


def assessment_features(ctx: L.EventContext, article: Article, a: L.Assessment, settings: Settings) -> dict[str, float]:
    """link.assess 결과 → 피처 (link 단계와 학습 스크립트가 같은 경로를 쓴다)."""
    return pair_features(
        ctx.event, article, settings, gate_reasons=a.gate_reasons, cos=a.cos, overlap=a.overlap, strong=a.strong, other_hit=a.other_hit,
        promise_candidate=a.promise_candidate, source_date=ctx.src_date, other_facilities=ctx.others, n_candidates=ctx.n_candidates,
    )


def feature_vector(f: dict[str, float]) -> list[float]:
    return [float(f.get(n, 0.0)) for n in FEATURES]


# ------------------------------------------------------------------ 모델
class FeatureModel:
    """피처 분류기: 'hgb' 숫자 피처 그래디언트 부스팅 · 'lr' 숫자 피처(표준화) + 제목·하이라이트 토큰 해싱 로지스틱 회귀 · 'avg' 두 확률의 평균.

    scikit-learn 은 fit/predict 안에서만 불러온다 (선택 의존성 `pip install -e ".[rerank]"`).
    """

    def __init__(self, kind: str = "avg", n_hash: int = 2**15, lr_c: float = 1.0, hgb_params: dict[str, Any] | None = None, seed: int = 0):
        if kind not in ("hgb", "lr", "avg"):
            raise ValueError(f"kind={kind}")
        self.kind, self.n_hash, self.lr_c, self.seed = kind, n_hash, lr_c, seed
        self.hgb_params = hgb_params or dict(max_iter=300, learning_rate=0.06, max_leaf_nodes=31, min_samples_leaf=40, l2_regularization=1.0)
        self.hgb = self.lr = self.scaler = None

    def _hasher(self):
        from sklearn.feature_extraction.text import HashingVectorizer

        return HashingVectorizer(n_features=self.n_hash, alternate_sign=False, norm="l2", token_pattern=r"\S+", lowercase=False)

    def _lr_matrix(self, X: np.ndarray, docs: list[str]):
        """숫자 피처(표준화) | 제목 토큰 해싱 | 하이라이트('h:') 토큰 해싱 — 두 텍스트 블록을 따로 L2 정규화 (하이라이트가 제목을 묽히지 않게)."""
        from scipy.sparse import csr_matrix, hstack

        hv = self._hasher()
        title = [" ".join(w for w in d.split() if not w.startswith("h:")) for d in docs]
        hil = [" ".join(w for w in d.split() if w.startswith("h:")) for d in docs]
        return hstack([csr_matrix(self.scaler.transform(X)), hv.transform(title), hv.transform(hil)]).tocsr()

    def fit(self, X: np.ndarray, docs: list[str], y: np.ndarray) -> FeatureModel:
        X = np.asarray(X, dtype=np.float64)
        y = np.asarray(y, dtype=int)
        if self.kind in ("hgb", "avg"):
            from sklearn.ensemble import HistGradientBoostingClassifier

            self.hgb = HistGradientBoostingClassifier(random_state=self.seed, **self.hgb_params).fit(X, y)
        if self.kind in ("lr", "avg"):
            from sklearn.linear_model import LogisticRegression
            from sklearn.preprocessing import StandardScaler

            self.scaler = StandardScaler().fit(X)
            self.lr = LogisticRegression(C=self.lr_c, max_iter=3000).fit(self._lr_matrix(X, docs), y)
        return self

    def predict_proba(self, X: np.ndarray, docs: list[str]) -> np.ndarray:
        X = np.asarray(X, dtype=np.float64)
        parts = []
        if self.hgb is not None:
            parts.append(self.hgb.predict_proba(X)[:, 1])
        if self.lr is not None:
            parts.append(self.lr.predict_proba(self._lr_matrix(X, docs))[:, 1])
        return np.mean(parts, axis=0)


@dataclass
class Reranker:
    model: Any  # 전체 라벨로 학습한 모델 (predict_proba(X, docs))
    fold_models: list[Any] = field(default_factory=list)
    event_folds: dict[str, int] = field(default_factory=dict)  # 학습에 쓴 사건 → 그 사건을 뺀 fold 번호
    crossfit: bool = True
    meta: dict[str, Any] = field(default_factory=dict)
    path: str = ""

    def model_for(self, event_key: str):
        k = self.event_folds.get(event_key)
        if self.crossfit and k is not None and 0 <= k < len(self.fold_models):
            return self.fold_models[k]
        return self.model

    def predict(self, event_key: str, rows: list[dict[str, float]], docs: list[str]) -> np.ndarray:
        if not rows:
            return np.zeros(0)
        X = np.asarray([feature_vector(r) for r in rows], dtype=np.float64)
        return np.clip(self.model_for(event_key).predict_proba(X, docs), 0.0, 1.0)

    def score(self, ctx: L.EventContext, arts: list[Article], feats: list[dict[str, float]]) -> np.ndarray:
        return self.predict(ctx.event.key, feats, [text_doc(a) for a in arts])

    def describe(self) -> str:
        kind = getattr(self.model, "kind", type(self.model).__name__)
        return f"features:{kind} crossfit={'on' if self.crossfit else 'off'} folds={len(self.fold_models)} ({Path(self.path).name})"


# ------------------------------------------------------------------ 교차 인코더 (LINK_RERANKER=cross)
def date_bucket(days: int | None) -> str:
    """교차 인코더 입력의 발생 후 기간 표지."""
    if days is None:
        return "[날짜 없음]"
    if days < 0:
        return "[발생 전]"
    for lim, name in ((0, "당일"), (7, "1주 안"), (30, "1달 안"), (90, "3달 안"), (365, "1년 안")):
        if days <= lim:
            return f"[{name}]"
    return "[1년 뒤]"


def cross_texts(event: Event, article: Article) -> tuple[str, str]:
    """(사건 텍스트, [발생 후 기간] 제목 하이라이트) — scripts/train_cross_encoder.py 의 학습 입력과 같은 형식."""
    days = (article.published_at - event.occurred_at).days if article.published_at is not None else None
    return event_text(event), f"{date_bucket(days)} {article.title or ''} {article.hilight or ''}".strip()


@dataclass
class CrossReranker:
    """미세조정한 교차 인코더(models/cross_encoder/, gitignore — scripts/train_cross_encoder.py --save 로 만든다).

    RERANK_CROSSFIT 이면 교차검증 때 그 사건을 뺀 fold 모델이 낸 확률(oof_scores.csv)을 먼저 쓰고, 없는 쌍(새 사건·새 후보)만 전체 모델로 계산한다.
    features 가 있으면 피처 분류기 확률과 RERANK_BLEND 비율로 섞는다 (p = blend·피처 + (1-blend)·교차 인코더).
    """

    model_dir: Path
    cache: dict[tuple[str, str], float] = field(default_factory=dict)
    crossfit: bool = True
    max_len: int = 128
    features: Reranker | None = None
    blend: float = 0.0
    used: dict[str, int] = field(default_factory=lambda: {"cache": 0, "model": 0})
    _model: Any = None
    _tok: Any = None
    _device: Any = None

    def _load(self) -> None:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self._device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
        self._tok = AutoTokenizer.from_pretrained(self.model_dir)
        self._model = AutoModelForSequenceClassification.from_pretrained(self.model_dir).float().to(self._device).eval()  # fp16 저장본 → fp32

    def infer(self, pairs: list[tuple[str, str]], batch: int = 128) -> np.ndarray:
        import torch

        if self._model is None:
            self._load()
        out: list[float] = []
        with torch.no_grad():
            for i in range(0, len(pairs), batch):
                b = pairs[i : i + batch]
                enc = self._tok([x for x, _ in b], [y for _, y in b], truncation="only_second", max_length=self.max_len, padding=True, return_tensors="pt")
                enc.pop("token_type_ids", None)  # RoBERTa(ko-sroberta): segment 가 하나뿐
                logits = self._model(**{k: v.to(self._device) for k, v in enc.items()}).logits.squeeze(-1)
                out += torch.sigmoid(logits).float().cpu().tolist()
        return np.asarray(out, dtype=np.float64)

    def score(self, ctx: L.EventContext, arts: list[Article], feats: list[dict[str, float]]) -> np.ndarray:
        key = ctx.event.key
        p = np.zeros(len(arts))
        todo = []
        for i, a in enumerate(arts):
            c = self.cache.get((key, a.news_id)) if self.crossfit else None
            if c is None:
                todo.append(i)
            else:
                p[i] = c
        self.used["cache"] += len(arts) - len(todo)
        self.used["model"] += len(todo)
        if todo:
            p[todo] = self.infer([cross_texts(ctx.event, arts[i]) for i in todo])
        if self.features is not None and self.blend > 0:
            p = self.blend * self.features.score(ctx, arts, feats) + (1 - self.blend) * p
        return np.clip(p, 0.0, 1.0)

    def describe(self) -> str:
        blend = f" + features×{self.blend}" if self.features is not None and self.blend > 0 else ""
        return f"cross{blend} crossfit={'on' if self.crossfit else 'off'} oof={len(self.cache)} ({self.model_dir.name}) used={self.used}"


def model_path(settings: Settings) -> Path:
    if not settings.rerank_model_path:
        return DEFAULT_MODEL
    p = Path(settings.rerank_model_path)
    return p if p.is_absolute() else BACKEND_DIR / p


def save_bundle(path: Path, model: Any, fold_models: list[Any], event_folds: dict[str, int], meta: dict[str, Any]) -> None:
    import joblib

    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {"format": BUNDLE_FORMAT, "kind": "features", "features": list(FEATURES), "model": model, "fold_models": fold_models, "event_folds": event_folds, "meta": meta},
        path,
        compress=3,
    )


def _load_features(settings: Settings, embedder_name: str | None = None) -> Reranker | None:
    path = model_path(settings)
    if not path.exists():
        log.warning("LINK_RERANKER=%s 인데 모델 파일이 없다(%s) — 이전 동작. scripts/train_reranker.py 로 만든다", settings.link_reranker, path)
        return None
    try:
        import joblib

        bundle = joblib.load(path)
    except Exception as exc:  # noqa: BLE001 — 선택 의존성·버전 문제는 이전 동작으로
        log.warning("재순위 모델을 읽지 못했다(%s: %s) — 이전 동작", type(exc).__name__, exc)
        return None
    if bundle.get("format") != BUNDLE_FORMAT or tuple(bundle.get("features") or ()) != FEATURES:
        log.warning("재순위 모델의 형식·피처 목록이 코드와 다르다 — 이전 동작. scripts/train_reranker.py 로 다시 학습한다")
        return None
    trained_on = (bundle.get("meta") or {}).get("embedding")
    if embedder_name and trained_on and trained_on != embedder_name:
        # 코사인 피처는 임베딩 모델마다 분포가 다르다 — 다른 백엔드(가상 픽스처의 hash 등)에서는 쓰지 않는다
        log.info("재순위 모델은 %s 임베딩으로 학습했다(지금 %s) — 이전 동작", trained_on, embedder_name)
        return None
    return Reranker(
        model=bundle["model"], fold_models=list(bundle.get("fold_models") or []), event_folds=dict(bundle.get("event_folds") or {}),
        crossfit=settings.rerank_crossfit, meta=dict(bundle.get("meta") or {}), path=str(path),
    )


def cross_dir(settings: Settings) -> Path:
    p = Path(settings.rerank_cross_dir) if settings.rerank_cross_dir else MODELS_DIR / "cross_encoder"
    return p if p.is_absolute() else BACKEND_DIR / p


def _load_cross(settings: Settings, embedder_name: str | None = None) -> CrossReranker | None:
    import csv
    import json

    d = cross_dir(settings)
    if not (d / "config.json").exists():
        log.warning("LINK_RERANKER=cross 인데 교차 인코더가 없다(%s) — 이전 동작. scripts/train_cross_encoder.py --save 로 만든다", d)
        return None
    try:
        import transformers  # noqa: F401
    except ImportError as exc:
        log.warning("교차 인코더에 transformers 가 필요하다(%s) — 이전 동작", exc)
        return None
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8")) if (d / "meta.json").exists() else {}
    if embedder_name and meta.get("embedding") and meta["embedding"] != embedder_name:
        # 재순위 대상(link·review 판정)은 임베딩 코사인에 기대므로 학습 때와 다른 임베딩(가상 픽스처 hash)에서는 쓰지 않는다
        log.info("교차 인코더는 %s 임베딩의 판정으로 학습했다(지금 %s) — 이전 동작", meta["embedding"], embedder_name)
        return None
    cache: dict[tuple[str, str], float] = {}
    if (d / "oof_scores.csv").exists():
        with (d / "oof_scores.csv").open(encoding="utf-8", newline="") as f:
            cache = {(r["event_key"], r["news_id"]): float(r["p"]) for r in csv.DictReader(f)}
    features = _load_features(settings, embedder_name) if settings.rerank_blend > 0 else None
    return CrossReranker(model_dir=d, cache=cache, crossfit=settings.rerank_crossfit, max_len=int(meta.get("max_len", 128)), features=features, blend=settings.rerank_blend if features else 0.0)


def load_reranker(settings: Settings, embedder_name: str | None = None) -> Reranker | CrossReranker | None:
    """LINK_RERANKER=none 이면 None. 모델이 없거나 읽지 못하거나 피처 목록·학습 임베딩이 다르면 None — link 는 이전 동작으로 돈다.
    embedder_name: 지금 임베딩 백엔드 이름(EmbeddingBackend.name) — 모델을 학습한 임베딩과 다르면 쓰지 않는다."""
    if settings.link_reranker == "none":
        return None
    if settings.link_reranker == "cross":
        return _load_cross(settings, embedder_name)
    return _load_features(settings, embedder_name)


def rerank_decision(p: float, settings: Settings) -> str:
    """분류기 확률 → link | review | drop."""
    if p >= settings.rerank_auto:
        return "link"
    if p >= settings.rerank_review:
        return "review"
    return "drop"


def keep_late_facility(a: L.Assessment, feats: dict[str, float], settings: Settings) -> bool:
    """분류기가 내리지 못하는 바닥: 재순위 전 자동 연결 + 창 뒤 기사 + 제목에 사건 시설명 (RERANK_KEEP_LATE_FACILITY)."""
    return settings.rerank_keep_late_facility and a.base_decision == "link" and feats.get("late", 0.0) > 0 and feats.get("t_fac", 0.0) > 0


def apply(reranker: Reranker | CrossReranker, ctx: L.EventContext, items: list[tuple[Article, L.Assessment]], settings: Settings) -> dict[str, int]:
    """사건 하나의 후보 판정에 분류기 확률을 덧입힌다 (link·review 로 판정된 후보만, 한 번에 예측). 반환: 바뀐 결정 수."""
    target = [(art, a) for art, a in items if a.ok and a.base_decision != "drop"]
    out = {"scored": len(target), "promoted": 0, "demoted": 0, "dropped": 0, "kept_late_facility": 0}
    if not target:
        return out
    feats = [assessment_features(ctx, art, a, settings) for art, a in target]
    probs = reranker.score(ctx, [art for art, _ in target], feats)
    rank = {"drop": 0, "review": 1, "link": 2}
    for (_, a), f, p in zip(target, feats, probs, strict=True):
        a.prob = float(p)
        a.decision = rerank_decision(a.prob, settings)
        if a.decision != "link" and keep_late_facility(a, f, settings):
            a.decision = "link"
            a.notes.append("keep-late-facility")
            out["kept_late_facility"] += 1
        # 경로 표시는 그대로: 강한 규칙으로 자동 연결이던 것이 남으면 rule, 그 밖의 자동 연결·검토 큐는 embedding
        a.method = a.base_method if (a.decision == "link" and a.base_decision == "link") else "embedding"
        if a.decision == "drop":
            out["dropped"] += 1
        elif rank[a.decision] > rank[a.base_decision]:
            out["promoted"] += 1
        elif rank[a.decision] < rank[a.base_decision]:
            out["demoted"] += 1
    return out


# ------------------------------------------------------------------ 학습 데이터
def link_state(link) -> str:
    """지금 DB 의 연결 상태 — auto | review | pinned | rejected | none."""
    if link is None:
        return "none"
    if link.rejected:
        return "rejected"
    if link.confirmed or link.link_method == "manual":
        return "pinned"
    return "review" if link.needs_review else "auto"


def collect_pairs(session, settings: Settings, embedder, labels: dict[tuple[str, str], bool] | None = None) -> list[dict[str, Any]]:
    """활성 사건의 후보마다 link.assess(재순위 없이)와 피처를 계산한다 — DB 는 바꾸지 않는다.

    남기는 행: 재순위 대상(게이트 통과 AND decide 가 link·review) + 라벨이 있는 행(대상 밖이면 피처 없이 in_domain=0).
    """
    labels = labels or {}
    events = session.query(Event).filter(Event.active.is_(True)).order_by(Event.key).all()
    facilities = [e.facility for e in events]
    out: list[dict[str, Any]] = []
    for ev in events:
        ctx = L.event_context(session, ev, embedder, facilities)
        cand_ids = {c.news_id for c in session.query(EventCandidate).filter_by(event_id=ev.id).all()}
        links = {l.news_id: l for l in ev.links}
        for art in sorted(L.candidate_articles(session, ev), key=lambda a: a.news_id):
            lab = labels.get((ev.key, art.news_id))
            link = links.get(art.news_id)
            state = link_state(link)
            base = {"event_key": ev.key, "news_id": art.news_id, "label": "" if lab is None else ("O" if lab else "X"), "state": state}
            if state in ("pinned", "rejected") or (link is not None and art.news_id not in cand_ids and cand_ids):
                if lab is not None:
                    out.append({**base, "in_domain": 0, "gate_ok": 0, "base_decision": state if state in ("pinned", "rejected") else "stale"})
                continue
            a = L.assess(ctx, art, settings, embedder, store_embedding=False)
            in_domain = a.ok and a.base_decision != "drop"
            if not in_domain and lab is None:
                continue
            row = {
                **base, "in_domain": int(in_domain), "gate_ok": int(a.ok), "base_decision": a.base_decision, "base_method": a.base_method,
                "score": round(a.score, 4), "checks": "+".join(r for r in a.gate_reasons if r.startswith(("x-", "cap-"))),
                "notes": "+".join(a.notes), "title": art.title or "", "hilight": art.hilight or "",
                "published": art.published_at.isoformat() if art.published_at else "", "doc": text_doc(art),
                "days": (art.published_at - ev.occurred_at).days if art.published_at else "",
            }
            if a.ok:
                row.update(assessment_features(ctx, art, a, settings))
            out.append(row)
    return out


def event_text(event: Event) -> str:
    """교차 인코더 입력의 사건 쪽 텍스트 — 사건명 · 지역 · 시설 · 핵심어."""
    return " · ".join(x for x in [event.title or "", event.region or "", event.facility or "", " ".join((event.keywords or [])[:6])] if x)
