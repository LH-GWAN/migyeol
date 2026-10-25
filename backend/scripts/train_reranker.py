"""학습 기반 재순위(LINK_RERANKER) — 피처 추출 · 사건 단위 교차검증 비교 · 모델 학습 (docs/EXPERIMENT.md §9).

  cd backend
  ENV="BIGKINDS_MODE=record BIGKINDS_REPLAY_ONLY=true LLM_MODE=rules EMBEDDING_BACKEND=st TODAY_OVERRIDE=2026-10-22"
  env $ENV .venv/bin/python scripts/train_reranker.py --extract            # DB → data/experiment/rerank_pairs.csv (DB 불변)
  env $ENV .venv/bin/python scripts/train_reranker.py --cv [--followup] [--cross-oof data/experiment/rerank_cross_oof.csv]
                                                                           # 규칙(A)·피처 분류기(B)·교차 인코더(C) 비교 → reports/rerank_cv_<날짜>.md
  env $ENV .venv/bin/python scripts/train_reranker.py --train [--kind avg]  # fold 모델 5개 + 전체 모델 → models/reranker_features.joblib
  (플래그가 없으면 --extract --cv --train)

교차검증: 활성 사건 90건을 5개 fold 로 나눈다(라벨 행 수 균형, 결정적). 같은 사건의 기사는 학습·평가에 함께 들어가지 않는다.
라벨: gold/cluster_full.csv (Claude 에이전트 라벨, 사람 아님). 재순위 대상 = 게이트 통과 AND link.decide 가 link·review 인 후보.
지표: 정밀도 = 자동 연결된 라벨 행의 O 비율, 재현율 = 라벨 O 전체(11,315) 중 자동 연결 비율 — label_eval.full_scores 와 같은 정의.
의존성: pip install -e ".[rerank]" (scikit-learn).
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from app.config import BACKEND_DIR, get_settings  # noqa: E402
from app.experiment import label_eval as LE  # noqa: E402
from app.pipeline import link as L  # noqa: E402
from app.pipeline import rerank as RR  # noqa: E402

DATA = BACKEND_DIR / "data" / "experiment"
PAIRS = DATA / "rerank_pairs.csv"
PAIRS_META = DATA / "rerank_pairs.meta.json"  # 추출에 쓴 임베딩 — 모델 메타로 옮겨 다른 임베딩에서는 쓰지 않게 한다
OOF = DATA / "rerank_oof.csv"
GOLD = BACKEND_DIR / "gold"
N_FOLDS = 5
META_COLS = ["event_key", "news_id", "fold", "label", "reason", "state", "in_domain", "gate_ok", "base_decision", "base_method", "score",
             "checks", "notes", "published", "days"]
TEXT_COLS = ["doc", "event_text", "title", "hilight"]
THRESHOLDS = (0.3, 0.4, 0.5, 0.6, 0.7)
REASON_KO = {
    "irrelevant": "무관", "prevention_policy": "예방·정책 일반", "other_period_same_type": "다른 시기 같은 유형", "general_disaster_news": "전국 재난 일반",
    "other_incident_same_region": "같은 지역 다른 사고", "reprint_unclear": "전재·불명확", "before_event": "사건 이전",
}


# ------------------------------------------------------------------ 공통
def open_db(settings):
    from app.db.session import init_db, session_factory

    init_db()
    return session_factory()()


def load_reasons() -> dict[tuple[str, str], str]:
    with (GOLD / "cluster_full.csv").open(encoding="utf-8-sig", newline="") as f:
        return {(r["사건키"], r["news_id"]): (r.get("사유") or "").strip() for r in csv.DictReader(f)}


def assign_folds(sizes: dict[str, int], keys: list[str], k: int = N_FOLDS) -> dict[str, int]:
    """라벨 행 수가 큰 사건부터 가장 가벼운 fold 에 (GroupKFold 와 같은 균형 규칙, 결정적)."""
    loads = [0] * k
    out: dict[str, int] = {}
    for key in sorted(keys, key=lambda x: (-sizes.get(x, 0), x)):
        i = min(range(k), key=lambda j: (loads[j], j))
        out[key] = i
        loads[i] += sizes.get(key, 0)
    return out


def read_pairs(path: Path = PAIRS) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["fold"] = int(r["fold"])
        r["in_domain"] = int(r["in_domain"])
    return rows


def matrix(rows: list[dict]) -> np.ndarray:
    return np.asarray([[float(r.get(n) or 0.0) for n in RR.FEATURES] for r in rows], dtype=np.float64)


# ------------------------------------------------------------------ 1. 추출
def extract(settings) -> Path:
    from app.db.models import Article, Event, EventArticle
    from app.pipeline.embeddings import build_embedding

    t0 = time.time()
    session = open_db(settings)
    embedder = build_embedding(settings)
    labels = LE.load_full_labels(GOLD) or {}
    reasons = load_reasons()
    rows = RR.collect_pairs(session, settings, embedder, labels)
    events = {e.key: e for e in session.query(Event).filter(Event.active.is_(True)).all()}
    seen = {(r["event_key"], r["news_id"]) for r in rows}
    # 라벨은 있는데 지금 후보에도 연결에도 없는 행 (재현율 분모에는 들어간다)
    for (key, nid), lab in labels.items():
        if (key, nid) in seen or key not in events:
            continue
        link = session.get(EventArticle, (events[key].id, nid))
        a = session.get(Article, nid)
        rows.append({"event_key": key, "news_id": nid, "label": "O" if lab else "X", "state": RR.link_state(link), "in_domain": 0, "gate_ok": 0,
                     "base_decision": "absent", "title": a.title if a else "", "published": a.published_at.isoformat() if a and a.published_at else ""})
    sizes = Counter(r["event_key"] for r in rows if r["label"] and r["in_domain"])
    folds = assign_folds(sizes, sorted(events))
    texts = {k: RR.event_text(e) for k, e in events.items()}
    DATA.mkdir(parents=True, exist_ok=True)
    cols = META_COLS + list(RR.FEATURES) + TEXT_COLS
    with PAIRS.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            r["fold"] = folds.get(r["event_key"], 0)
            r["reason"] = reasons.get((r["event_key"], r["news_id"]), "")
            r["event_text"] = texts.get(r["event_key"], "")
            w.writerow(r)
    session.close()
    PAIRS_META.write_text(json.dumps({"embedding": embedder.name, "extracted_at": datetime.now().isoformat(timespec="seconds"), "rows": len(rows)}, ensure_ascii=False), encoding="utf-8")
    n_dom = sum(1 for r in rows if r["in_domain"])
    n_lab = sum(1 for r in rows if r["label"])
    print(f"추출: {len(rows)}행 (재순위 대상 {n_dom}, 라벨 {n_lab}) → {PAIRS} [{time.time() - t0:.0f}s]")
    # 판정 재현 점검: 지금 DB 상태와 link.assess(재순위 없음)의 결정이 같은가 (사건 간 배타 처리로 검토 큐가 된 것 제외)
    agree = Counter()
    for r in rows:
        if r.get("state") in ("auto", "review") and r.get("gate_ok") is not None and r["base_decision"] in ("link", "review", "drop"):
            agree[(r["state"], r["base_decision"])] += 1
    print("DB 상태 × 재계산 결정:", dict(agree))
    return PAIRS


# ------------------------------------------------------------------ 2. 비교
def scores(rows: list[dict], link: np.ndarray, base_link: np.ndarray, base_review: np.ndarray) -> dict:
    y = np.array([r["label"] == "O" for r in rows])
    lab = np.array([bool(r["label"]) for r in rows])
    n_o = int((y & lab).sum())
    kept = link & lab
    tp = int((kept & y).sum())
    n = int(kept.sum())
    p = tp / n if n else 0.0
    rc = tp / n_o if n_o else 0.0
    return {
        "n_link": n, "precision": p, "recall": rc, "f1": 2 * p * rc / (p + rc) if p + rc else 0.0,
        "rescued_o": int((link & base_review & lab & y).sum()), "promoted_x": int((link & base_review & lab & ~y).sum()),
        "removed_x": int((~link & base_link & lab & ~y).sum()), "removed_o": int((~link & base_link & lab & y).sum()),
    }


def per_fold(rows: list[dict], link: np.ndarray) -> tuple[float, float, float, float]:
    """fold 별 정밀도·재현율의 평균·표준편차."""
    ps, rs = [], []
    for k in range(N_FOLDS):
        idx = [i for i, r in enumerate(rows) if r["fold"] == k and r["label"]]
        y = np.array([rows[i]["label"] == "O" for i in idx])
        lk = link[idx]
        tp = (lk & y).sum()
        ps.append(tp / max(1, lk.sum()))
        rs.append(tp / max(1, y.sum()))
    return float(np.mean(ps)), float(np.std(ps)), float(np.mean(rs)), float(np.std(rs))


def bootstrap_delta(rows: list[dict], link: np.ndarray, base: np.ndarray, n: int = 2000, seed: int = 0) -> tuple[tuple[float, float], tuple[float, float]]:
    """사건 단위 부트스트랩 — (ΔP 95% 구간, ΔR 95% 구간), 기준 대비."""
    keys = sorted({r["event_key"] for r in rows})
    kidx = {k: i for i, k in enumerate(keys)}
    agg = np.zeros((len(keys), 5))  # tp, n, tp_base, n_base, n_o
    for i, r in enumerate(rows):
        if not r["label"]:
            continue
        o = r["label"] == "O"
        j = kidx[r["event_key"]]
        agg[j] += [link[i] and o, link[i], base[i] and o, base[i], o]
    rng = np.random.default_rng(seed)
    dp, dr = [], []
    for _ in range(n):
        s = agg[rng.integers(0, len(keys), len(keys))].sum(axis=0)
        dp.append(s[0] / max(1, s[1]) - s[2] / max(1, s[3]))
        dr.append(s[0] / max(1, s[4]) - s[2] / max(1, s[4]))
    q = lambda v: (float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5)))  # noqa: E731
    return q(dp), q(dr)


def fit_predict_folds(rows: list[dict], kind: str, X: np.ndarray, docs: list[str]) -> np.ndarray:
    """사건 단위 fold 마다 학습(라벨 있는 재순위 대상 행) → 그 fold 의 재순위 대상 전부 예측 (라벨 없는 행 포함)."""
    p = np.full(len(rows), np.nan)
    dom = np.array([r["in_domain"] == 1 for r in rows])
    lab = np.array([bool(r["label"]) for r in rows])
    y = np.array([r["label"] == "O" for r in rows], dtype=int)
    fold = np.array([r["fold"] for r in rows])
    for k in range(N_FOLDS):
        tr = np.where(dom & lab & (fold != k))[0]
        te = np.where(dom & (fold == k))[0]
        m = RR.FeatureModel(kind=kind).fit(X[tr], [docs[i] for i in tr], y[tr])
        p[te] = m.predict_proba(X[te], [docs[i] for i in te])
    return p


def rule_links(rows: list[dict], settings, *, rescue: bool, remove_days: int | None, remove_overlap: float = 0.0) -> np.ndarray:
    """접근법 A — 진단의 두 사유를 규칙으로.
    rescue: '다른 시드 시설명' 검사에서 일반어 시설명(산사태·집중호우·어선·산불·폭설 …)은 세지 않는다 (검토 큐에 갇힌 O)
    remove: 제목에 사건 단어가 없고(t_overlap ≤ remove_overlap) 발생 remove_days 일 뒤인 자동 연결은 검토 큐로 (무관·예방 정책 일반)
    """
    out = np.zeros(len(rows), dtype=bool)
    for i, r in enumerate(rows):
        if r["in_domain"] != 1:
            continue
        f = {n: float(r.get(n) or 0.0) for n in ("strong", "other_hit", "other_fac_specific", "cap_recur", "cap_place", "cos", "overlap", "t_overlap", "days_log")}
        other = (f["other_fac_specific"] > 0 or f["cap_recur"] > 0 or f["cap_place"] > 0) if rescue else f["other_hit"] > 0
        d = L.decide(f["strong"] > 0, other, f["cos"], f["overlap"], settings)[0] if rescue else r["base_decision"]
        if d == "link" and remove_days is not None and f["t_overlap"] <= remove_overlap and f["days_log"] > np.log1p(remove_days):
            d = "review"
        out[i] = d == "link"
    return out


def rule_cv(rows: list[dict], settings, grid: list[dict]) -> tuple[np.ndarray, list[dict]]:
    """규칙 매개변수를 학습 fold 의 F1 로 고르고 평가 fold 에 적용 (규칙도 같은 교차검증으로)."""
    cand = {json.dumps(g, sort_keys=True): rule_links(rows, settings, **g) for g in grid}
    fold = np.array([r["fold"] for r in rows])
    out = np.zeros(len(rows), dtype=bool)
    chosen = []
    for k in range(N_FOLDS):
        tr_rows = [r for r in rows if r["fold"] != k]
        tr_idx = np.where(fold != k)[0]
        best = max(cand, key=lambda g: scores(tr_rows, cand[g][tr_idx], np.zeros(len(tr_idx), bool), np.zeros(len(tr_idx), bool))["f1"])
        te = fold == k
        out[te] = cand[best][te]
        chosen.append(json.loads(best))
    return out, chosen


def auc(y: np.ndarray, s: np.ndarray) -> float:
    from sklearn.metrics import roc_auc_score

    ok = ~np.isnan(s)
    if ok.sum() == 0 or len(set(y[ok])) < 2:
        return float("nan")
    return float(roc_auc_score(y[ok], s[ok]))


def followup_eval(settings, rows: list[dict], policies: dict[str, np.ndarray]) -> dict[str, dict]:
    """정책별 자동 연결로 후속 기사를 골라 규칙 어댑터로 다시 분류 → 사건 라벨(gold/experiment_labels.csv) 기준 precision/recall.
    근사: 약속 재추출·약속 키워드 재검색·사건 간 배타 처리는 다시 하지 않는다 (label_eval.FollowupSim)."""
    from app.db.models import Event
    from app.llm.rules_adapter import RulesLLM
    from app.pipeline.embeddings import build_embedding

    session = open_db(settings)
    embedder = build_embedding(settings)
    sim = LE.FollowupSim(session, settings, RulesLLM(settings, embedder))
    _, fu = LE.load_labels(GOLD)
    events = {e.key: e for e in session.query(Event).filter(Event.active.is_(True)).all() if e.key in fu}
    pinned = {k: {l.news_id for l in e.links if (l.confirmed or l.link_method == "manual") and not l.rejected} for k, e in events.items()}
    out = {}
    for name, link in policies.items():
        active = {k: set(v) for k, v in pinned.items()}
        for i, r in enumerate(rows):
            if link[i] and r["event_key"] in active:
                active[r["event_key"]].add(r["news_id"])
        system = {k: sim.has_followup(e, active[k], settings) for k, e in events.items()}
        out[name] = LE.followup_scores(system, fu)
        print(f"  후속 {name}: {out[name]}", flush=True)
    session.close()
    return out


def diagnose(rows: list[dict]) -> list[str]:
    """진단 표 (라벨 X 사유 × 지금 상태, 사건별 남은 오염, 검토 큐의 O, 피처 단변량 AUC)."""
    lab = [r for r in rows if r["label"]]
    st = lambda r: {"auto": "자동", "review": "검토 대기", "pinned": "고정"}.get(r["state"], "연결 없음")  # noqa: E731
    out = ["### 1-1. 라벨 X 7,794건 — 사유 × 지금 상태", "", "| 사유 | X 전체 | 지금 자동 연결 | 검토 대기 | 연결 없음(게이트 탈락) |", "|---|---|---|---|---|"]
    xs = [r for r in lab if r["label"] == "X"]
    for reason, n in Counter(r["reason"] for r in xs).most_common():
        c = Counter(st(r) for r in xs if r["reason"] == reason)
        out.append(f"| {REASON_KO.get(reason, reason)} | {n} | {c['자동']} | {c['검토 대기']} | {c['연결 없음']} |")
    c = Counter(st(r) for r in xs)
    out.append(f"| 계 | {len(xs)} | {c['자동']} | {c['검토 대기']} | {c['연결 없음']} |")
    auto = [r for r in lab if r["state"] == "auto"]
    n_o = sum(r["label"] == "O" for r in lab)
    tp = sum(r["label"] == "O" for r in auto)
    out += ["", f"지금 자동 연결(라벨 행) {len(auto)} · O {tp} · 정밀도 {tp / len(auto):.3f} · 재현율 {tp / n_o:.3f}", ""]
    # 자동 연결 X — 경로·시기
    out += ["### 1-2. 남은 오염(자동 연결 X)의 경로·시기", "", "| 경로 | 발생 90일 안 | 90일 뒤 | 계 |", "|---|---|---|---|"]
    ax = [r for r in auto if r["label"] == "X"]
    for m in ("rule", "embedding"):
        e = sum(1 for r in ax if r.get("base_method") == m and float(r.get("late") or 0) == 0)
        lt = sum(1 for r in ax if r.get("base_method") == m and float(r.get("late") or 0) > 0)
        out.append(f"| {'강한 규칙(rule)' if m == 'rule' else '임베딩 점수(embedding)'} | {e} | {lt} | {e + lt} |")
    # 사건별
    by_ev: dict[str, Counter] = defaultdict(Counter)
    titles = {}
    for r in auto:
        by_ev[r["event_key"]][r["label"]] += 1
        by_ev[r["event_key"]]["reason:" + r["reason"]] += r["label"] == "X"
        titles[r["event_key"]] = (r.get("event_text") or "").split(" · ")[0]
    tot = len(ax)
    out += ["", "### 1-3. 남은 오염이 몰린 사건 (자동 연결 X 상위 15)", "", "| 사건 | 광역 | 자동 연결 | X | X 비율 | 누적 몫 | 주된 사유 |", "|---|---|---|---|---|---|---|"]
    cum = 0
    wide = {r["event_key"]: float(r.get("wide") or 0) > 0 for r in auto}
    for k, c in sorted(by_ev.items(), key=lambda kv: -kv[1]["X"])[:15]:
        cum += c["X"]
        n = c["O"] + c["X"]
        rs = sorted(((v, kk[7:]) for kk, v in c.items() if kk.startswith("reason:") and v), reverse=True)[:2]
        out.append(f"| {titles[k][:30]} | {'광역' if wide[k] else '기초'} | {n} | {c['X']} | {c['X'] / n:.2f} | {cum / tot:.2f} | "
                   + ", ".join(f"{REASON_KO.get(b, b)} {a}" for a, b in rs) + " |")
    n_ev = sum(1 for c in by_ev.values() if c["X"])
    out.append(f"\n자동 연결 X 가 있는 사건 {n_ev}곳 · 상위 15곳이 {cum / tot:.0%}")
    # 검토 대기 O
    rev = [r for r in lab if r["state"] == "review"]
    ro = [r for r in rev if r["label"] == "O"]
    why = lambda r: ("다른 시드 시설명" if "other-facility" in r.get("notes", "") else "늦은 같은 유형 새 사고(cap-recur)" if "cap-recur" in r.get("checks", "")  # noqa: E731
                     else "다른 장소(cap-place)" if "cap-place" in r.get("checks", "") else "점수 < LINK_AUTO" if r.get("base_decision") == "review" else "사건 간 배타(multi-event)")
    out += ["", f"### 1-4. 검토 대기 큐 — 라벨 행 {len(rev)} · O {len(ro)} (O 비율 {len(ro) / max(1, len(rev)):.2f})", "",
            "| 검토 대기가 된 이유 | O | X | O 비율 |", "|---|---|---|---|"]
    for w, n in Counter(why(r) for r in rev).most_common():
        o = sum(1 for r in ro if why(r) == w)
        out.append(f"| {w} | {o} | {n - o} | {o / n:.2f} |")
    gen = sum(1 for r in ro if float(r.get("other_fac_generic") or 0) > 0 and float(r.get("other_fac_specific") or 0) == 0)
    gx = sum(1 for r in rev if r["label"] == "X" and float(r.get("other_fac_generic") or 0) > 0 and float(r.get("other_fac_specific") or 0) == 0)
    out.append(f"\n다른 시드 시설명이 **일반어로만**(산사태·집중호우·어선·산불·폭설 …) 걸린 검토 대기: O {gen} · X {gx}")
    early = sum(1 for r in ro if float(r.get("late") or 0) == 0)
    out.append(f"검토 대기 O 중 발생 90일 안 {early} · 제목에 사건 지역어 {sum(1 for r in ro if float(r.get('t_region') or 0) > 0)} · 제목에 사건 고유어 {sum(1 for r in ro if float(r.get('t_uniq_n') or 0) > 0)}")
    # 단변량 AUC
    dom = [r for r in lab if r["in_domain"] == 1]
    y = np.array([r["label"] == "O" for r in dom])
    X = matrix(dom)
    res = []
    for j, n in enumerate(RR.FEATURES):
        if np.ptp(X[:, j]) == 0:
            continue
        a = auc(y, X[:, j])
        res.append((n, a))
    res.sort(key=lambda t: -abs(t[1] - 0.5))
    out += ["", f"### 1-5. 피처 단변량 분리력 (재순위 대상 라벨 행 {len(dom)}, AUC — 0.5 에서 멀수록 분리, 0.5 미만은 값이 클수록 X)", "",
            "| 피처 | AUC | 피처 | AUC | 피처 | AUC |", "|---|---|---|---|---|---|"]
    for i in range(0, len(res), 3):
        cells = [f"{n} | {a:.3f}" for n, a in res[i:i + 3]]
        cells += ["| "] * (3 - len(cells))
        out.append("| " + " | ".join(cells) + " |")
    return out


def label_noise() -> list[str]:
    """두 에이전트 라벨(cluster_sample 10-23, cluster_full 10-25)이 겹치는 행의 일치도 — 라벨 잡음 상한."""
    full = LE.load_full_labels(GOLD) or {}
    with (GOLD / "cluster_sample.csv").open(encoding="utf-8-sig", newline="") as f:
        smp = {(r["사건키"], r["news_id"]): (r.get("사람_OK(O/X)") or "").strip().upper() for r in csv.DictReader(f)}
    pairs = [(v == "O", full[k]) for k, v in smp.items() if v in ("O", "X") and k in full]
    n = len(pairs)
    agree = sum(a == b for a, b in pairs)
    po = agree / n
    pa, pb = sum(a for a, _ in pairs) / n, sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    kappa = (po - pe) / (1 - pe)
    ox = sum(1 for a, b in pairs if a and not b)
    xo = sum(1 for a, b in pairs if not a and b)
    e = (1 - (1 - 2 * (1 - po)) ** 0.5) / 2  # 두 라벨러가 독립으로 같은 오류율 e 일 때 불일치 = 2e(1-e)
    return [
        f"- 두 에이전트 라벨이 겹치는 {n}행: 일치 {agree} ({po:.3f}), Cohen κ {kappa:.2f} — 표본 O·전체 X {ox}, 표본 X·전체 O {xo}.",
        f"- 독립·같은 오류율로 보면 라벨 하나의 오류율 ≈ {e:.1%}. 완벽한 분류기도 이 라벨로 재면 정밀도·재현율이 대략 {1 - e:.2f} 언저리에서 멈춘다(상한).",
        "- 오류가 독립이 아니면(같은 지침·같은 모델 계열) 실제 잡음은 이보다 클 수 있다. 차이 0.01 안팎은 잡음 안이다.",
    ]


def regression_masks(rows: list[dict]) -> dict[str, np.ndarray]:
    """회귀 사례 (docs/EXPERIMENT.md §7·tests/test_link_window.py): 부산 만덕~센텀 2026 준공·개통·지반 침하, 영도구 2024-11·12 방호울타리, 현대시장 2024-07 복구."""
    def m(pred):
        return np.array([bool(r.get("in_domain")) and r["label"] == "O" and pred(r) for r in rows])

    return {
        "만덕": m(lambda r: "만덕" in r.get("event_text", "") and r["published"] >= "2026-01-01" and ("만덕" in r["title"] or "대심도" in r["title"])),
        "영도": m(lambda r: "청동초" in r.get("event_text", "") and r["published"] in ("2024-11-13", "2024-12-24")),
        "현대": m(lambda r: "현대시장" in r.get("event_text", "") and "2024-07-02" <= r["published"] <= "2024-07-03" and "현대시장" in r["title"]),
    }


def cv(settings, cross_oof: Path | None, with_followup: bool) -> Path:
    rows = read_pairs()
    t0 = time.time()
    X = matrix(rows)
    docs = [r.get("doc") or "" for r in rows]
    lab = np.array([bool(r["label"]) for r in rows])
    dom = np.array([r["in_domain"] == 1 for r in rows])
    y = np.array([r["label"] == "O" for r in rows])
    base_link = np.array([r["in_domain"] == 1 and r["base_decision"] == "link" for r in rows])
    base_review = np.array([r["in_domain"] == 1 and r["base_decision"] == "review" for r in rows])
    db_auto = np.array([r["state"] == "auto" for r in rows])
    floor = base_link & np.array([float(r.get("late") or 0) > 0 and float(r.get("t_fac") or 0) > 0 for r in rows])
    reg = regression_masks(rows)

    # B — 피처 분류기
    probs: dict[str, np.ndarray] = {}
    for kind in ("lr", "hgb", "avg"):
        probs[kind] = fit_predict_folds(rows, kind, X, docs)
        print(f"B/{kind} OOF AUC {auc(y[dom & lab], probs[kind][dom & lab]):.4f} [{time.time() - t0:.0f}s]", flush=True)
    # C — 교차 인코더 OOF (scripts/train_cross_encoder.py). B+C = 확률 가중 평균 (추가 학습 없음 — 중첩 교차검증이 필요 없는 결합)
    if cross_oof and cross_oof.exists():
        with cross_oof.open(encoding="utf-8", newline="") as f:
            cp = {(r["event_key"], r["news_id"]): float(r["p"]) for r in csv.DictReader(f)}
        probs["cross"] = np.array([cp.get((r["event_key"], r["news_id"]), np.nan) if r["in_domain"] == 1 else np.nan for r in rows])
        missing = int((np.isnan(probs["cross"]) & dom).sum())
        print(f"C OOF AUC {auc(y[dom & lab], probs['cross'][dom & lab]):.4f} (점수 없는 대상 행 {missing})")
        for w in (0.3, 0.5):
            probs[f"blend{w}"] = np.where(np.isnan(probs["cross"]), np.nan, w * probs["avg"] + (1 - w) * probs["cross"])
    with OOF.open("w", encoding="utf-8", newline="") as f:
        w_ = csv.writer(f)
        w_.writerow(["event_key", "news_id", "fold", "label", *probs])
        for i, r in enumerate(rows):
            if r["in_domain"] == 1:
                w_.writerow([r["event_key"], r["news_id"], r["fold"], r["label"], *(f"{probs[k][i]:.4f}" for k in probs)])

    # A — 규칙 (매개변수도 교차검증)
    grid = [{"rescue": rs, "remove_days": d, "remove_overlap": o} for rs in (False, True) for d in (None, 14, 30, 60, 90, 180) for o in (0.0, 0.1)
            if not (d is None and o > 0)]
    a_both, chosen_both = rule_cv(rows, settings, [g for g in grid if g["rescue"] and g["remove_days"] is not None])
    a_rescue = rule_links(rows, settings, rescue=True, remove_days=None)
    a_remove, chosen_remove = rule_cv(rows, settings, [g for g in grid if not g["rescue"] and g["remove_days"] is not None])

    label = {"lr": "B/lr", "hgb": "B/hgb", "avg": "B/avg", "cross": "C/cross", "blend0.3": "B+C/0.3·B+0.7·C", "blend0.5": "B+C/0.5·B+0.5·C"}
    policies: dict[str, np.ndarray] = {"지금 DB (사건 간 배타 포함)": db_auto, "지금 게이트·임계값 (재계산)": base_link,
                                       "A1 일반어 시설명은 '다른 시설' 아님 (검토 큐 구출)": a_rescue,
                                       "A2 제목에 사건 단어 없는 늦은 자동 연결 → 검토 (교차검증)": a_remove, "A1 + A2 (교차검증)": a_both}
    for kind, p in probs.items():
        pp = np.nan_to_num(p, nan=-1.0)
        for t in THRESHOLDS:
            policies[f"{label[kind]} p≥{t}"] = dom & (pp >= t)
        for t in (0.4, 0.5, 0.6):
            policies[f"{label[kind]} p≥{t} + 바닥"] = dom & ((pp >= t) | floor)
    strong = np.array([float(r.get("strong") or 0) > 0 and float(r.get("other_hit") or 0) == 0 for r in rows])
    policies["B/avg p≥0.5 — 강한 규칙은 그대로"] = dom & ((base_link & strong) | (np.nan_to_num(probs["avg"], nan=-1) >= 0.5))

    res = {name: scores(rows, link, base_link, base_review) for name, link in policies.items()}
    for name, link in policies.items():
        res[name]["fold"] = per_fold(rows, link)
    fu_names = ["지금 게이트·임계값 (재계산)", "A1 + A2 (교차검증)", "B/avg p≥0.5", "B/avg p≥0.5 + 바닥", "B/avg p≥0.6 + 바닥", "B/avg p≥0.4 + 바닥"]
    if "cross" in probs:
        fu_names += ["C/cross p≥0.5 + 바닥", "B+C/0.3·B+0.7·C p≥0.5 + 바닥", "B+C/0.5·B+0.5·C p≥0.5 + 바닥"]
    fu = followup_eval(settings, rows, {n: policies[n] for n in fu_names}) if with_followup else {}

    L_ = [f"# 학습 기반 재순위 — 사건 단위 5-fold 교차검증 ({datetime.now().strftime('%Y-%m-%d %H:%M')})", "",
          f"- 쌍: `{PAIRS.relative_to(BACKEND_DIR)}` {len(rows)}행 (재순위 대상 {int(dom.sum())}, 라벨 {int(lab.sum())}, 라벨 O {int((lab & y).sum())})",
          "- 라벨: gold/cluster_full.csv (Claude 에이전트 라벨, 사람 아님). fold: 사건 90건을 라벨 행 수 균형으로 5개 — 같은 사건 기사는 학습·평가에 함께 들어가지 않는다.",
          "- 정밀도·재현율: 라벨 행 기준 (재현율 분모 = 라벨 O 전체). '건진 O' = 지금 검토 대기(점수·다른 시설 등)인 라벨 O 중 자동 연결로 올라온 수.",
          "- '+ 바닥' = 재순위 전 자동 연결 중 창 뒤(발생 + 90일) 기사이고 제목에 사건 시설명이 있으면 분류기가 내리지 않는다 (RERANK_KEEP_LATE_FACILITY).",
          f"- 회귀 사례(라벨 O): 만덕 2026 {int(reg['만덕'].sum())} · 영도구 방호울타리 {int(reg['영도'].sum())} · 현대시장 복구 {int(reg['현대'].sum())} — 자동 연결로 남은 수.", "",
          "## 1. 진단", "", *diagnose(rows), "", "## 2. 접근법 비교", "",
          "| 접근법 | 자동 연결(라벨 행) | 정밀도 | 재현율 | F1 | AUC | 검토 큐에서 건진 O | 검토 큐에서 올라온 X | 자동에서 뺀 X | 자동에서 뺀 O | fold 정밀도 (평균±sd) | fold 재현율 | 회귀 사례 만덕·영도·현대 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    auc_of = {label[k]: auc(y[dom & lab], probs[k][dom & lab]) for k in probs}
    auc_of["score"] = auc(y[dom & lab], np.array([float(r.get("score") or 0) for r in rows])[dom & lab])
    for name, s in res.items():
        kind = name.split(" ")[0] if "/" in name else ("score" if name.startswith("지금") else "")
        a = auc_of.get(kind, float("nan"))
        fp, fps, fr, frs = s["fold"]
        rg = " · ".join(f"{int((policies[name] & m).sum())}/{int(m.sum())}" for m in reg.values())
        L_.append(f"| {name} | {s['n_link']} | {s['precision']:.3f} | {s['recall']:.3f} | {s['f1']:.3f} | {'-' if math.isnan(a) else f'{a:.3f}'} | {s['rescued_o']} | "
                  f"{s['promoted_x']} | {s['removed_x']} | {s['removed_o']} | {fp:.3f}±{fps:.3f} | {fr:.3f}±{frs:.3f} | {rg} |")
    L_ += ["", f"- '지금 게이트·임계값' AUC 는 합산 점수(link_score)의 AUC. A 의 교차검증 선택값 — A1+A2: {chosen_both} · A2: {chosen_remove}"]
    # fold 별 AUC
    L_ += ["", "### fold 별 AUC (재순위 대상 라벨 행)", "", "| fold | " + " | ".join(label[k] for k in probs) + " |", "|---|" + "---|" * len(probs)]
    fold = np.array([r["fold"] for r in rows])
    for k in range(N_FOLDS):
        mk = dom & lab & (fold == k)
        L_.append(f"| {k} | " + " | ".join(f"{auc(y[mk], probs[c][mk]):.3f}" for c in probs) + " |")
    # 부트스트랩
    L_ += ["", "### 사건 단위 부트스트랩 (2,000회) — 지금 게이트·임계값 대비 Δ 95% 구간", "", "| 접근법 | ΔP | ΔR |", "|---|---|---|"]
    for name in [n for n in ["A1 + A2 (교차검증)", "B/avg p≥0.5", "B/avg p≥0.5 + 바닥", "C/cross p≥0.5 + 바닥", "B+C/0.3·B+0.7·C p≥0.5 + 바닥", "B+C/0.5·B+0.5·C p≥0.5 + 바닥"] if n in policies]:
        (p0, p1), (r0, r1) = bootstrap_delta(rows, policies[name], base_link)
        L_.append(f"| {name} | {p0:+.3f} ~ {p1:+.3f} | {r0:+.3f} ~ {r1:+.3f} |")
    # 검토 큐 크기
    for kind in [k for k in ("avg", "blend0.3", "cross") if k in probs]:
        L_ += ["", f"### 검토 큐 하한(RERANK_REVIEW) — {label[kind]}, 자동 연결 p≥0.5 + 바닥", "",
               "| RERANK_REVIEW | 검토 큐(라벨 행) | 그중 O | 폐기(라벨 행) | 폐기된 O | 라벨 없는 행 (검토 · 폐기) |", "|---|---|---|---|---|---|"]
        pa = np.nan_to_num(probs[kind], nan=-1.0)
        auto_ = (pa >= 0.5) | floor
        for rv in (0.1, 0.2, 0.3):
            q = dom & ~auto_ & (pa >= rv)
            d = dom & ~auto_ & (pa < rv)
            L_.append(f"| {rv} | {int((q & lab).sum())} | {int((q & lab & y).sum())} | {int((d & lab).sum())} | {int((d & lab & y).sum())} | {int((q & ~lab).sum())} · {int((d & ~lab).sum())} |")
    if fu:
        L_ += ["", "## 3. 후속보도 탐지 (사건 라벨 90건, 시뮬레이션 — 약속 재추출·재검색·사건 간 배타 없음)", "",
               "| 접근법 | '있음' | TP | FP | FN | precision | recall | F1 |", "|---|---|---|---|---|---|---|---|"]
        for name, s in fu.items():
            L_.append(f"| {name} | {s['system_positive']} | {s['tp']} | {s['fp']} | {s['fn']} | {s['followup_precision']} | {s['followup_recall']} | {s['followup_f1']} |")
    L_ += ["", "## 4. 라벨 잡음 상한", "", *label_noise()]
    out = settings.reports_dir / f"rerank_cv_{datetime.now().strftime('%Y%m%d')}.md"
    out.write_text("\n".join(L_) + "\n", encoding="utf-8")
    print("\n".join(L_))
    print(f"\n-> {out} [{time.time() - t0:.0f}s]")
    return out


# ------------------------------------------------------------------ 3. 학습
def train(settings, kind: str, out: Path) -> Path:
    import sklearn

    rows = read_pairs()
    X = matrix(rows)
    docs = [r.get("doc") or "" for r in rows]
    dom = np.array([r["in_domain"] == 1 for r in rows])
    lab = np.array([bool(r["label"]) for r in rows])
    y = np.array([r["label"] == "O" for r in rows], dtype=int)
    fold = np.array([r["fold"] for r in rows])
    folds = {r["event_key"]: r["fold"] for r in rows}
    fold_models = []
    for k in range(N_FOLDS):
        tr = np.where(dom & lab & (fold != k))[0]
        fold_models.append(RR.FeatureModel(kind=kind).fit(X[tr], [docs[i] for i in tr], y[tr]))
    tr = np.where(dom & lab)[0]
    full = RR.FeatureModel(kind=kind).fit(X[tr], [docs[i] for i in tr], y[tr])
    # OOF 와 같은지 (교차검증 표의 수치가 이 fold 모델의 수치인지) 확인
    if OOF.exists():
        with OOF.open(encoding="utf-8", newline="") as f:
            oof = {(r["event_key"], r["news_id"]): float(r[kind]) for r in csv.DictReader(f) if r.get(kind)}
        diffs = []
        for k in range(N_FOLDS):
            te = np.where(dom & (fold == k))[0]
            p = fold_models[k].predict_proba(X[te], [docs[i] for i in te])
            diffs += [abs(p[j] - oof[(rows[i]["event_key"], rows[i]["news_id"])]) for j, i in enumerate(te) if (rows[i]["event_key"], rows[i]["news_id"]) in oof]
        print(f"fold 모델 ↔ OOF 최대 차이 {max(diffs):.5f} ({len(diffs)}행)")
    emb = json.loads(PAIRS_META.read_text(encoding="utf-8")).get("embedding") if PAIRS_META.exists() else None
    meta = {"trained_at": datetime.now().isoformat(timespec="seconds"), "kind": kind, "labels": "gold/cluster_full.csv (Claude 에이전트 라벨)",
            "embedding": emb,
            "rows": int(len(tr)), "positives": int(y[tr].sum()), "events": len(folds), "folds": N_FOLDS, "sklearn": sklearn.__version__,
            "pairs": str(PAIRS.relative_to(BACKEND_DIR))}
    RR.save_bundle(out, full, fold_models, folds, meta)
    print(f"모델 → {out} ({out.stat().st_size / 1e6:.1f} MB) · {meta}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--extract", action="store_true")
    ap.add_argument("--cv", action="store_true")
    ap.add_argument("--train", action="store_true")
    ap.add_argument("--followup", action="store_true", help="--cv 에서 정책별 후속보도 탐지 시뮬레이션 (느림)")
    ap.add_argument("--cross-oof", default=str(DATA / "rerank_cross_oof.csv"))
    ap.add_argument("--kind", default="avg", choices=("avg", "lr", "hgb"))
    ap.add_argument("--out", default=None, help="모델 경로 (기본 models/reranker_features.joblib)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    if not (args.extract or args.cv or args.train):
        args.extract = args.cv = args.train = True
    settings = get_settings()
    if args.extract:
        extract(settings)
    if args.cv:
        cv(settings, Path(args.cross_oof) if args.cross_oof else None, args.followup)
    if args.train:
        train(settings, args.kind, Path(args.out) if args.out else RR.model_path(settings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
