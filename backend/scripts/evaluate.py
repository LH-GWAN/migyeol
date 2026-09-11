"""정답 데이터셋(gold/events.yaml) 대비 정확도 측정 -> reports/eval_<날짜>.md

  python scripts/evaluate.py
  python scripts/evaluate.py --sweep link_auto=0.45,0.50,0.55,0.60
  python scripts/evaluate.py --sweep completion_min_conf=0.6,0.7,0.8

지표
  사건 연결   : precision / recall / F1 (자동 연결, 검토 큐 제외)
  약속 추출   : actor_org 정확 일치율, action 유사도(≥0.8) 일치율, deadline 월 단위 일치율, strength 일치율
  상태 판정   : 5-class accuracy + confusion matrix (완료 false positive 별도 보고)
  후속 탐색   : gold followup_news_ids 중 신호가 생성된 비율 (recall)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bigkinds import build_client  # noqa: E402
from app.config import STATUS_DONE, STATUSES, get_settings  # noqa: E402
from app.db.models import Event  # noqa: E402
from app.db.session import init_db, session_factory  # noqa: E402
from app.llm.client import build_llm  # noqa: E402
from app.pipeline.embeddings import build_embedding  # noqa: E402
from app.pipeline.runner import Pipeline  # noqa: E402


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return round(p, 3), round(r, 3), round(f, 3)


def evaluate(session, gold: dict, embedder) -> dict:
    events = {e.key: e for e in session.query(Event).all()}
    link_tp = link_fp = link_fn = 0
    actor_ok = action_ok = deadline_ok = strength_ok = promise_total = promise_found = 0
    status_ok = 0
    confusion: Counter = Counter()
    followup_hit = followup_total = 0
    rows = []

    for g in gold["events"]:
        ev = events.get(g["key"])
        if ev is None:
            rows.append({"key": g["key"], "status": "(없음)", "expected": g["expected_status"], "ok": False})
            continue
        # 연결
        pred = {l.news_id for l in ev.links if not l.rejected and not l.needs_review}
        truth = set(g["linked_news_ids"])
        tp, fp, fn = len(pred & truth), len(pred - truth), len(truth - pred)
        link_tp, link_fp, link_fn = link_tp + tp, link_fp + fp, link_fn + fn
        # 약속
        db_promises = [p for p in ev.promises if p.is_trackable]
        for gp in g["promises"]:
            promise_total += 1
            best = None
            best_sim = 0.0
            if db_promises and gp["action"]:
                vecs = embedder.encode([gp["action"], *[p.action for p in db_promises]])
                sims = vecs[1:] @ vecs[0]
                i = int(np.argmax(sims))
                best, best_sim = db_promises[i], float(sims[i])
                if db_promises[i].action == gp["action"]:
                    best_sim = 1.0
            if best is None or best_sim < 0.8:
                continue
            promise_found += 1
            action_ok += 1
            actor_ok += int(best.actor_org == gp["actor_org"])
            strength_ok += int(best.strength == gp["strength"])
            gd = gp.get("deadline_date")
            bd = best.deadline_date.isoformat() if best.deadline_date else None
            deadline_ok += int((gd or "")[:7] == (bd or "")[:7])
        # 상태
        ok = ev.status == g["expected_status"]
        status_ok += int(ok)
        confusion[(g["expected_status"], ev.status)] += 1
        # 후속 탐색
        sig_ids = {s.news_id for s in ev.signals}
        for nid in g["followup_news_ids"]:
            followup_total += 1
            followup_hit += int(nid in sig_ids)
        rows.append(
            {
                "key": g["key"],
                "title": g["title"],
                "expected": g["expected_status"],
                "status": ev.status,
                "reason": ev.status_reason,
                "ok": ok,
                "link": f"{tp}/{len(truth)} (+{fp} 오연결)",
                "promises": f"{len(db_promises)}/{len(g['promises'])}",
                "priority": ev.priority_score,
            }
        )

    p, r, f = prf(link_tp, link_fp, link_fn)
    done_fp = sum(v for (exp, got), v in confusion.items() if got == STATUS_DONE and exp != STATUS_DONE)
    return {
        "link": {"precision": p, "recall": r, "f1": f, "tp": link_tp, "fp": link_fp, "fn": link_fn},
        "promise": {
            "total": promise_total,
            "found": promise_found,
            "action_match": round(action_ok / promise_total, 3) if promise_total else 0,
            "actor_match": round(actor_ok / promise_total, 3) if promise_total else 0,
            "deadline_month_match": round(deadline_ok / promise_total, 3) if promise_total else 0,
            "strength_match": round(strength_ok / promise_total, 3) if promise_total else 0,
        },
        "status": {"accuracy": round(status_ok / len(gold["events"]), 3), "n": len(gold["events"]), "done_false_positive": done_fp},
        "followup": {"recall": round(followup_hit / followup_total, 3) if followup_total else 0, "hit": followup_hit, "total": followup_total},
        "confusion": {f"{k[0]} -> {k[1]}": v for k, v in sorted(confusion.items())},
        "rows": rows,
    }


def render(result: dict, meta: dict, sweep_rows: list[dict] | None = None) -> str:
    L = []
    L.append(f"# 평가 결과 — {meta['date']}")
    L.append("")
    L.append(f"- 모드: bigkinds=`{meta['bigkinds_mode']}` llm=`{meta['llm_mode']}` embedding=`{meta['embedding']}`")
    L.append(f"- 임계값: {json.dumps(meta['thresholds'], ensure_ascii=False)}")
    L.append(f"- 정답셋: `{meta['gold']}` ({result['status']['n']}건)")
    L.append("")
    L.append("## 요약")
    L.append("")
    L.append("| 지표 | 값 |")
    L.append("|---|---|")
    lk = result["link"]
    L.append(f"| 사건 연결 P / R / F1 | {lk['precision']} / {lk['recall']} / {lk['f1']} (tp {lk['tp']}, fp {lk['fp']}, fn {lk['fn']}) |")
    pr = result["promise"]
    L.append(f"| 약속 탐지(action 유사도≥0.8) | {pr['found']}/{pr['total']} = {pr['action_match']} |")
    L.append(f"| 약속 주체 정확 일치 | {pr['actor_match']} |")
    L.append(f"| 약속 기한 월 단위 일치 | {pr['deadline_month_match']} |")
    L.append(f"| 발언 강도 일치 | {pr['strength_match']} |")
    st = result["status"]
    L.append(f"| 상태 판정 accuracy | {st['accuracy']} (완료 false positive {st['done_false_positive']}건) |")
    fu = result["followup"]
    L.append(f"| 후속 기사 탐색 recall | {fu['recall']} ({fu['hit']}/{fu['total']}) |")
    L.append("")
    L.append("## 상태 confusion (기대 -> 판정)")
    L.append("")
    for k, v in result["confusion"].items():
        L.append(f"- {k}: {v}")
    L.append("")
    L.append("## 사건별")
    L.append("")
    L.append("| 사건 | 기대 상태 | 판정 상태 | 규칙 | 일치 | 연결 | 약속 | 우선순위 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for r in result["rows"]:
        L.append(
            f"| {r.get('title', r['key'])} | {r['expected']} | {r['status']} | {r.get('reason', '')} | {'O' if r['ok'] else 'X'} | {r.get('link', '')} | {r.get('promises', '')} | {r.get('priority', '')} |"
        )
    if sweep_rows:
        L.append("")
        L.append("## 임계값 스윕")
        L.append("")
        L.append("| 값 | 연결 F1 | 상태 accuracy | 완료 FP |")
        L.append("|---|---|---|---|")
        for s in sweep_rows:
            L.append(f"| {s['param']}={s['value']} | {s['link_f1']} | {s['status_acc']} | {s['done_fp']} |")
    L.append("")
    L.append("> 상태는 5개 문자열로만 표시된다. 보도가 없다는 사실은 조치가 없었다는 근거가 아니라 확인이 필요하다는 신호로만 해석한다.")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", default=None, help="예: link_auto=0.45,0.50,0.55")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    settings = get_settings()
    init_db()
    session = session_factory()()
    embedder = build_embedding(settings)
    with open(settings.gold_file, encoding="utf-8") as f:
        gold = yaml.safe_load(f)
    today = settings.today()

    sweep_rows = None
    if args.sweep:
        param, values = args.sweep.split("=")
        original = getattr(settings, param)
        sweep_rows = []
        pipe = Pipeline(session, settings, build_client(settings), build_llm(settings), embedder, today=today)
        for v in values.split(","):
            setattr(settings, param, type(original)(float(v)) if isinstance(original, (int, float)) else v)
            pipe.run("link")
            pipe.run("judge")
            res = evaluate(session, gold, embedder)
            sweep_rows.append({"param": param, "value": v, "link_f1": res["link"]["f1"], "status_acc": res["status"]["accuracy"], "done_fp": res["status"]["done_false_positive"]})
        setattr(settings, param, original)
        pipe.run("link")
        pipe.run("judge")

    result = evaluate(session, gold, embedder)
    meta = {
        "date": date.today().isoformat(),
        "bigkinds_mode": settings.bigkinds_mode,
        "llm_mode": settings.llm_mode,
        "embedding": embedder.name,
        "thresholds": {
            "link_auto": settings.link_auto,
            "link_review": settings.link_review,
            "signal_min_conf": settings.signal_min_conf,
            "completion_min_conf": settings.completion_min_conf,
            "stale_followup_days": settings.stale_followup_days,
        },
        "gold": str(settings.gold_file.relative_to(settings.gold_file.parents[1])),
    }
    md = render(result, meta, sweep_rows)
    out = Path(args.out) if args.out else settings.reports_dir / f"eval_{date.today().strftime('%Y%m%d')}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"\n-> {out}")
    session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
