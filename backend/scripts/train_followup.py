"""2단계(후속 신호 판정) 연구 — 규칙 보강과 학습 판정을 같은 잣대로 비교한다 (docs/EXPERIMENT.md §10, docs/연구.md 3.7).

  cd backend
  ENV="BIGKINDS_MODE=record LLM_MODE=rules EMBEDDING_BACKEND=st TODAY_OVERRIDE=2026-10-22"
  env $ENV .venv/bin/python scripts/train_followup.py --extract   # 지금 DB 의 연결·약속으로 후보 기사마다 규칙 판정을 변형별로 다시 돌린다 (DB 불변)
  env $ENV .venv/bin/python scripts/train_followup.py --cv        # 사건 라벨 90건 기준 비교 → reports/followup_cv_<날짜>.md

잣대: 사건 라벨(gold/experiment_labels.csv) 90건의 후속보도 precision / recall / F1, 그리고 '근거까지 맞은' 탐지 —
  근거 기사(시스템이 '있음' 의 근거로 든 기사)가 같은 사건 기사(gold/cluster_full.csv O)인 사건 수, 라벨 근거 기사(사람_근거_news_id)와 같은 사건 수.
학습 판정·규칙 조합 선택은 사건 단위 5-fold (rerank_pairs 와 같은 fold) — 같은 사건이 학습·평가에 함께 들어가지 않는다.
"""

from __future__ import annotations

import argparse
import csv
import math
import pickle
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from app.config import BACKEND_DIR, get_settings  # noqa: E402

DATA = BACKEND_DIR / "data" / "experiment"
CANDS = DATA / "followup_cands.pkl"
GOLD = BACKEND_DIR / "gold"
POSITIVE = {"완료", "진행", "새로운문제"}

# 응급 대응 약속(진화·수색·구조·감식·총력 …) — 복구·보강·지원 같은 조치어가 없으면 '사건 대응 작전' 이지 추적할 조치 약속이 아니다
EMERGENCY_RE = re.compile(r"(진화|수색|구조|감식|대피|총력|만전|최선을\s*다|동원|투입|가용|원인\s*(을\s*)?(규명|조사|파악)|수사|입건|조사할|조사하겠|순찰|예찰|비상\s*근무|비상\s*체계)")
REMEDY_RE = re.compile(r"(복구|보강|설치|교체|개선|보상|배상|대책|정비|이전|철거|재건|준설|확충|증설|신설|도입|구축|마련|개정|제정|폐쇄|진단|조성|지급|지원금|보험|임시\s*주택|재시공|개통|준공)")

RULE_VARIANTS: dict[str, dict] = {
    "v2": {},
    "fix": {"followup_expression_fixes": True},
    "fix+skip": {"followup_expression_fixes": True, "followup_skip_commitment": True},
    "fix+match": {"followup_expression_fixes": True, "followup_action_match": True},
    "fix+skip+match": {"followup_expression_fixes": True, "followup_skip_commitment": True, "followup_action_match": True},
    "fix+skip+match-uw": {"followup_expression_fixes": True, "followup_skip_commitment": True, "followup_action_match": True, "followup_kw_alone": False},
}
PROMISE_SETS = ("all", "no_op", "src")


def operational(action: str) -> bool:
    return bool(EMERGENCY_RE.search(action or "")) and not REMEDY_RE.search(action or "")


def load_labels():
    labs = {r["사건키"]: r for r in csv.DictReader((GOLD / "experiment_labels.csv").open(encoding="utf-8-sig"))}
    fu = {k: r["사람_후속보도(O/X)"].strip() == "O" for k, r in labs.items() if r["사람_후속보도(O/X)"].strip() in ("O", "X")}
    evid = {k: set(r.get("사람_근거_news_id", "").split()) for k, r in labs.items()}
    full = {(r["사건키"], r["news_id"]): r["사람_OK(O/X)"].strip() for r in csv.DictReader((GOLD / "cluster_full.csv").open(encoding="utf-8-sig"))}
    folds = {}
    pairs = DATA / "rerank_pairs.csv"
    if pairs.exists():
        for r in csv.DictReader(pairs.open(encoding="utf-8")):
            folds[r["event_key"]] = int(r["fold"])
    return fu, evid, full, folds


