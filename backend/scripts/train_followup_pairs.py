"""3단계 연구 — 후속 판정을 기사 단위 학습으로 (docs/연구.md 3.8, docs/EXPERIMENT.md §11).

사건 라벨(90건)은 "원천 발언에서 약속한 그 조치가 발언 뒤에 보도됐는가" 를 묻는다. 규칙 판정(rules-v3)은 사건의 모든 인용문 약속을
기사 문장과 겹침으로 맞춰 보므로, 다른 주체·다른 조치의 기사와 원천 발언이 약속이 아닌 사건에서 틀린 '있음' 이 났다(3.8 진단).
여기서는 후보 기사마다 "이 기사가 원천 발언의 조치를 전하는가" 를 학습한 확률로 매기고, 사건의 최댓값으로 '있음' 을 정한다.

  --extract  지금 DB 에서 사건 90건의 후보 기사(연결 전부 — 활성·검토·해제 표시), 규칙 판정(지금 기본값 · 원천 발언만 약속으로 본 것),
             임베딩 유사도, 학습용 기사 정답을 모아 data/experiment/followup_pairs.pkl 로 저장한다.
  --check    추출본으로 지금 판정(규칙 + 기준일 + 확률 바닥)을 재현해 DB 와 같은지 본다.
  --cv       기사 단위 학습 → 사건 최댓값 · 규칙과 섞기 · 맹검 에이전트 판정의 일치 → reports/followup_pairs_cv_<날짜>.md
  --cross-eval data/experiment/followup_cross_quote_unknown0.csv   교차 인코더(train_followup_cross.py) 바깥 fold 점수 평가

기사 단위 정답(학습에만 쓰고 평가에는 쓰지 않는다 — 모두 에이전트 라벨에서 끌어낸 것):
  1 = 라벨 '있음' 사건의 라벨 근거 기사(사람_근거_news_id) 와 그 전재(같은 날 ±1일 · 제목 2-글자 자카드 ≥ 0.5)
  0 = 라벨 '없음' 사건의 후보 기사 (사건 라벨이 "그런 후속이 없다" 고 했으므로)
  없음 = 라벨 '있음' 사건의 그 밖의 후보 (후속일 수도 아닐 수도 있다 — 학습에서 빼거나 약한 0)
평가는 언제나 사건 라벨 90건, 사건 단위 5-fold(rerank_pairs.csv 의 fold) 다.
"""

from __future__ import annotations

import argparse
import csv
import math
import pickle
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA = BACKEND_DIR / "data" / "experiment"
PAIRS = DATA / "followup_pairs.pkl"
GOLD = BACKEND_DIR / "gold"
POSITIVE = {"완료", "진행", "새로운문제"}
NEWS_ID_RE = re.compile(r"\b\d{8}\.\d{14,20}\b")
# 원천 발언의 유형 — 다짐(기관이 하겠다) / 지시·요청(남에게 하라) / 설명(그 밖)
COMMIT_RE = re.compile(r"(하겠|할\s*계획|할\s*예정|예정이다|예정입니다|방침|키로|하기로|추진하겠|마련하겠|강구할|노력하겠|최선을 다하|만전을 기하|총력을 다하겠|지원하겠|챙기겠)")
DIRECT_RE = re.compile(r"(하라|해라|하십시오|해달라|해 달라|바란다|당부|지시|요청했|촉구|주시기|해야 한다|해야 할)")


def source_type(quote: str) -> str:
    c, d = bool(COMMIT_RE.search(quote or "")), bool(DIRECT_RE.search(quote or ""))
    return "commit" if c and not d else "commit+direct" if c else "direct" if d else "explain"


def _bigrams(t: str) -> set[str]:
    t = re.sub(r"[^가-힣A-Za-z0-9]", "", t or "")
    return {t[i:i + 2] for i in range(len(t) - 1)}


def jaccard(a: str, b: str) -> float:
    x, y = _bigrams(a), _bigrams(b)
    return len(x & y) / len(x | y) if x and y else 0.0


def news_hhmm(news_id: str) -> int | None:
    """news_id 의 시각(HHMM) — '언론사.YYYYMMDDHHMMSS…'."""
    m = re.match(r"^\d{8}\.(\d{8})(\d{4})", news_id or "")
    return int(m.group(2)) if m else None


def load_labels() -> tuple[dict, dict]:
    labs = {r["사건키"]: r for r in csv.DictReader((GOLD / "experiment_labels.csv").open(encoding="utf-8-sig"))}
    folds: dict[str, int] = {}
    pairs = DATA / "rerank_pairs.csv"
    if pairs.exists():
        for r in csv.DictReader(pairs.open(encoding="utf-8")):
            folds[r["event_key"]] = int(r["fold"])
    return labs, folds


# ------------------------------------------------------------------ 1. 추출
def _rule(llm, settings, inp) -> dict:
    from app.llm.client import validate_span

    sig, det = llm.classify_followup_detail(inp)
    sig = validate_span(sig, inp)
    return {"signal": sig.signal, "conf": float(sig.confidence), "matches": bool(sig.matches_promise and sig.promise_id is not None), "pid": sig.promise_id,
            "span": sig.evidence_span or "", **det}


