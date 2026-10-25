"""정답 데이터셋(GOLD_FILE — mock 은 fixtures/gold, record/live 는 gold/events.yaml) 대비 정확도 측정 -> reports/eval_<일시>.md

  python scripts/evaluate.py
  python scripts/evaluate.py --sweep link_auto=0.45,0.50,0.55,0.60      # DB 사본에서 실행 (운영 DB 를 바꾸지 않는다)
  python scripts/evaluate.py --sweep completion_min_conf=0.6,0.7,0.8

실험 라벨(gold/cluster_sample.csv·experiment_labels.csv) 기준 — record 모드 실험 DB 용 (app/experiment/label_eval.py)
  python scripts/evaluate.py --labels [--before-db data/_backup/x.db]      # 현재 DB(와 이전 DB)의 군집 정밀도·후속보도 P/R
  python scripts/evaluate.py --label-sweep "weight_min=0,0.15 weight_alpha=0,0.3 link_auto=0.55,0.6"   # DB 사본에서 가중치 재계산 후 설정 조합 표

정답셋 형식 (gold/events.yaml)
  as_of: 2026-09-11                      # 판정 기준일 (없으면 settings.today(), 다르면 경고)
  events:
    - key: ...                           # 필수
      expected_status: ...               # 선택 — 없으면 상태 지표 분모에서 제외
      linked_news_ids: ["0110...", ...]  # 선택 — 따옴표 필수(숫자로 읽히면 오류)
      promises: [{actor_org, action, deadline_date, strength}]   # 선택
      followup_news_ids: [...]           # 선택

지표
  사건 연결   : precision / recall / F1 — 자동 연결(rule/embedding, 검토 대기 제외)만. 검토 큐 건수·정답 비율 별도
  약속 추출   : 정답 약속과 DB 약속을 1:1 매칭(action 유사도 ≥0.8, 탐욕적). actor/deadline(월)/strength 일치율, DB 약속 기준 precision
  상태 판정   : 5-class accuracy + confusion (완료 false positive 별도). 판정 이력 없는 사건은 '(미판정)' 으로 오답 처리
  후속 탐색   : gold followup_news_ids 중 현재 prompt_version 신호(활성 연결)가 있는 비율
DB 에 없는 정답 사건은 모든 분모에 들어가고(누락), gold 에 없는 DB 사건은 경고로 보고한다.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bigkinds import build_client  # noqa: E402
from app.config import BACKEND_DIR, DISCLAIMER, STATUS_DONE, STATUSES, get_settings  # noqa: E402
from app.db.models import Event, StatusJudgment  # noqa: E402
from app.db.session import db_path, init_db, reset_engine, session_factory  # noqa: E402
from app.llm.client import PROMPT_VERSIONS, build_llm  # noqa: E402
from app.pipeline.embeddings import build_embedding  # noqa: E402
from app.pipeline.runner import Pipeline  # noqa: E402

UNJUDGED = "(미판정)"
MISSING = "(없음)"


class GoldError(ValueError):
    pass


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return round(p, 3), round(r, 3), round(f, 3)


def load_gold(path: Path) -> dict:
    """스키마 검증·정규화: news_id 는 문자열만, deadline_date 는 ISO 문자열로, 상태는 5개 중 하나."""
    with open(path, encoding="utf-8") as f:
        gold = yaml.safe_load(f) or {}
    events = gold.get("events") or []
    if not events:
        raise GoldError(f"정답셋이 비어 있습니다: {path}")
    keys: set[str] = set()
    for g in events:
        key = g.get("key")
        if not key:
            raise GoldError("key 가 없는 정답 사건이 있다")
        if key in keys:
            raise GoldError(f"정답 key 중복: {key}")
        keys.add(key)
        for field in ("linked_news_ids", "followup_news_ids"):
            ids = g.get(field)
            if ids is None:
                continue
            for i, nid in enumerate(ids):
                if not isinstance(nid, str):
                    raise GoldError(f"{key}.{field}[{i}] 가 문자열이 아니다({nid!r}) — YAML 에서 따옴표로 감싸야 한다")
        st = g.get("expected_status")
        if st is not None and st not in STATUSES:
            raise GoldError(f"{key}.expected_status 가 5개 상태가 아니다: {st!r}")
        for p in g.get("promises") or []:
            dd = p.get("deadline_date")
            if isinstance(dd, (date, datetime)):
                p["deadline_date"] = dd.isoformat()[:10]
            elif dd is not None and not isinstance(dd, str):
                raise GoldError(f"{key} promise deadline_date 타입 오류: {dd!r}")
    as_of = gold.get("as_of") or gold.get("today")
    if isinstance(as_of, (date, datetime)):
        as_of = as_of.isoformat()[:10]
    gold["as_of"] = as_of
    return gold


def _match_promises(gold_ps: list[dict], db_ps: list, embedder) -> list[tuple[dict, object | None, float]]:
    """정답 약속 ↔ DB 약속 1:1 탐욕 매칭 (유사도 내림차순)."""
    if not gold_ps:
        return []
    if not db_ps:
        return [(gp, None, 0.0) for gp in gold_ps]
    g_texts = [gp.get("action") or "" for gp in gold_ps]
    d_texts = [p.action or "" for p in db_ps]
    vecs = embedder.encode([*g_texts, *d_texts])
    sim = vecs[: len(gold_ps)] @ vecs[len(gold_ps) :].T
    for i, gt in enumerate(g_texts):
        for j, dt in enumerate(d_texts):
            if gt and gt == dt:
                sim[i, j] = 1.0
    pairs = sorted(((float(sim[i, j]), i, j) for i in range(len(gold_ps)) for j in range(len(db_ps))), reverse=True)
    used_g: set[int] = set()
    used_d: set[int] = set()
    out: dict[int, tuple[object | None, float]] = {}
    for s, i, j in pairs:
        if i in used_g or j in used_d or s < 0.8:
            continue
        used_g.add(i)
        used_d.add(j)
        out[i] = (db_ps[j], s)
    return [(gp, *(out.get(i, (None, 0.0)))) for i, gp in enumerate(gold_ps)]


def evaluate(session, gold: dict, embedder) -> dict:
    events = {e.key: e for e in session.query(Event).all()}
    judged_ids = {row[0] for row in session.query(StatusJudgment.event_id).distinct().all()}
    version = PROMPT_VERSIONS["followup"]

    link_tp = link_fp = link_fn = 0
    review_total = review_correct = 0
    promise_total = promise_found = actor_ok = deadline_ok = strength_ok = 0
    db_promise_total = db_promise_matched = 0
    status_total = status_ok = 0
    confusion: Counter = Counter()
    followup_hit = followup_total = 0
    rows = []
    missing_keys = []

    for g in gold["events"]:
        ev = events.get(g["key"])
        truth = set(g.get("linked_news_ids") or [])
        gold_ps = g.get("promises") or []
        expected = g.get("expected_status")
        followups = g.get("followup_news_ids") or []
        if ev is None:
            missing_keys.append(g["key"])
            link_fn += len(truth)
            promise_total += len(gold_ps)
            followup_total += len(followups)
            if expected:
                status_total += 1
                confusion[(expected, MISSING)] += 1
            rows.append({"key": g["key"], "title": g.get("title", g["key"]), "expected": expected or "-", "status": MISSING, "reason": "", "ok": False, "link": f"0/{len(truth)}", "promises": f"0/{len(gold_ps)}", "priority": ""})
            continue

        # 연결 — 파이프라인의 마지막 결정(auto_decision)만 측정. 관리자가 해제한 오연결도 fp 로, 관리자 확정도 tp 로 세지 않는다
        if any(l.auto_decision for l in ev.links):
            pred = {l.news_id for l in ev.links if l.auto_decision == "link"}
            review = {l.news_id for l in ev.links if l.auto_decision == "review"}
        else:
            # link 단계를 아직 새 코드로 돌리지 않은 DB: 옛 기준(활성 자동 연결)으로 대체
            pred = {l.news_id for l in ev.links if l.link_method in ("rule", "embedding") and l.is_active}
            review = {l.news_id for l in ev.links if l.link_method in ("rule", "embedding") and l.needs_review and not l.rejected}
        if g.get("linked_news_ids") is not None:
            tp, fp, fn = len(pred & truth), len(pred - truth), len(truth - pred)
            link_tp, link_fp, link_fn = link_tp + tp, link_fp + fp, link_fn + fn
            review_total += len(review)
            review_correct += len(review & truth)
        else:
            tp, fp = 0, 0

        # 약속 — 1:1 매칭
        db_ps = [p for p in ev.promises if p.is_trackable]
        db_promise_total += len(db_ps)
        for gp, best, _ in _match_promises(gold_ps, db_ps, embedder):
            promise_total += 1
            if best is None:
                continue
            promise_found += 1
            db_promise_matched += 1
            actor_ok += int(best.actor_org == gp.get("actor_org"))
            strength_ok += int(best.strength == gp.get("strength"))
            gd = gp.get("deadline_date")
            bd = best.deadline_date.isoformat() if best.deadline_date else None
            deadline_ok += int((gd or "")[:7] == (bd or "")[:7])

        # 상태
        got = ev.status if ev.id in judged_ids else UNJUDGED
        ok = expected is not None and got == expected
        if expected is not None:
            status_total += 1
            status_ok += int(ok)
            confusion[(expected, got)] += 1

        # 후속 탐색 — 현재 버전 신호 + 활성 연결
        active = {l.news_id for l in ev.links if l.is_active}
        sig_ids = {s.news_id for s in ev.signals if s.prompt_version == version and s.news_id in active}
        for nid in followups:
            followup_total += 1
            followup_hit += int(nid in sig_ids)

        rows.append(
            {
                "key": g["key"],
                "title": g.get("title") or ev.title,
                "expected": expected or "-",
                "status": got,
                "reason": ev.status_reason if ev.id in judged_ids else "",
                "ok": ok,
                "link": f"{tp}/{len(truth)} (+{fp} 오연결, 검토 {len(review)})" if g.get("linked_news_ids") is not None else "N/A",
                "promises": f"{len(db_ps)}/{len(gold_ps)}",
                "priority": ev.priority_score,
            }
        )

    extra_db = sorted(set(events) - {g["key"] for g in gold["events"]})
    p, r, f = prf(link_tp, link_fp, link_fn)
    done_fp = sum(v for (exp, got), v in confusion.items() if got == STATUS_DONE and exp != STATUS_DONE)

    def ratio(a: int, b: int) -> float | str:
        return round(a / b, 3) if b else "N/A(라벨 0건)"

    return {
        "link": {"precision": p, "recall": r, "f1": f, "tp": link_tp, "fp": link_fp, "fn": link_fn, "review_total": review_total, "review_correct": review_correct},
        "promise": {
            "total": promise_total,
            "found": promise_found,
            "action_match": ratio(promise_found, promise_total),
            "actor_match": ratio(actor_ok, promise_total),
            "deadline_month_match": ratio(deadline_ok, promise_total),
            "strength_match": ratio(strength_ok, promise_total),
            "db_total": db_promise_total,
            "db_precision": ratio(db_promise_matched, db_promise_total),
        },
        "status": {"accuracy": ratio(status_ok, status_total), "n": status_total, "done_false_positive": done_fp},
        "followup": {"recall": ratio(followup_hit, followup_total), "hit": followup_hit, "total": followup_total},
        "confusion": {f"{k[0]} -> {k[1]}": v for k, v in sorted(confusion.items())},
        "rows": rows,
        "missing_keys": missing_keys,
        "extra_db_keys": extra_db,
    }


def render(result: dict, meta: dict, sweep_rows: list[dict] | None = None) -> str:
    L = []
    L.append(f"# 평가 결과 — {meta['date']}")
    L.append("")
    L.append(f"- 모드: bigkinds=`{meta['bigkinds_mode']}` llm=`{meta['llm_mode']}` embedding=`{meta['embedding']}`")
    L.append(f"- 판정 기준일: `{meta['judge_today']}` (gold as_of `{meta['gold_as_of']}`, TODAY_OVERRIDE `{meta['today_override']}`)")
    L.append(f"- 임계값: {json.dumps(meta['thresholds'], ensure_ascii=False)}")
    L.append(f"- 정답셋: `{meta['gold']}` ({len(result['rows'])}건)")
    if meta["llm_mode"] == "stub":
        L.append("- ⚠ llm=stub: 약속·후속 신호 지표는 정답 재생이라 성능 지표가 아니다 (파이프라인 배선 검증용)")
    if meta.get("date_warning"):
        L.append(f"- ⚠ {meta['date_warning']}")
    if result["missing_keys"]:
        L.append(f"- ⚠ DB 에 없는 정답 사건 {len(result['missing_keys'])}건: {', '.join(result['missing_keys'])}")
    if result["extra_db_keys"]:
        L.append(f"- ⚠ 정답셋에 없는 DB 사건 {len(result['extra_db_keys'])}건: {', '.join(result['extra_db_keys'])}")
    L.append("")
    L.append("## 요약")
    L.append("")
    L.append("| 지표 | 값 |")
    L.append("|---|---|")
    lk = result["link"]
    L.append(f"| 사건 연결 P / R / F1 (자동 연결) | {lk['precision']} / {lk['recall']} / {lk['f1']} (tp {lk['tp']}, fp {lk['fp']}, fn {lk['fn']}) |")
    L.append(f"| 검토 큐 | {lk['review_total']}건 (정답 {lk['review_correct']}건) |")
    pr = result["promise"]
    L.append(f"| 약속 탐지(1:1, action 유사도≥0.8) | {pr['found']}/{pr['total']} = {pr['action_match']} |")
    L.append(f"| 약속 주체 정확 일치 | {pr['actor_match']} |")
    L.append(f"| 약속 기한 월 단위 일치 | {pr['deadline_month_match']} |")
    L.append(f"| 발언 강도 일치 | {pr['strength_match']} |")
    L.append(f"| DB 약속 기준 precision | {pr['db_precision']} ({pr['db_total']}건) |")
    st = result["status"]
    L.append(f"| 상태 판정 accuracy | {st['accuracy']} (n={st['n']}, 완료 false positive {st['done_false_positive']}건) |")
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
        L.append("## 임계값 스윕 (DB 사본에서 실행)")
        L.append("")
        L.append("| 값 | 연결 P / R / F1 | 검토 큐 | 상태 accuracy | 완료 FP |")
        L.append("|---|---|---|---|---|")
        for s in sweep_rows:
            L.append(f"| {s['param']}={s['value']} | {s['p']} / {s['r']} / {s['f1']} | {s['review']} | {s['status_acc']} | {s['done_fp']} |")
    L.append("")
    L.append(f"> 상태는 5개 문자열로만 표시된다. {DISCLAIMER}")
    return "\n".join(L)


def gold_label(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(BACKEND_DIR))
    except ValueError:
        return str(path)


def _sweep(settings, gold: dict, embedder, today: date, param: str, values: list[str]) -> list[dict]:
    """운영 DB 를 임시 파일로 복사해 그 사본에서 link·judge 를 돌린다."""
    src = db_path()
    if src is None or not src.exists():
        raise SystemExit("스윕은 SQLite 파일 DB 에서만 지원한다")
    tmp = src.with_name(f"{src.stem}.sweep.db")
    # WAL 에 남은 최근 쓰기(신고·관리자 결정)까지 담도록 sqlite backup API 로 복사한다 (파일 복사는 WAL 을 빠뜨린다)
    with sqlite3.connect(src) as src_conn, sqlite3.connect(tmp) as dst_conn:
        src_conn.backup(dst_conn)
    reset_engine()
    init_db(f"sqlite:///{tmp}")
    session = session_factory()()
    original = getattr(settings, param)
    rows = []
    try:
        pipe = Pipeline(session, settings, build_client(settings), build_llm(settings), embedder, today=today)
        for v in values:
            setattr(settings, param, type(original)(float(v)) if isinstance(original, (int, float)) else v)
            pipe.run("link")
            pipe.run("judge")
            res = evaluate(session, gold, embedder)
            rows.append(
                {
                    "param": param,
                    "value": v,
                    "p": res["link"]["precision"],
                    "r": res["link"]["recall"],
                    "f1": res["link"]["f1"],
                    "review": res["link"]["review_total"],
                    "status_acc": res["status"]["accuracy"],
                    "done_fp": res["status"]["done_false_positive"],
                }
            )
    finally:
        setattr(settings, param, original)
        session.close()
        reset_engine()
        for p in (tmp, tmp.with_name(tmp.name + "-wal"), tmp.with_name(tmp.name + "-shm")):
            if p.exists():
                p.unlink()
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", default=None, help="예: link_auto=0.45,0.50,0.55 (DB 사본에서 실행)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--labels", action="store_true", help="실험 라벨 CSV 기준 지표 (현재 DB)")
    ap.add_argument("--before-db", default=None, help="--labels 와 함께: 비교할 이전 DB 파일 (사본에서 읽는다)")
    ap.add_argument("--label-sweep", default=None, help='예: "weight_min=0,0.15 weight_alpha=0,0.3 link_auto=0.55,0.6"')
    ap.add_argument("--no-followup", action="store_true", help="--label-sweep 에서 후속보도 재분류 생략 (군집만)")
    args = ap.parse_args()

    settings = get_settings()
    if args.labels or args.label_sweep:
        return label_main(settings, args)
    try:
        gold = load_gold(settings.gold_file)
    except (GoldError, FileNotFoundError) as exc:
        print(f"정답셋 오류: {exc} (BIGKINDS_MODE={settings.bigkinds_mode})")
        return 1
    embedder = build_embedding(settings)

    today = settings.today()
    date_warning = None
    if gold.get("as_of"):
        as_of = date.fromisoformat(gold["as_of"])
        if as_of != today:
            date_warning = f"gold as_of({as_of}) 와 판정 기준일({today}) 이 다르다 — R4(60일·기한 경과) 경계 사건의 결과가 달라질 수 있다. 같은 기준일로 다시 판정하려면 TODAY_OVERRIDE={as_of} 로 run_pipeline --stage judge"

    sweep_rows = None
    if args.sweep:
        param, values = args.sweep.split("=")
        sweep_rows = _sweep(settings, gold, embedder, today, param, values.split(","))

    init_db()
    session = session_factory()()
    result = evaluate(session, gold, embedder)
    session.close()
    meta = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "bigkinds_mode": settings.bigkinds_mode,
        "llm_mode": settings.llm_mode,
        "embedding": embedder.name,
        "judge_today": today.isoformat(),
        "gold_as_of": gold.get("as_of") or "-",
        "today_override": settings.today_override.isoformat() if settings.today_override else "-",
        "date_warning": date_warning,
        "thresholds": {
            "link_auto": settings.link_auto,
            "link_review": settings.link_review,
            "signal_min_conf": settings.signal_min_conf,
            "completion_min_conf": settings.completion_min_conf,
            "stale_followup_days": settings.stale_followup_days,
        },
        "gold": gold_label(settings.gold_file),
    }
    md = render(result, meta, sweep_rows)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    suffix = f"_sweep_{args.sweep.split('=')[0]}" if args.sweep else ""
    out = Path(args.out) if args.out else settings.reports_dir / f"eval_{stamp}{suffix}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"\n-> {out}")
    return 0


def _copy_db(src: Path, name: str) -> Path:
    """sqlite backup API 로 사본을 만든다 (WAL 까지). 사본은 간이 마이그레이션(term_weights 등 새 컬럼)을 받는다."""
    tmp = BACKEND_DIR / "data" / f"{src.stem}.{name}.db"
    for p in (tmp, tmp.with_name(tmp.name + "-wal"), tmp.with_name(tmp.name + "-shm")):
        if p.exists():
            p.unlink()
    with sqlite3.connect(src) as src_conn, sqlite3.connect(tmp) as dst_conn:
        src_conn.backup(dst_conn)
    return tmp


def _drop_db(tmp: Path) -> None:
    for p in (tmp, tmp.with_name(tmp.name + "-wal"), tmp.with_name(tmp.name + "-shm")):
        if p.exists():
            p.unlink()


def label_main(settings, args) -> int:
    from app.experiment import label_eval as LE
    from app.pipeline.link import refresh_term_weights

    gold_dir = BACKEND_DIR / "gold"
    src = db_path()
    if src is None or not src.exists():
        raise SystemExit("라벨 평가는 SQLite 파일 DB 에서만 지원한다")
    lines = [f"# 라벨 기준 평가 ({datetime.now().strftime('%Y-%m-%d %H:%M')})", "", f"- DB: `{src.name}` · 라벨: gold/cluster_sample.csv · gold/experiment_labels.csv", ""]
    snap_cols = ["db", "cluster_kept", "cluster_precision", "o_retained", "x_removed", "full_kept", "full_precision", "full_recall", "system_positive", "tp", "fp", "fn", "followup_precision", "followup_recall", "followup_f1"]
    if args.labels:
        rows = []
        targets = ([("before", Path(args.before_db))] if args.before_db else []) + [("current", src)]
        for name, path in targets:
            tmp = _copy_db(path if path.is_absolute() else BACKEND_DIR / path, f"labels_{name}")
            try:
                reset_engine()
                init_db(f"sqlite:///{tmp}")
                session = session_factory()()
                rows.append({"db": f"{name} ({path.name})", **LE.snapshot(session, settings, gold_dir)})
                session.close()
            finally:
                reset_engine()
                _drop_db(tmp)
        lines += ["## 현재 상태", "", LE.render_table(rows, snap_cols)]
    if args.label_sweep:
        grid: dict[str, list] = {}
        for part in args.label_sweep.split():
            k, v = part.split("=")
            typ = type(getattr(settings, k))
            grid[k] = [typ(float(x)) if typ is int else typ(x) for x in v.split(",")]
        tmp = _copy_db(src, "labelsweep")
        try:
            reset_engine()
            init_db(f"sqlite:///{tmp}")
            session = session_factory()()
            embedder = build_embedding(settings)
            all_events = session.query(Event).filter(Event.active.is_(True)).all()
            refresh_term_weights(session, all_events)  # 사본에서 — 운영 DB 불변
            from app.llm.rules_adapter import RulesLLM

            rows = LE.sweep(session, settings, embedder, RulesLLM(settings, embedder), gold_dir, grid, with_followup=not args.no_followup)
            session.close()
        finally:
            reset_engine()
            _drop_db(tmp)
        cols = [*grid, "auto_links", "cluster_kept", "cluster_precision", "o_retained", "x_removed"]
        if (gold_dir / "cluster_full.csv").exists():
            cols += ["full_kept", "full_precision", "full_recall", "full_f1"]
        if not args.no_followup:
            cols += ["system_positive", "followup_precision", "followup_recall", "followup_f1"]
        lines += ["## 스윕 (DB 사본, multi-event·약속 재추출 없음)", "", LE.render_table(rows, cols)]
    md = "\n".join(lines)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out = Path(args.out) if args.out else settings.reports_dir / f"eval_labels_{stamp}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"\n-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