# ------------------------------------------------------------------ 1. 추출
def extract(settings) -> Path:
    from app.db.models import Article, Event
    from app.db.session import init_db, session_factory
    from app.llm.client import validate_span
    from app.llm.rules_adapter import RulesLLM
    from app.llm.schemas import FollowupInput, PromiseRef
    from app.pipeline.embeddings import build_embedding
    from app.pipeline.judge import trackable_promises
    from app.rules import weights as W

    t0 = time.time()
    fu, evid, full, folds = load_labels()
    init_db()
    s = session_factory()()
    emb = build_embedding(settings)
    llm = RulesLLM(settings, emb)
    out = []
    for ev in s.query(Event).filter(Event.active.is_(True)).order_by(Event.key).all():
        if ev.key not in fu:
            continue
        src = s.get(Article, ev.source_news_id) if ev.source_news_id else None
        src_date = src.published_at if src is not None else None
        proms = trackable_promises(ev)
        pmeta = {}
        for p in proms:
            a = s.get(Article, p.evidence_news_id)
            ids = {p.evidence_news_id, *(p.merged_news_ids or [])}
            pmeta[p.id] = {
                "action": p.action, "actor": p.actor_org, "strength": p.strength, "date": a.published_at if a is not None else None,
                "is_source": bool(ev.source_news_id and ev.source_news_id in ids), "operational": operational(p.action),
            }
        sets = {"all": [p for p in proms], "no_op": [p for p in proms if not pmeta[p.id]["operational"]]}
        srcp = [p for p in sets["no_op"] if pmeta[p.id]["is_source"]] or [p for p in proms if pmeta[p.id]["is_source"]]
        sets["src"] = srcp or sets["no_op"] or sets["all"]
        identity = sorted({*(W.text_tokens(ev.facility or "")), *(W.region_terms(ev.region or "")), *([ev.facility] if ev.facility else [])})
        quotes: dict[str, list[str]] = {}
        for q in ev.quotations:
            quotes.setdefault(q.news_id, []).append(f"{q.source}: {q.quotation}")
        arts = []
        for l in ev.links:
            if not l.is_active:
                continue
            a = s.get(Article, l.news_id)
            if a is None or a.published_at is None or a.published_at < ev.occurred_at or a.change_status == "cancelled":
                continue
            arts.append((a, l))
        from app.pipeline.embeddings import from_bytes, needs_recompute

        qvec = np.asarray(emb.encode([ev.source_quotation or ev.title])[0])
        cands = []
        for a, l in sorted(arts, key=lambda x: x[0].published_at):
            row = {"news_id": a.news_id, "published": a.published_at, "title": a.title or "", "hilight": a.hilight or "", "link_score": float(l.link_score or 0.0),
                   "link_method": l.link_method, "pinned": bool(l.confirmed or l.link_method == "manual"), "keep_late": "keep-late-facility" in (l.link_reason or ""),
                   "cluster": full.get((ev.key, a.news_id), "-"),
                   "evidence": a.news_id in evid.get(ev.key, set()), "res": {},
                   "cos_src": 0.0 if needs_recompute(a.embedding, a.embedding_backend, emb) else float(np.dot(from_bytes(a.embedding), qvec))}
            for set_name, plist in sets.items():
                if not plist:
                    continue
                refs = [PromiseRef(id=p.id, actor_org=p.actor_org, action=p.action, deadline_date=p.deadline_date.isoformat() if p.deadline_date else None) for p in plist]
                inp = FollowupInput(
                    event_summary=ev.summary or ev.title, promises=refs, article_title=a.title or "", hilight=a.hilight or a.content_200 or "",
                    quotations=quotes.get(a.news_id, []), published_at=a.published_at.isoformat(), term_weights=ev.term_weights or {}, identity_terms=identity,
                )
                for vname, flags in RULE_VARIANTS.items():
                    llm.settings = settings.model_copy(update=flags)
                    sig, det = llm.classify_followup_detail(inp)
                    sig = validate_span(sig, inp)
                    row["res"][(set_name, vname)] = {
                        "signal": sig.signal, "conf": sig.confidence, "matches": bool(sig.matches_promise and sig.promise_id is not None),
                        "pid": sig.promise_id, "span": sig.evidence_span, **det,
                    }
            cands.append(row)
        from app.rules.orgs import canonical_org

        resp = sorted({x for x in [ev.responsible_org, canonical_org(ev.responsible_org or ""), *(ev.org_aliases or [])] if x})
        out.append({
            "key": ev.key, "title": ev.title, "occurred": ev.occurred_at, "src_date": src_date, "label": fu[ev.key], "fold": folds.get(ev.key, 0), "resp": resp,
            "promises": pmeta, "sets": {k: [p.id for p in v] for k, v in sets.items()}, "cands": cands, "evidence": sorted(evid.get(ev.key, set())),
        })
        print(f"  {ev.key} 후보 {len(cands)} · 약속 {len(proms)} (작전 {sum(m['operational'] for m in pmeta.values())} · 원천 {sum(m['is_source'] for m in pmeta.values())}) [{time.time() - t0:.0f}s]", flush=True)
    s.close()
    DATA.mkdir(parents=True, exist_ok=True)
    with CANDS.open("wb") as f:
        pickle.dump({"events": out, "settings": {"grace": settings.followup_grace_days, "min_conf": settings.signal_min_conf}, "extracted_at": datetime.now().isoformat()}, f)
    print(f"추출: 사건 {len(out)} · 후보 {sum(len(e['cands']) for e in out)} → {CANDS} [{time.time() - t0:.0f}s]")
    return CANDS