def extract(settings) -> Path:
    from app.db.models import Article, Event
    from app.db.session import init_db, session_factory
    from app.llm.rules_adapter import RulesLLM
    from app.llm.schemas import FollowupInput, PromiseInput, PromiseRef
    from app.pipeline.embeddings import build_embedding, from_bytes, needs_recompute
    from app.pipeline.judge import kept_late, link_prob, trackable_promises, usable_followup_link
    from app.rules import weights as W
    from app.rules.orgs import canonical_org

    t0 = time.time()
    labs, folds = load_labels()
    full = {(r["사건키"], r["news_id"]): r["사람_OK(O/X)"].strip() for r in csv.DictReader((GOLD / "cluster_full.csv").open(encoding="utf-8-sig"))}
    init_db()
    s = session_factory()()
    emb = build_embedding(settings)
    llm = RulesLLM(settings, emb)
    out = []
    for ev in s.query(Event).filter(Event.active.is_(True)).order_by(Event.key).all():
        lab = labs.get(ev.key)
        if lab is None or lab["사람_후속보도(O/X)"].strip() not in ("O", "X"):
            continue
        label = lab["사람_후속보도(O/X)"].strip() == "O"
        evid = set(lab["사람_근거_news_id"].split())
        memo_ids = set(NEWS_ID_RE.findall(lab["사람_메모"])) - evid
        src = s.get(Article, ev.source_news_id) if ev.source_news_id else None
        src_date = src.published_at if src is not None else None
        src_quote = (ev.source_quotation or lab.get("원천_발언") or "").strip()
        speakers = sorted({q.source for q in ev.quotations if q.news_id == ev.source_news_id and q.source})
        resp = sorted({x for x in [ev.responsible_org, canonical_org(ev.responsible_org or ""), *(ev.org_aliases or [])] if x and len(x) >= 2})
        sp_orgs = sorted({canonical_org(x) for x in speakers if canonical_org(x)} - {""})
        # 원천 발언을 약속 하나로 본다: 규칙 약속 추출이 다짐을 찾으면 그 행동 절, 아니면 발언 전체(앞 120자)
        ext = llm.extract_promise(PromiseInput(event_summary=ev.summary or ev.title, responsible_org=ev.responsible_org or "", source=speakers[0] if speakers else "",
                                               quotation=src_quote, article_title=src.title if src is not None else "",
                                               published_at=(src_date or ev.occurred_at).isoformat()))
        src_action = (ext.action or "").strip() if ext.is_promise and ext.action else ""
        proms = trackable_promises(ev)
        pmeta = {}
        for p in proms:
            a = s.get(Article, p.evidence_news_id)
            ids = {p.evidence_news_id, *(p.merged_news_ids or [])}
            pmeta[p.id] = {"action": p.action, "actor": p.actor_org, "strength": p.strength, "date": a.published_at if a is not None else None,
                           "is_source": bool(ev.source_news_id and ev.source_news_id in ids)}
        refs = [PromiseRef(id=p.id, actor_org=p.actor_org, action=p.action, deadline_date=p.deadline_date.isoformat() if p.deadline_date else None) for p in proms]
        ref_srcq = [PromiseRef(id=-1, actor_org=ev.responsible_org or "", action=src_quote[:120] or ev.title)]
        ref_srca = [PromiseRef(id=-2, actor_org=ev.responsible_org or "", action=src_action)] if src_action else None
        identity = sorted({*(W.text_tokens(ev.facility or "")), *(W.region_terms(ev.region or "")), *([ev.facility] if ev.facility else [])})
        quotes: dict[str, list[tuple[str, str]]] = {}
        for q in ev.quotations:
            quotes.setdefault(q.news_id, []).append((q.source or "", q.quotation or ""))
        texts = [src_quote or ev.title, *[pmeta[p.id]["action"] for p in proms]]
        vecs = np.asarray(emb.encode(texts))
        qv, pv = vecs[0], vecs[1:]
        ev_arts = []
        for l in ev.links:
            a = s.get(Article, l.news_id)
            if a is None or a.published_at is None or a.published_at < ev.occurred_at or a.change_status == "cancelled":
                continue
            ev_arts.append((a, l))
        ev_arts.sort(key=lambda x: (x[0].published_at, x[0].news_id))
        evid_rows = [(a.title or "", a.published_at) for a, _ in ev_arts if a.news_id in evid]
        cands = []
        for a, l in ev_arts:
            ok = not needs_recompute(a.embedding, a.embedding_backend, emb)
            av = from_bytes(a.embedding) if ok else None
            syn = a.news_id not in evid and any(abs((a.published_at - d).days) <= 1 and jaccard(a.title or "", t) >= 0.5 for t, d in evid_rows)
            row = {
                "news_id": a.news_id, "published": a.published_at, "hhmm": news_hhmm(a.news_id), "title": a.title or "", "hilight": a.hilight or a.content_200 or "",
                "kw": list(a.tms_keywords or [])[:30] if isinstance(a.tms_keywords, list) else [],
                "active": bool(l.is_active), "review": bool(l.needs_review and not l.rejected), "rejected": bool(l.rejected),
                "usable": bool(usable_followup_link(l, settings)), "p": link_prob(l), "link_score": float(l.link_score or 0.0), "keep_late": kept_late(l),
                "pinned": bool(l.confirmed or l.link_method == "manual"), "method": l.link_method or "",
                "after_src": bool(src_date is None or a.published_at > src_date),
                "evidence": a.news_id in evid, "syn": bool(syn), "memo_id": a.news_id in memo_ids, "cluster": full.get((ev.key, a.news_id), "-"),
                "quotes": quotes.get(a.news_id, []),
                "cos_src": float(np.dot(av, qv)) if ok else 0.0,
                "cos_prom": float(np.max(pv @ av)) if ok and len(pv) else 0.0,
                "res": {},
            }
            base = dict(event_summary=ev.summary or ev.title, article_title=a.title or "", hilight=row["hilight"],
                        quotations=[f"{x}: {y}" for x, y in row["quotes"]], published_at=a.published_at.isoformat(), term_weights=ev.term_weights or {},
                        identity_terms=identity)
            if refs:
                row["res"]["all"] = _rule(llm, settings, FollowupInput(promises=refs, **base))
            row["res"]["srcq"] = _rule(llm, settings, FollowupInput(promises=ref_srcq, **base))
            if ref_srca:
                row["res"]["srca"] = _rule(llm, settings, FollowupInput(promises=ref_srca, **base))
            cands.append(row)
        out.append({
            "key": ev.key, "id": ev.id, "title": ev.title, "occurred": ev.occurred_at, "src_date": src_date, "src_news_id": ev.source_news_id,
            "src_hhmm": news_hhmm(ev.source_news_id or ""), "src_quote": src_quote, "src_type": source_type(src_quote), "src_action": src_action,
            "src_is_promise": bool(ext.is_promise), "speakers": speakers, "speaker_orgs": sp_orgs, "resp": resp, "incident": ev.incident_type or "",
            "region": ev.region or "", "facility": ev.facility or "", "label": label, "fold": folds.get(ev.key, 0), "evidence": sorted(evid),
            "memo": lab["사람_메모"], "promises": pmeta, "cands": cands,
        })
        n_pos = sum(c["evidence"] or c["syn"] for c in cands if c["after_src"])
        print(f"  {ev.key} 후보 {len(cands)} (활성 {sum(c['active'] for c in cands)}) · 약속 {len(proms)} · 원천 {out[-1]['src_type']} · 라벨 {'O' if label else 'X'}"
              f" · 정답 1 후보 {n_pos} [{time.time() - t0:.0f}s]", flush=True)
    s.close()
    DATA.mkdir(parents=True, exist_ok=True)
    with PAIRS.open("wb") as f:
        pickle.dump({"events": out, "extracted_at": datetime.now().isoformat(),
                     "settings": {k: getattr(settings, k) for k in ("signal_min_conf", "followup_min_link_prob", "followup_cutoff", "followup_expression_fixes")}}, f)
    print(f"추출: 사건 {len(out)} · 후보 {sum(len(e['cands']) for e in out)} → {PAIRS} [{time.time() - t0:.0f}s]")
    return PAIRS