# ------------------------------------------------------------------ 2. 정책 평가
def cutoff_ok(ev: dict, c: dict, r: dict, rule: str, grace: int) -> bool:
    pub: date = c["published"]
    occ: date = ev["occurred"]
    src: date | None = ev["src_date"]
    pdate = ev["promises"].get(r.get("pid"), {}).get("date")
    if rule == "current":  # 지금: max(발생+7일, 추적 약속 중 가장 이른 발언일)
        dates = [ev["promises"][i]["date"] for i in ev["sets"][ev["_set"]] if ev["promises"][i]["date"]]
        cut = max(occ + timedelta(days=grace), min(dates)) if dates else occ + timedelta(days=grace)
        return pub > cut
    if rule == "source":  # 원천 발언일 뒤 (발생+7일 유예도 유지)
        cut = max(occ + timedelta(days=grace), src or occ)
        return pub > cut
    if rule == "source_next":  # 원천 발언 다음 날부터 (유예 없음)
        return pub > (src or occ)
    if rule == "promise_each":  # 신호가 가리킨 약속의 발언일 뒤 + 발생+7일
        return pub > max(occ + timedelta(days=grace), pdate or occ)
    if rule == "promise_each_next":  # 신호가 가리킨 약속의 발언일 다음 날부터 (유예 없음)
        return pub > max(occ, pdate or occ)
    if rule == "both_next":  # 원천 발언과 신호가 가리킨 약속 발언 둘 다의 다음 날부터 (유예 없음)
        return pub > max(occ, pdate or occ, src or occ)
    raise ValueError(rule)


def valid(r: dict, min_conf: float) -> bool:
    return r["matches"] and r["conf"] >= min_conf and r["signal"] in POSITIVE


def decide_events(events: list[dict], set_name: str, variant: str, cutoff: str, grace: int, min_conf: float, keep=None) -> dict[str, list[dict]]:
    """사건 → '있음' 근거 후보 목록 (빈 목록 = 없음)."""
    out = {}
    for ev in events:
        ev["_set"] = set_name
        pos = []
        if ev["sets"].get(set_name):
            for c in ev["cands"]:
                r = c["res"].get((set_name, variant))
                if r is None or not valid(r, min_conf) or not cutoff_ok(ev, c, r, cutoff, grace):
                    continue
                if keep is not None and not keep(ev, c, r):
                    continue
                pos.append(c)
        out[ev["key"]] = pos
    return out