def load_pairs() -> dict:
    with PAIRS.open("rb") as f:
        return pickle.load(f)


# ------------------------------------------------------------------ 2. 지금 판정 재현
def production_positive(ev: dict, c: dict, min_conf: float) -> bool:
    """judge.followup_articles + classify_followups + rules.status.is_valid 와 같은 규칙으로 이 후보가 유효 신호인가."""
    r = c["res"].get("all")
    if r is None or not c["usable"] or not c["after_src"]:
        return False
    if not (r["matches"] and r["conf"] >= min_conf and r["signal"] in POSITIVE):
        return False
    pdate = ev["promises"].get(r["pid"], {}).get("date")
    if pdate is not None and c["published"] <= pdate:
        return False  # 약속 발언일 이전·당일 기사의 신호는 '불명확' 으로 내린다
    if r["signal"] == "진행" and c["keep_late"] and c["p"] is not None and c["p"] < 0.6 and not c["pinned"]:
        return False  # 재순위 바닥 규칙으로 남은 늦은 기사의 진행
    return True


def scores(events: list[dict], positive: dict[str, bool]) -> dict:
    tp = sum(1 for e in events if positive[e["key"]] and e["label"])
    fp = sum(1 for e in events if positive[e["key"]] and not e["label"])
    fn = sum(1 for e in events if not positive[e["key"]] and e["label"])
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {"positive": tp + fp, "tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0}


def check() -> None:
    blob = load_pairs()
    evs = blob["events"]
    mc = blob["settings"]["signal_min_conf"]
    pos = {e["key"]: any(production_positive(e, c, mc) for c in e["cands"]) for e in evs}
    sc = scores(evs, pos)
    print(f"지금 판정 재현: '있음' {sc['positive']} · TP {sc['tp']} · FP {sc['fp']} · FN {sc['fn']} · P {sc['precision']:.3f} R {sc['recall']:.3f} F1 {sc['f1']:.3f}")
    n = sum(len(e["cands"]) for e in evs)
    cand = [c for e in evs for c in e["cands"] if c["active"] and c["usable"] and c["after_src"]]
    lab1 = sum(1 for e in evs for c in e["cands"] if e["label"] and (c["evidence"] or c["syn"]) and c["active"] and c["usable"] and c["after_src"])
    lab0 = sum(1 for e in evs for c in e["cands"] if not e["label"] and c["active"] and c["usable"] and c["after_src"])
    print(f"후보 전체 {n} · 판정 후보(활성·바닥 통과·원천 발언 뒤) {len(cand)} · 정답 1 {lab1} · 정답 0 {lab0}")
    print("원천 발언 유형:", {t: sum(1 for e in evs if e["src_type"] == t) for t in ("commit", "commit+direct", "direct", "explain")},
          "· 원천 발언 약속 추출됨", sum(e["src_is_promise"] for e in evs))


# ------------------------------------------------------------------ 3. 기사 단위 학습 → 사건 최댓값
DONE_RE = re.compile(r"(완료|마쳤|마무리|끝냈|준공|완공|개통|재개장|재개통|정상\s*운영|정상화|복구했|설치했|교체했)")
PROG_RE = re.compile(r"(추진|착수|착공|진행|공사|시행|시작|돌입|본격|가동|투입)")
PLAN_RE = re.compile(r"(예정|계획|추진할|검토|방침|하겠다|키로|하기로)")
EMERG_RE = re.compile(r"(수색|진화|구조|감식|압수수색|입건|구속|영장|수사|부검|실종|사망자|희생자|발인|빈소|합동분향소)")
OPINION_RE = re.compile(r"(\[사설\]|사설>|\[칼럼\]|\[기고\]|기자수첩|\[시론\]|\[오피니언\]|\[데스크)")
AGAIN_RE = re.compile(r"(또다시|또\s|다시\s|재발|재차|잇따|되풀이)")
EMERG_ACTION_RE = re.compile(r"(진화|수색|구조|감식|대피|총력|만전|최선을\s*다|동원|투입|가용|원인\s*(을\s*)?(규명|조사|파악)|수사|입건|조사할|조사하겠|순찰)")
SIGS = ("완료", "진행", "새로운문제", "불명확", "무관")
SRC_TYPES = ("commit", "commit+direct", "direct", "explain")
INCIDENTS = ("사고>산업사고", "사고>교통사고", "재해>자연재해")


def candidates(ev: dict) -> list[dict]:
    """판정 후보 — 지금 제품과 같은 범위: 활성 연결 · 확률 바닥 통과 · 원천 발언 다음 날부터."""
    return [c for c in ev["cands"] if c["active"] and c["usable"] and c["after_src"]]


AGENT_LABELS = GOLD / "followup_article_agent.csv"  # 맹검 에이전트 기사 판정 855건 (3.8 ⑤, 학습용 — 평가에는 쓰지 않는다)


def load_agent_labels(path: Path = AGENT_LABELS) -> dict[tuple[str, str], dict]:
    """에이전트 기사 판정(맹검, 3.8) — (사건키, news_id) → {"q1": Y/N/?, "q2": …}. 없으면 빈 dict."""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig") as f:
        return {(r["event_key"], r["news_id"]): r for r in csv.DictReader(f)}


def article_label(ev: dict, c: dict, mode: str = "unknown0", agent: dict | None = None) -> int | None:
    """학습용 기사 정답. mode:
      base     — 근거·전재 1, '없음' 사건 후보 0, '있음' 사건의 나머지 None (사건 자체로 정답이 갈려 원천 발언만 보고 외우는 지름길이 생긴다)
      unknown0 — base + '있음' 사건의 나머지 0 (같은 원천 발언 안에서 후속과 아닌 기사를 가르게 한다)
      agent    — unknown0 + 에이전트 엄격 판정(q1)으로 '있음' 사건의 나머지를 1/0, '없음' 사건에서 에이전트가 Y 로 본 기사는 None(사건 라벨과 어긋남)"""
    if mode in ("agent", "agent2") and agent is not None:
        q = "q1" if mode == "agent" else "q2"  # agent = 엄격(원천 발언의 그 조치), agent2 = 넓게(그 사건에 대한 기관 대응)
        a = agent.get((ev["key"], c["news_id"]))
        if not ev["label"]:
            return None if a is not None and a.get(q) == "Y" else 0
        if c["evidence"] or c["syn"]:
            return 1
        if a is not None and a.get(q) in ("Y", "N"):
            return 1 if a[q] == "Y" else 0
        return 0
    if not ev["label"]:
        return 0
    if c["evidence"] or c["syn"]:
        return 1
    return 0 if mode in ("unknown0", "agent", "agent2") else None


def _rule_feats(r: dict | None) -> list[float]:
    if r is None:
        return [0.0] * (len(SIGS) + 8) + [1.0]
    return [*(float(r["signal"] == x) for x in SIGS), r["conf"], float(r["matches"]), float(r.get("overlap", 0)), float(r.get("emb", 0.0) or 0.0),
            float(r.get("kw", 0.0) or 0.0), float(bool(r.get("title_sentence"))), float(bool(r.get("commit"))), float(bool(r.get("demand"))), 0.0]


def features(ev: dict, c: dict, cands: list[dict], min_conf: float) -> list[float]:
    lg = math.log1p
    pub = c["published"]
    d_src = (pub - ev["src_date"]).days if ev["src_date"] else (pub - ev["occurred"]).days
    d_occ = (pub - ev["occurred"]).days
    r = c["res"].get("all")
    p = ev["promises"].get(r["pid"], {}) if r else {}
    text = c["title"] + " " + c["hilight"]
    title = c["title"]
    resp = ev["resp"] + ev["speaker_orgs"]
    actor = (p.get("actor") or "")
    q_resp = any(any(x in (sp or "") or (sp and sp in x) for x in resp) for sp, _ in c["quotes"])
    order = sorted(cands, key=lambda x: (x["published"], x["news_id"]))
    rank = order.index(c) / max(1, len(order) - 1) if c in order else 0.0
    return [
        *(float(ev["src_type"] == t) for t in SRC_TYPES), float(ev["src_is_promise"]), lg(len(ev["promises"])),
        float(any(m["is_source"] for m in ev["promises"].values())), *(float(ev["incident"].startswith(t)) for t in INCIDENTS), lg(len(cands)),
        lg(max(d_src, 0)), lg(max(d_occ, 0)), float(d_src <= 1), float(d_src <= 7), float(d_src <= 30), float(d_src > 365), rank,
        c["p"] if c["p"] is not None else c["link_score"], float(c["keep_late"]), float(c["pinned"]),
        *_rule_feats(r), float(production_positive(ev, c, min_conf)),
        float(r.get("object_hits", 0)) if r else 0.0, float(r.get("identity_hits", 0)) if r else 0.0, float(bool(r.get("same_kind"))) if r else 0.0,
        float(r.get("path") == "unique-word") if r else 0.0,
        float(bool(p.get("is_source"))), float(p.get("strength") == "확약"), float(bool(actor) and any(actor in x or x in actor for x in resp)),
        float(bool(EMERG_ACTION_RE.search(p.get("action") or ""))), float(bool(p.get("date")) and pub > p["date"]),
        *_rule_feats(c["res"].get("srcq")), *_rule_feats(c["res"].get("srca")),
        c["cos_src"], c["cos_prom"],
        float(bool(DONE_RE.search(title))), float(bool(PROG_RE.search(title))), float(bool(PLAN_RE.search(title))), float(bool(EMERG_RE.search(title))),
        float(bool(OPINION_RE.search(title))), float(bool(AGAIN_RE.search(title))),
        float(bool(DONE_RE.search(text))), float(bool(EMERG_RE.search(text))),
        float(q_resp), float(any(x in text for x in resp)), float(bool(c["quotes"])),
    ]


def make_model(kind: str):
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    if kind == "lr":
        return make_pipeline(StandardScaler(), LogisticRegression(C=0.3, max_iter=5000, class_weight="balanced"))
    return HistGradientBoostingClassifier(max_iter=250, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=15, l2_regularization=1.0, random_state=0,
                                          class_weight="balanced")


def build_rows(evs: list[dict], min_conf: float, extra=None, mode: str = "base", agent: dict | None = None) -> list[tuple]:
    """(사건, 후보, 피처, 정답) — 정답 None 은 학습에서 빼거나(weak_neg=0) 약한 0 으로 쓴다. extra(ev, c) → 피처 뒤에 붙일 값 목록."""
    rows = []
    for ev in evs:
        cs = candidates(ev)
        for c in cs:
            f = features(ev, c, cs, min_conf)
            if extra is not None:
                f = f + list(extra(ev, c))
            rows.append((ev, c, f, article_label(ev, c, mode, agent)))
    return rows


def event_max(rows: list[tuple], probs: np.ndarray) -> dict[str, float]:
    out: dict[str, float] = {}
    for (ev, _c, _f, _y), q in zip(rows, probs, strict=True):
        out[ev["key"]] = max(out.get(ev["key"], 0.0), float(q))
    return out


THRESH = tuple(float(round(x, 3)) for x in np.arange(0.05, 0.96, 0.025))


def best_threshold(evs: list[dict], emax: dict[str, float]) -> float:
    def f1(t):
        return scores(evs, {e["key"]: emax.get(e["key"], 0.0) >= t for e in evs})["f1"]
    return max(THRESH, key=lambda t: (f1(t), -abs(t - 0.5)))


def nested_cv(evs: list[dict], fold_of: dict[str, int], kind: str, min_conf: float, weak_neg: float = 0.0, extra=None, mode: str = "base",
              agent: dict | None = None) -> tuple[dict, dict, list[float]]:
    """바깥 5-fold: 안쪽 교차검증(학습 fold 안)으로 문턱 → 바깥 평가 fold 에 적용. weak_neg > 0 이면 정답 None 후보를 그 무게의 0 으로."""
    rows = build_rows(evs, min_conf, extra, mode, agent)
    X = np.asarray([r[2] for r in rows], dtype=float)

    def fit(idx):
        y, w, keep = [], [], []
        for i in idx:
            yy = rows[i][3]
            if yy is None:
                if weak_neg <= 0:
                    continue
                y.append(0); w.append(weak_neg)
            else:
                y.append(yy); w.append(1.0)
            keep.append(i)
        m = make_model(kind)
        if kind == "lr":
            m.fit(X[keep], np.asarray(y), logisticregression__sample_weight=np.asarray(w))
        else:
            m.fit(X[keep], np.asarray(y), sample_weight=np.asarray(w))
        return m

    folds = sorted(set(fold_of.values()))
    decided: dict[str, bool] = {}
    emax_out: dict[str, float] = {}
    ts = []
    for k in folds:
        tr = [i for i, r in enumerate(rows) if fold_of[r[0]["key"]] != k]
        te = [i for i, r in enumerate(rows) if fold_of[r[0]["key"]] == k]
        inner: dict[str, float] = {}
        for j in folds:
            if j == k:
                continue
            a = [i for i in tr if fold_of[rows[i][0]["key"]] != j]
            b = [i for i in tr if fold_of[rows[i][0]["key"]] == j]
            if not b:
                continue
            m = fit(a)
            inner.update(event_max([rows[i] for i in b], m.predict_proba(X[b])[:, 1]))
        tr_evs = [e for e in evs if fold_of[e["key"]] != k]
        t = best_threshold(tr_evs, inner)
        ts.append(t)
        m = fit(tr)
        em = event_max([rows[i] for i in te], m.predict_proba(X[te])[:, 1]) if te else {}
        emax_out.update(em)
        for e in evs:
            if fold_of[e["key"]] == k:
                decided[e["key"]] = em.get(e["key"], 0.0) >= t
    return decided, emax_out, ts


def random_folds(evs: list[dict], seed: int) -> dict[str, int]:
    """라벨 비율을 맞춘 사건 5-fold (다른 나누기로 되풀이해 흔들림을 본다)."""
    rng = np.random.default_rng(seed)
    out = {}
    for lab in (True, False):
        keys = [e["key"] for e in evs if e["label"] == lab]
        rng.shuffle(keys)
        for i, k in enumerate(keys):
            out[k] = i % 5
    return out


# ------------------------------------------------------------------ 4. 규칙 + 학습 점수 혼합 (중첩)
H_GRID = tuple(float(round(x, 3)) for x in np.arange(0.05, 0.96, 0.05))
HYBRIDS = {
    "학습 점수만 (사건 최댓값)": (lambda f, t: f["max"] >= t[0], [(t,) for t in H_GRID]),
    "규칙이 양성으로 본 후보만 다시 채점": (lambda f, t: f["rmax"] >= t[0], [(t,) for t in H_GRID]),
    "(규칙 그리고 점수 ≥ t1) 또는 점수 ≥ t2": (lambda f, t: (f["rules"] and f["max"] >= t[0]) or f["max"] >= t[1], [(a, b) for a in H_GRID for b in H_GRID if b >= a]),
    "규칙 그리고 (원천 발언이 다짐 또는 점수 ≥ t)": (lambda f, t: f["rules"] and (f["commit"] or f["max"] >= t[0]), [(t,) for t in H_GRID]),
}


def hybrid_cv(evs: list[dict], fold_of: dict[str, int], kind: str, min_conf: float, mode: str, weak: float, agent: dict | None) -> dict[str, dict]:
    """기사 모델(kind·정답 mode)의 안쪽 점수로 학습 사건의 문턱을 고르고 평가 fold 에 적용한다. 사건 단위 작은 로지스틱 회귀도 같은 점수로."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    rows = build_rows(evs, min_conf, None, mode, agent)
    X = np.asarray([r[2] for r in rows], dtype=float)
    idx: dict[str, list[int]] = {}
    for i, r in enumerate(rows):
        idx.setdefault(r[0]["key"], []).append(i)
    rpos = np.asarray([production_positive(r[0], r[1], min_conf) for r in rows])
    by_key = {e["key"]: e for e in evs}

    def fit_predict(tr_keys, te_keys):
        tr = [i for k in tr_keys for i in idx.get(k, []) if rows[i][3] is not None or weak > 0]
        te = [i for k in te_keys for i in idx.get(k, [])]
        y = np.asarray([rows[i][3] if rows[i][3] is not None else 0 for i in tr])
        w = np.asarray([1.0 if rows[i][3] is not None else weak for i in tr])
        m = make_model(kind)
        if kind == "lr":
            m.fit(X[tr], y, logisticregression__sample_weight=w)
        else:
            m.fit(X[tr], y, sample_weight=w)
        return dict(zip(te, m.predict_proba(X[te])[:, 1], strict=True)) if te else {}

    def ev_feats(k, prob):
        ii = idx.get(k, [])
        e = by_key[k]
        ps = [prob.get(i, 0.0) for i in ii]
        rp = [prob.get(i, 0.0) for i in ii if rpos[i]]
        return {"rules": bool(rp), "max": max(ps) if ps else 0.0, "rmax": max(rp) if rp else 0.0, "commit": e["src_type"] == "commit",
                "vec": [float(bool(rp)), max(ps) if ps else 0.0, max(rp) if rp else 0.0, math.log1p(len(rp)), float(e["src_type"] == "commit"),
                        float(e["src_type"] == "explain"), float(e["src_type"] in ("direct", "commit+direct")), float(bool(e["promises"])),
                        float(e["src_is_promise"]), math.log1p(len(ii))]}

    keys = [e["key"] for e in evs]
    folds = sorted(set(fold_of.values()))
    out: dict[str, dict] = {name: {} for name in HYBRIDS}
    out["사건 단위 작은 로지스틱 회귀"] = {}
    for k in folds:
        tr = [x for x in keys if fold_of[x] != k]
        te = [x for x in keys if fold_of[x] == k]
        inner: dict = {}
        for j in folds:
            if j != k:
                inner.update(fit_predict([x for x in tr if fold_of[x] != j], [x for x in tr if fold_of[x] == j]))
        outer = fit_predict(tr, te)
        trf = {x: ev_feats(x, inner) for x in tr}
        tef = {x: ev_feats(x, outer) for x in te}
        tr_evs = [by_key[x] for x in tr]
        for name, (rule, grid) in HYBRIDS.items():
            t = max(grid, key=lambda t: (scores(tr_evs, {x: rule(trf[x], t) for x in tr})["f1"], -sum(abs(v - 0.5) for v in t)))  # noqa: B023
            for x in te:
                out[name][x] = rule(tef[x], t)
        m = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=2000, class_weight="balanced"))
        m.fit(np.asarray([trf[x]["vec"] for x in tr]), np.asarray([by_key[x]["label"] for x in tr]))
        for x, q in zip(te, m.predict_proba(np.asarray([tef[x]["vec"] for x in te]))[:, 1], strict=True):
            out["사건 단위 작은 로지스틱 회귀"][x] = bool(q >= 0.5)
    return {name: scores(evs, d) for name, d in out.items()}


# ------------------------------------------------------------------ 5. 맹검 에이전트 판정과 사건 라벨
def agent_check(evs: list[dict], min_conf: float, agent: dict) -> list[str]:
    from sklearn.metrics import cohen_kappa_score

    def a(e, c, q):
        o = agent.get((e["key"], c["news_id"]))
        return o is not None and o[q] == "Y"

    def any_(e, f):
        return any(f(e, c) for c in candidates(e))

    rules = lambda e, c: production_positive(e, c, min_conf)  # noqa: E731
    pol = {
        "지금 규칙": lambda e: any_(e, rules),
        "에이전트 엄격 (원천 발언의 그 조치)": lambda e: any_(e, lambda e, c: a(e, c, "q1")),
        "에이전트 넓게 (그 사건에 대한 기관 대응)": lambda e: any_(e, lambda e, c: a(e, c, "q2")),
        "규칙 그리고 에이전트 넓게 (사건 단위)": lambda e: any_(e, rules) and any_(e, lambda e, c: a(e, c, "q2")),
        "규칙 또는 에이전트 엄격": lambda e: any_(e, rules) or any_(e, lambda e, c: a(e, c, "q1")),
    }
    labels = [e["label"] for e in evs]
    L = ["| 판정 (사건: 후보 중 하나라도 '예') | '있음' | TP | FP | FN | precision | recall | F1 | κ |", "|---|---|---|---|---|---|---|---|---|"]
    for name, f in pol.items():
        pos = {e["key"]: f(e) for e in evs}
        sc = scores(evs, pos)
        k = cohen_kappa_score(labels, [pos[e["key"]] for e in evs])
        L.append(f"| {name} | {sc['positive']} | {sc['tp']} | {sc['fp']} | {sc['fn']} | {sc['precision']:.3f} | {sc['recall']:.3f} | {sc['f1']:.3f} | {k:.2f} |")
    ev_pos = [(e, c) for e in evs for c in candidates(e) if e["label"] and (c["evidence"] or c["syn"]) and (e["key"], c["news_id"]) in agent]
    q1 = sum(a(e, c, "q1") for e, c in ev_pos)
    q2 = sum(a(e, c, "q2") for e, c in ev_pos)
    x_y = sum(1 for e in evs if not e["label"] and any_(e, lambda e, c: a(e, c, "q1")))
    L += ["", f"라벨 근거·전재 기사 {len(ev_pos)}건 중 에이전트 엄격 '예' {q1}건 · 넓게 '예' {q2}건. 라벨 '없음' {sum(not e['label'] for e in evs)}건 중 "
          f"에이전트가 엄격 기준으로 후속 기사를 찾은 사건 {x_y}건."]
    return L


def cv(seeds=(-1, 1, 2, 3, 4, 5), out: Path | None = None) -> Path:
    """reports/followup_pairs_cv_<날짜>.md — 3.8 의 표 전부 (지금 규칙 · 기사 단위 학습 · 혼합 · 에이전트 판정)."""
    blob = load_pairs()
    evs = blob["events"]
    mc = blob["settings"]["signal_min_conf"]
    agent = load_agent_labels()
    base = {e["key"]: e["fold"] for e in evs}
    splits = [base if sd < 0 else random_folds(evs, sd) for sd in seeds]
    t0 = time.time()
    prod = scores(evs, {e["key"]: any(production_positive(e, c, mc) for c in e["cands"]) for e in evs})
    L = [f"# 3단계 — 후속 판정 기사 단위 학습 교차검증 ({datetime.now():%Y-%m-%d %H:%M})", "",
         f"`backend/scripts/train_followup_pairs.py --cv` · 추출 {blob['extracted_at'][:16]} · 사건 {len(evs)} · 판정 후보 {sum(len(candidates(e)) for e in evs)} · "
         f"사건 나누기 {len(splits)}가지(첫째 = rerank_pairs.csv 의 fold). 평가는 언제나 사건 라벨 90건(Claude 에이전트 라벨). 문턱은 학습 fold 안쪽 교차검증.", "",
         f"지금 규칙(rules-v3 + 기준일 + 확률 바닥): '있음' {prod['positive']} · TP {prod['tp']} · FP {prod['fp']} · P {prod['precision']:.3f} · R {prod['recall']:.3f} · "
         f"**F1 {prod['f1']:.3f}**", "", "## 1. 기사 단위 학습 → 사건 최댓값", "",
         "| 기사 정답 | 모델 | 기본 나누기 F1 (P / R) | 나누기 평균 ± 표준편차 |", "|---|---|---|---|"]
    configs = [("base", 0.0, "근거·전재 1 · '없음' 사건 0 · 나머지 뺌"), ("base", 0.3, "위 + 나머지를 무게 0.3 의 0"), ("unknown0", 0.0, "위 + 나머지를 0"),
               ("agent", 0.0, "+ 에이전트 엄격 판정"), ("agent2", 0.0, "+ 에이전트 넓게 판정")]
    for mode, w, desc in configs:
        for kind in ("lr", "gbm"):
            f1s, first = [], None
            for fo in splits:
                dec, _, _ = nested_cv(evs, fo, kind, mc, w, None, mode, agent)
                sc = scores(evs, dec)
                f1s.append(sc["f1"])
                first = first or sc
            L.append(f"| {desc} | {kind} | {first['f1']:.3f} ({first['precision']:.3f} / {first['recall']:.3f}) | {np.mean(f1s):.3f} ± {np.std(f1s):.3f} |")
            print(L[-1], f"[{time.time() - t0:.0f}s]", flush=True)
    L += ["", "## 2. 규칙과 학습 점수 섞기", "", "| 기사 모델 | 방법 | 기본 나누기 F1 (P / R · TP / FP) | 나누기 평균 ± 표준편차 |", "|---|---|---|---|"]
    for mode, kind, w in (("base", "lr", 0.3), ("agent", "gbm", 0.0), ("agent", "lr", 0.0)):
        res: dict[str, list[dict]] = {}
        for fo in splits:
            for name, sc in hybrid_cv(evs, fo, kind, mc, mode, w, agent).items():
                res.setdefault(name, []).append(sc)
        for name, scs in res.items():
            b = scs[0]
            L.append(f"| {mode}·{kind}·{w} | {name} | {b['f1']:.3f} ({b['precision']:.3f} / {b['recall']:.3f} · {b['tp']} / {b['fp']}) | "
                     f"{np.mean([x['f1'] for x in scs]):.3f} ± {np.std([x['f1'] for x in scs]):.3f} |")
            print(L[-1], f"[{time.time() - t0:.0f}s]", flush=True)
    if agent:
        L += ["", "## 3. 맹검 에이전트 판정과 사건 라벨 (학습 아님 — 독립 판정의 일치)", ""] + agent_check(evs, mc, agent)
    out = out or BACKEND_DIR.parent / "reports" / f"followup_pairs_cv_{datetime.now():%Y%m%d}.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"→ {out} [{time.time() - t0:.0f}s]")
    return out


def cross_eval(path: Path) -> None:
    """train_followup_cross.py 의 바깥 fold 점수: 기사 AUC(근거 대 '없음' 사건 후보 / '있음' 사건 안), 사건 최댓값 AUC, 문턱을 다 볼 때의 최고 F1(낙관)."""
    from sklearn.metrics import roc_auc_score

    with path.open(encoding="utf-8") as f:
        sc = {(r["event_key"], r["news_id"]): float(r["p"]) for r in csv.DictReader(f) if r["role"] == "outer"}
    evs = load_pairs()["events"]
    a = [(int(e["label"] and (c["evidence"] or c["syn"])), sc[(e["key"], c["news_id"])]) for e in evs for c in candidates(e)
         if (e["key"], c["news_id"]) in sc and (not e["label"] or c["evidence"] or c["syn"])]
    b = [(int(c["evidence"] or c["syn"]), sc[(e["key"], c["news_id"])]) for e in evs if e["label"] for c in candidates(e) if (e["key"], c["news_id"]) in sc]
    em = {e["key"]: max([sc.get((e["key"], c["news_id"]), 0.0) for c in candidates(e)] or [0.0]) for e in evs}
    best = max((scores(evs, {k: v >= t for k, v in em.items()})["f1"], t) for t in sorted(set(em.values())))
    print(f"{path.name}: 점수 {len(sc)} · 기사 AUC(근거 대 '없음' 사건) {roc_auc_score([y for y, _ in a], [q for _, q in a]):.3f} · "
          f"기사 AUC('있음' 사건 안) {roc_auc_score([y for y, _ in b], [q for _, q in b]):.3f} · 사건 최댓값 AUC "
          f"{roc_auc_score([e['label'] for e in evs], [em[e['key']] for e in evs]):.3f} · 문턱 최적 F1 {best[0]:.3f} (낙관)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--extract", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--cv", action="store_true")
    ap.add_argument("--cross-eval", default=None, help="train_followup_cross.py 의 출력 CSV")
    args = ap.parse_args()
    settings = get_settings()
    if args.extract:
        extract(settings)
    if args.check or args.extract:
        check()
    if args.cv:
        cv()
    if args.cross_eval:
        cross_eval(Path(args.cross_eval))
    return 0


if __name__ == "__main__":
    sys.exit(main())