def event_scores(events: list[dict], pos: dict[str, list[dict]]) -> dict:
    tp = fp = fn = tn = tp_cluster = tp_evid = fp_cluster = 0
    for ev in events:
        p = bool(pos[ev["key"]])
        if p and ev["label"]:
            tp += 1
            tp_cluster += any(c["cluster"] == "O" for c in pos[ev["key"]])
            tp_evid += any(c["evidence"] for c in pos[ev["key"]])
        elif p:
            fp += 1
            fp_cluster += any(c["cluster"] == "O" for c in pos[ev["key"]])
        elif ev["label"]:
            fn += 1
        else:
            tn += 1
    pr = tp / (tp + fp) if tp + fp else 0.0
    rc = tp / (tp + fn) if tp + fn else 0.0
    return {"positive": tp + fp, "tp": tp, "fp": fp, "fn": fn, "precision": pr, "recall": rc, "f1": 2 * pr * rc / (pr + rc) if pr + rc else 0.0,
            "tp_cluster": tp_cluster, "tp_evidence": tp_evid, "fp_cluster": fp_cluster}


def fmt(name: str, sc: dict) -> str:
    return (f"| {name} | {sc['positive']} | {sc['tp']} | {sc['fp']} | {sc['fn']} | {sc['precision']:.3f} | {sc['recall']:.3f} | {sc['f1']:.3f} | "
            f"{sc['tp_cluster']} | {sc['tp_evidence']} |")


HEAD = ["| 방법 | '있음' | TP | FP | FN | precision | recall | F1 | 근거까지 맞음(군집 O) | 라벨 근거 기사와 같음 |", "|---|---|---|---|---|---|---|---|---|---|"]


# ------------------------------------------------------------------ 3. 학습 판정 (후보 기사 단위 → 사건 단위)
FEATS = ("sig_done", "sig_prog", "sig_new", "conf", "overlap", "object_hits", "identity_hits", "same_kind", "unique_path", "emb", "kw", "title_sentence",
         "commit", "demand", "rank", "p_source", "p_operational", "p_firm", "days_occ", "days_src", "days_promise", "link_score", "pinned", "n_promises",
         "ev_has_src", "p_actor_resp", "actor_in_text", "src_x_psource", "cos_src")


def pair_features(ev: dict, c: dict, r: dict) -> list[float]:
    p = ev["promises"].get(r.get("pid"), {})
    lg = lambda d: math.copysign(math.log1p(abs(d)), d) if d is not None else 0.0  # noqa: E731
    pub = c["published"]
    has_src = any(ev["promises"][i]["is_source"] for i in ev["sets"]["all"])
    resp = [x for x in ev.get("resp", []) if len(x) >= 2]
    actor = p.get("actor") or ""
    actor_resp = bool(actor) and any(actor in x or x in actor for x in resp)
    text = (c.get("title") or "") + " " + (c.get("hilight") or "")
    actor_in = any(x in text for x in resp) or (len(actor) >= 2 and actor in text)
    return [
        float(r["signal"] == "완료"), float(r["signal"] == "진행"), float(r["signal"] == "새로운문제"), r["conf"], r.get("overlap", 0), r.get("object_hits", 0),
        r.get("identity_hits", 0), float(bool(r.get("same_kind"))), float(r.get("path") == "unique-word"), r.get("emb", 0.0), r.get("kw", 0.0),
        float(bool(r.get("title_sentence"))), float(bool(r.get("commit"))), float(bool(r.get("demand"))), r.get("rank", 0),
        float(bool(p.get("is_source"))), float(bool(p.get("operational"))), float(p.get("strength") == "확약"),
        lg((pub - ev["occurred"]).days), lg((pub - ev["src_date"]).days if ev["src_date"] else None), lg((pub - p["date"]).days if p.get("date") else None),
        c["link_score"], float(c["pinned"]), float(len(ev["sets"]["all"])),
        float(has_src), float(actor_resp), float(actor_in), float(has_src and bool(p.get("is_source"))), float(c.get("cos_src", 0.0)),
    ]


def _model(kind: str):
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    if kind == "lr":
        return make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=3000, class_weight="balanced"))
    return HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=20, random_state=0, class_weight="balanced")


def learning_rows(events: list[dict], set_name: str, variant: str, cutoff: str, grace: int, min_conf: float, pos_def: str = "evidence_or_cluster") -> list[tuple]:
    """학습 행: 규칙이 양성 신호를 낸 후보(기준일 통과). 정답 1 = 라벨 '있음' 사건의 근거 기사(pos_def=evidence) 또는 근거·군집 O 기사,
    0 = 라벨 '없음' 사건의 기사. 라벨 '있음' 사건의 그 밖의 기사는 정답을 모르므로 학습에서 뺀다(예측은 한다)."""
    rows = []
    for ev in events:
        ev["_set"] = set_name
        if not ev["sets"].get(set_name):
            continue
        for c in ev["cands"]:
            r = c["res"].get((set_name, variant))
            if r is None or not valid(r, min_conf) or not cutoff_ok(ev, c, r, cutoff, grace):
                continue
            y = None
            if not ev["label"]:
                y = 0
            elif c["evidence"] or (pos_def == "evidence_or_cluster" and c["cluster"] == "O"):
                y = 1
            rows.append((ev, c, r, pair_features(ev, c, r), y))
    return rows


def _event_f1(events: list[dict], probs: dict, t: float) -> float:
    pos = {ev["key"]: [c for c in ev["cands"] if probs.get((ev["key"], c["news_id"]), -1.0) >= t] for ev in events}
    return event_scores(events, pos)["f1"]


GRID = tuple(float(round(x, 2)) for x in np.arange(0.2, 0.86, 0.05))
LINK_FLOORS = (0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8)


def link_floor(t: float):
    """근거 기사의 연결 확률(재순위 확률) 바닥 — 관리자 확정·수동 연결은 통과, 재순위 바닥 규칙으로 남은 늦은 기사는 완료·새로운문제만 (judge.classify_followups 와 같은 규칙)."""
    return lambda ev, c, r: c["link_score"] >= t or c["pinned"] or (c.get("keep_late", False) and r["signal"] in ("완료", "새로운문제"))


def learned_nested(events: list[dict], set_name: str, variant: str, cutoff: str, grace: int, min_conf: float, kind: str = "lr",
                   pos_def: str = "evidence_or_cluster") -> tuple[dict[str, list[dict]], list[float], dict]:
    """학습 판정의 정직한 교차검증: 바깥 5-fold 마다 안쪽 교차검증(학습 fold 안)으로 문턱을 고르고, 바깥 평가 fold 에 적용한다.
    반환: 사건 → '있음' 근거 후보, fold 별 문턱, 후보별 확률(바깥 평가 fold 모델)."""
    rows = learning_rows(events, set_name, variant, cutoff, grace, min_conf, pos_def)
    pos: dict[str, list[dict]] = {}
    ts: list[float] = []
    probs_out: dict = {}
    for k in range(5):
        tr_events = [e for e in events if e["fold"] != k]
        inner: dict = {}
        for j in sorted({e["fold"] for e in tr_events}):
            fit = [x for x in rows if x[0]["fold"] not in (k, j) and x[4] is not None]
            pred = [x for x in rows if x[0]["fold"] == j]
            if not fit or not pred or len({x[4] for x in fit}) < 2:
                continue
            m = _model(kind).fit(np.asarray([x[3] for x in fit]), np.asarray([x[4] for x in fit]))
            for (ev, c, _, _, _), q in zip(pred, m.predict_proba(np.asarray([x[3] for x in pred]))[:, 1], strict=True):
                inner[(ev["key"], c["news_id"])] = float(q)
        t = max(GRID, key=lambda t: (_event_f1(tr_events, inner, t), -abs(t - 0.5)))
        ts.append(t)
        fit = [x for x in rows if x[0]["fold"] != k and x[4] is not None]
        te = [x for x in rows if x[0]["fold"] == k]
        m = _model(kind).fit(np.asarray([x[3] for x in fit]), np.asarray([x[4] for x in fit]))
        if te:
            for (ev, c, _, _, _), q in zip(te, m.predict_proba(np.asarray([x[3] for x in te]))[:, 1], strict=True):
                probs_out[(ev["key"], c["news_id"])] = float(q)
        for ev in [e for e in events if e["fold"] == k]:
            pos[ev["key"]] = [c for c in ev["cands"] if probs_out.get((ev["key"], c["news_id"]), -1.0) >= t]
    return pos, ts, probs_out


def cv(settings) -> Path:
    with CANDS.open("rb") as f:
        blob = pickle.load(f)
    events = blob["events"]
    grace = blob["settings"]["grace"]
    min_conf = blob["settings"]["min_conf"]
    lines = [f"# 2단계 — 후속 신호 판정 비교 ({datetime.now().strftime('%Y-%m-%d %H:%M')})", "",
             f"- 후보: `{CANDS.relative_to(BACKEND_DIR)}` — 지금 실험 DB(재순위 켬)의 활성 연결 중 사건 발생일 이후 기사 {sum(len(e['cands']) for e in events)}건, 사건 라벨 {len(events)}건 (있음 {sum(e['label'] for e in events)})",
             "- 잣대: 사건 라벨 precision / recall / F1, 근거까지 맞음 = '있음' 근거 기사 중 군집 라벨 O 가 있는 TP 사건 수, 라벨 근거 기사와 같음 = 근거 기사가 사람_근거_news_id 에 있는 TP 사건 수",
             "- 규칙 변형(v2 = 지금): fix = 표현 고침('추가 피해 최소화' 는 새 문제 아님 · '내년 2월 재개장' 은 완료 아님) · skip = 다짐·요구 문장의 진행 표현 제외 · match = 약속 대상어 겹침만 (사건 이름 겹침은 같은 종류 행동어가 있을 때) · -uw = 고유어 경로 끔",
             "- 약속 묶음: all = 추적 약속 전부(지금) · no_op = 응급 대응 약속(진화·수색·감식·총력 …, 조치어 없음) 제외 · src = 원천 기사의 약속(없으면 no_op)",
             "- 후속 기사 기준일: current = max(발생+7일, 가장 이른 약속 발언일)(지금) · source = max(발생+7일, 원천 발언일) · source_next = 원천 발언 다음 날부터 · "
             "promise_each = 신호가 가리킨 약속 발언일 뒤 + 발생+7일 · promise_each_next = 신호가 가리킨 약속 발언 다음 날부터 · both_next = 원천 발언과 그 약속 발언 둘 다의 다음 날부터", ""]
    # 2-1 지금 재현
    base = event_scores(events, decide_events(events, "all", "v2", "current", grace, min_conf))
    lines += ["## 1. 지금 판정 재현", "", *HEAD, fmt("지금 (all · v2 · current)", base), ""]
    # 2-2 격자 (모든 조합, 전체 사건 — 규칙은 90건 전체를 보고 만들었으므로 낙관적)
    grid = []
    for sname in PROMISE_SETS:
        for v in RULE_VARIANTS:
            for cut in ("current", "source", "source_next", "promise_each", "promise_each_next", "both_next"):
                sc = event_scores(events, decide_events(events, sname, v, cut, grace, min_conf))
                grid.append(((sname, v, cut), sc))
    grid.sort(key=lambda x: (-x[1]["f1"], -x[1]["tp_cluster"]))
    lines += ["## 2. 규칙 조합 (사건 90건 전체 — 규칙을 이 사건들을 보며 만들었으니 낙관적, 상위 15)", "", *HEAD]
    lines += [fmt(" · ".join(k), sc) for k, sc in grid[:15]]
    # 한 가지씩 바꾼 효과
    lines += ["", "### 한 가지씩 바꾼 효과 (지금에서 하나만 바꿈)", "", *HEAD]
    for name, cfg in [("약속 묶음 no_op", ("no_op", "v2", "current")), ("약속 묶음 src", ("src", "v2", "current")), ("규칙 fix", ("all", "fix", "current")),
                      ("규칙 fix+skip", ("all", "fix+skip", "current")), ("규칙 fix+match", ("all", "fix+match", "current")),
                      ("규칙 fix+skip+match", ("all", "fix+skip+match", "current")), ("규칙 fix+skip+match-uw", ("all", "fix+skip+match-uw", "current")),
                      ("기준일 source", ("all", "v2", "source")), ("기준일 source_next", ("all", "v2", "source_next")),
                      ("기준일 promise_each", ("all", "v2", "promise_each")), ("기준일 promise_each_next", ("all", "v2", "promise_each_next")),
                      ("기준일 both_next", ("all", "v2", "both_next")), ("기준일 both_next + 규칙 fix", ("all", "fix", "both_next"))]:
        lines.append(fmt(name, event_scores(events, decide_events(events, *cfg, grace, min_conf))))
    # 근거 기사의 연결 확률 바닥 (재순위 확률 — 확정 연결은 그대로)
    lines += ["", "### 근거 기사의 연결 확률 바닥 (기준일 both_next · 규칙 fix · 바닥 규칙으로 남은 늦은 기사는 완료·새로운문제만)", "", *HEAD]
    for t in LINK_FLOORS:
        lines.append(fmt(f"연결 확률 ≥ {t}", event_scores(events, decide_events(events, "all", "fix", "both_next", grace, min_conf, link_floor(t)))))
    # 바닥 문턱도 교차검증으로 고르기 (학습 fold F1 → 평가 fold)
    cv_floor, picked = {}, []
    for k in range(5):
        tr = [e for e in events if e["fold"] != k]
        te = [e for e in events if e["fold"] == k]
        t = max(LINK_FLOORS, key=lambda t: (event_scores(tr, decide_events(tr, "all", "fix", "both_next", grace, min_conf, link_floor(t)))["f1"], -abs(t - 0.6)))
        picked.append(t)
        cv_floor.update(decide_events(te, "all", "fix", "both_next", grace, min_conf, link_floor(t)))
    floor_cv = event_scores(events, cv_floor)
    # 2-3 규칙 조합을 교차검증으로 고르기
    combos = [k for k, _ in grid]
    chosen, cv_pos = [], {}
    for k in range(5):
        tr = [e for e in events if e["fold"] != k]
        te = [e for e in events if e["fold"] == k]
        best = max(combos, key=lambda cfg: (event_scores(tr, decide_events(tr, *cfg, grace, min_conf))["f1"], cfg == ("all", "v2", "current")))
        chosen.append(best)
        cv_pos.update(decide_events(te, *best, grace, min_conf))
    sc_rule_cv = event_scores(events, cv_pos)
    lines += ["", "## 3. 교차검증 비교 (사건 단위 5-fold)", "", *HEAD, fmt("지금", base), fmt("규칙 조합 90가지 (학습 fold F1 로 고름)", sc_rule_cv),
              fmt(f"기준일 both_next + 규칙 fix + 연결 확률 바닥 (문턱을 학습 fold 로 고름: {picked})", floor_cv)]
    # 2-4 학습 판정 — 바깥 5-fold · 안쪽 교차검증으로 문턱 선택 (정직한 추정). 설정은 미리 정한 몇 가지만 비교한다
    learned = {}
    for (sname, v, cut) in [("all", "v2", "both_next"), ("all", "fix", "both_next"), ("all", "fix", "promise_each_next"), ("all", "fix+skip+match", "both_next"),
                            ("no_op", "fix", "both_next")]:
        for kind in ("lr", "hgb"):
            for pos_def in ("evidence_or_cluster", "evidence"):
                pos, ts, _ = learned_nested(events, sname, v, cut, grace, min_conf, kind, pos_def)
                learned[(sname, v, cut, kind, pos_def)] = (event_scores(events, pos), ts)
    for key, (sc, ts) in sorted(learned.items(), key=lambda x: (-x[1][0]["f1"], -x[1][0]["tp_cluster"])):
        lines.append(fmt(f"학습 판정 {key[3]} · {key[0]} · {key[1]} · {key[2]} · 정답={'근거' if key[4] == 'evidence' else '근거+군집O'} · 문턱 {ts}", sc))
    lines += ["", f"- 규칙 조합의 fold 별 선택: {chosen}"]
    out = settings.reports_dir / f"followup_cv_{datetime.now().strftime('%Y%m%d')}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\n-> {out}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--extract", action="store_true")
    ap.add_argument("--cv", action="store_true")
    args = ap.parse_args()
    if not (args.extract or args.cv):
        args.extract = args.cv = True
    settings = get_settings()
    if args.extract:
        extract(settings)
    if args.cv:
        cv(settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
